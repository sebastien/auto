"""Module: materialize — Freeze task templates and retain reference origins."""

from __future__ import annotations

import re
from copy import deepcopy

from auto.features.task.model import Task, createTaskFromText, taskText
from auto.features.plan.model import createPlan
from auto.features.plan.policy import shellPermitted
from auto.features.task.operations import TaskGet
from auto.runtime.config import validateConfig
from auto.features.elaboration.parameters import resolveParameters
from auto.features.elaboration.metadata import MetadataProjection
from auto.features.elaboration.templates import ParameterTemplate, Unknown
from auto.features.elaboration.sources import TemplateSources
from auto.runtime.operations import Resolve, Value, isReference
from auto.features.script.inheritance import inheritTemplate
from auto.features.script.model import Script
from auto.utils.data import AutoError, document, documentText, merge, sections
from auto.utils.references import relocate, relocateMeta
from auto.utils.config import RUNTIME_KEYS
from auto.utils.layout import taskFile

# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------


TASK_SECTIONS = {"Pre", "Process", "Post", "Evaluation"}


def demote(text: str) -> str:
	"""Nest the headings of a script body one level down, outside fenced code."""
	lines, fence = [], None
	for line in text.splitlines(keepends=True):
		marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
		if marker:
			fence = marker[1] if fence is None else None if marker[1][0] == fence[0] and len(marker[1]) >= len(fence) else fence
		lines.append("#" + line if fence is None and not marker and re.match(r"^#{1,5} ", line) else line)
	return "".join(lines)


def taskTemplate(context, meta: dict, body: str, path) -> tuple[dict, str]:
	"""Give a script-style template (an action) the shape of a task template.

	A template inheriting another with `from`, or a script whose body is not
	in task sections, is composed as `auto run` would compose it; its content
	becomes the Process."""
	if not isinstance(meta.get("from"), str):
		# Task templates may choose their sections with Liquid: only a static
		# body can be told apart from a task by its headings.
		if "{%" in body or not body.strip():
			return meta, body
		try:
			_, parts = sections(body)
		except AutoError:
			return meta, body
		if any("{{" in name for name in parts):
			return meta, body
		if "Process" in parts or parts and set(parts) <= TASK_SECTIONS:
			return meta, body
	composed = inheritTemplate(context, Script(body, path, meta))
	# Sources resolve from the task template's folder: pin relative ones to their origin.
	text = relocate(composed.content, composed.path.parent)
	# A script body's own sections nest under the Process.
	return composed.meta, "## Process\n\n" + (demote(text) if sections(text)[1] else text)


def materialize(context, value: dict, parent: str, *, dry: bool = False, deferred=None) -> Task:
	"""Instantiate a task from its template and elaborated fields."""
	definition = dict(value)
	deferred = set() if deferred is None else deferred
	generated = {"_template_sources", "_template_bindings", "_template_scope"} & definition.keys()
	if generated:
		raise AutoError("Elaborated tasks cannot supply generated template metadata: " + ", ".join(sorted(generated)))
	meta, parts, description = {}, {}, ""
	deferredProfile, outputProjection = False, None
	if definition.get("from"):
		ref = definition["from"]
		expected = ref.get("digest") if isinstance(ref, dict) else None
		ref = ref["ref"] if isinstance(ref, dict) else ref
		content = Resolve(context, ref)
		if expected is not None and content.digest != expected:
			raise AutoError("Task template changed since its pinned revision")
		meta, body = document(content.text)
		meta, body = taskTemplate(context, meta, body, content.path)
		validateConfig({key: item for key, item in meta.items() if key in {"pre", "post", "transform", "apply", "eval"}})
		if not isinstance(meta.get("params", {}), dict) or not isinstance(definition.get("params", {}), dict):
			raise AutoError("Template parameters must be mappings")
		params = merge(relocateMeta({"params": meta.get("params", {})}, content.path.parent)["params"],
			relocateMeta({"params": definition.get("params", {})}, context.project)["params"])
		live = context.derive(defaults=context.policyDefaults) if context.policyDefaults is not None else context
		allowed = context.allowShell and (dry or shellPermitted(live, TaskGet(live, parent)))
		parameterContext = context.derive(allowShell=allowed and not dry,
			variables={key: value for key, value in context.variables.items() if key != "_policyDeniedAction"})
		resolved, defaults = resolveParameters(parameterContext, content.path, meta, params,
			lambda ctx, value, config: Value(ctx, value, context.project, config), dry=dry,
			inherited=relocateMeta({key: value for key, value in definition.items() if key in RUNTIME_KEYS}, context.project))
		sources = TemplateSources(context, meta.get("_template_sources"), task=definition["id"], parameterContext=parameterContext,
			inherited=defaults, dry=dry)
		sectionDefaults = {}
		compiler = ParameterTemplate({**resolved, **({"__template_values": meta["_template_bindings"]} if meta.get("_template_bindings") else {})}, context.maxBytes,
			onFile=lambda ref, namespace, inline, conditional, section: sources.transclude(ref, content.path.parent, namespace,
				inline=inline, conditional=conditional, inherited=sectionDefaults.setdefault(section, merge(sources.inherited))))
		compiled = {}
		for key, item in meta.items():
			if key in {"params", "_template_bindings", "_template_sources"}:
				continue
			if key in {"pre", "post", "apply", "eval"}:
				compiled[key] = compiler.expand(item, mode="shell") if isinstance(item, str) else [compiler.expand(command, mode="shell") for command in item]
			else:
				slots = True if key == "inputs" else {"schema", "template"} if key == "output" else None
				value = compiler.value(item, slots=slots, partial=dry, deferred=deferred, path=(key,))
				if not isinstance(value, Unknown):
					compiled[key] = value
				elif key == "title":
					# Presence is known, but truth/type checks require the resolved title.
					compiled[key] = item
				elif key == "profile" and "profile" not in definition:
					deferredProfile = True
		# Companions inherit the winning contract as well as runtime settings.
		# Relocate before passing it down so a nested file cannot rebase it.
		sources.inherited = sources.inheritedDefaults(compiled, content.path.parent,
			relocateMeta({key: item for key, item in definition.items()
				if key in {*RUNTIME_KEYS, "inputs", "output"}}, context.project), defaults, deferred)
		outputProjection = sources.inherited.get("output", {})
		selection = merge(relocateMeta({key: item for key, item in compiled.items() if key == "artifact"}, content.path.parent),
			relocateMeta({key: item for key, item in definition.items() if key == "artifact"}, context.project))
		contract = sources.framework(merge(selection, sources.inherited), content.path.parent, resolved,
			compileChecks=True, contractOnly=True)
		sources.inherited = merge(contract, sources.inherited)
		if dry and ("artifact",) in deferred and "artifact" not in definition:
			# The selected artifact may supply any missing contract field.
			sources.inherited["output"] = Unknown()
		body = compiler.expand(body, partial=dry)
		if sources.records:
			compiled["_template_sources"] = sources.records
		definition["params"] = deepcopy({name: params[name] if isinstance(item, Unknown) else "\\" + item if isinstance(item, str) else item for name, item in resolved.items()})
		compiled["params"] = definition["params"]
		if compiler.bindings or meta.get("_template_bindings"):
			compiled["_template_bindings"] = merge(meta.get("_template_bindings", {}), compiler.bindings)
		template = createTaskFromText(documentText(compiled, body), content.path)
		if dry and compiler.possibleProcess:
			# A Process supplied by an unresolved branch is checked after Values
			# resolve; keep the known sections available for independent checks.
			template.sections.setdefault("Process", "")
		meta = relocateMeta(template.meta, content.path.parent)
		parts = {name: relocate(body, content.path.parent) for name, body in template.sections.items()}
		description = template.description
		definition["from"] = {"ref": ref, "digest": content.digest}
	merged = merge(meta, relocateMeta({key: item for key, item in definition.items() if key not in {"sections", "description"}}, context.project))
	if not deferredProfile and (merged.get("profile") is None or merged.get("profile") == ""):
		profile = createPlan(context.plan).meta.get("profile")
		if profile:
			merged["profile"] = relocateMeta({"profile": profile}, context.plan)["profile"]
	merged["id"], merged["parent"] = definition["id"], parent
	# A template's catalogue metadata is its own: `from` identifies it.
	for key in ("kind", "identity") if definition.get("from") else ():
		merged.pop(key, None)
	if not merged.get("params"):
		merged.pop("params", None)
	sources = TemplateSources(context, merged.get("_template_sources"), task=definition["id"],
		parameterContext=parameterContext if definition.get("from") else None, inherited=merged, dry=dry)
	if definition.get("from"):
		params = {**resolved, **({"__template_values": merged["_template_bindings"]} if merged.get("_template_bindings") else {})}
	else:
		params = {name: item[1:] if isinstance(item, str) and item.startswith("\\") else
			Unknown() if isinstance(item, str) and (item.startswith("!") or isReference(item)) else item
			for name, item in merged.get("params", {}).items()}
	sources.framework(merged, taskFile(context.plan, definition["id"]).parent, params, compileChecks=bool(definition.get("from")),
		outputProjection=outputProjection if dry else None)
	if sources.records:
		merged["_template_sources"] = sources.records
	task = Task(definition["id"], merged, {**parts, **{name: relocate(body, context.project) for name, body in definition.get("sections", {}).items()}},
		taskFile(context.plan, definition["id"]), definition.get("description", description))
	if len(taskText(task).encode("utf-8")) > context.maxBytes:
		raise AutoError("Materialised task exceeds byte limit")
	if dry:
		task.projectDefaults = MetadataProjection(deferred, definition).defaults
	return task

# EOF
