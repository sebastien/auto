"""Module: operations — Ephemeral script expansion, discovery and execution."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from liquid.exceptions import LiquidError

from auto.features.script.config import commands, composedMeta
from auto.features.script.inheritance import inheritTemplate
from auto.features.script.model import Script, createScript
from auto.features.script.templates import Renderer
from auto.features.usage.operations import UsageRecord
from auto.runtime.config import READ_ONLY_ENGINES, RUNTIME_DEFAULTS, validateConfig
from auto.runtime.model import DETAIL, Context, Event, FailureKind, Limits, RunStatus, failureFor, statusFor
from auto.runtime.operations import Engine, Resolve, Select, Shell, Value, isReference
from auto.utils.config import RUNTIME_KEYS
from auto.utils.data import AutoError, document, documentText, jsonText, merge, promptText, timestamp


# -----------------------------------------------------------------------------
#
# UTILITIES
#
# -----------------------------------------------------------------------------


def loadScript(context: Context, script: Script | str | dict) -> Script:
	"""Load a script from a model, a mapping or a reference, composed with its template."""
	if isinstance(script, Script):
		return inheritTemplate(context, script)
	if isinstance(script, dict):
		path = Path(script.get("path", context.project / "script.md"))
		loaded = createScript(script["content"], path, script.get("meta")) if "meta" in script else createScript(script["content"], path)
	else:
		content = Resolve(context, script if isReference(script) else "./" + script)
		loaded = createScript(content.text, content.path, ref=content.ref)
	return inheritTemplate(context, loaded)


def contractText(output: dict) -> str:
	"""Render an output contract for a model: its format, then its template and schema verbatim."""
	from auto.features.script.templates import fence
	parts = [f"Format: {output['format']}"]
	if "template" in output:
		template = output["template"] if isinstance(output["template"], str) else jsonText(output["template"])
		# A template given as one fenced block is shown as is; others are fenced.
		fenced = re.fullmatch(r"(`{3,}).*\n[\s\S]*\n\1\s*", template)
		parts.append("Template:\n\n" + (template.strip() + "\n" if fenced else fence(template, output["format"])))
	if "schema" in output:
		parts.append("Schema:\n\n" + fence(jsonText(output["schema"]), "json"))
	others = {key: item for key, item in output.items() if key not in {"format", "template", "schema"}}
	if others:
		parts.append(promptText(others))
	return "\n\n".join(part.rstrip("\n") for part in parts) + "\n"


def prepare(context: Context, script, renderer: Renderer | None = None, *, role: str | None = None) -> dict:
	"""Render a script and its output contract for one engine request."""
	loaded = loadScript(context, script)
	try:
		renderer = renderer or Renderer(context, loaded, role=role)
		prompt = renderer.render()
		output = dict(renderer.config.get("output", {}))
		output.setdefault("format", "markdown")
		if output["format"] not in {"markdown", "text", "json"}:
			raise AutoError("Unsupported output format")
		for name in ("schema", "template"):
			if name in output:
				output[name] = renderer.sources.value(output[name], loaded.path.parent, renderer.config)
		if isinstance(output.get("schema"), str):
			output["schema"] = json.loads(output["schema"])
		if "schema" in output:
			Draft202012Validator.check_schema(output["schema"])
		if output:
			prompt += "\n\n## Output contract\n\n" + contractText(output)
		return {"prompt": prompt, "config": {key: renderer.config[key] for key in RUNTIME_KEYS if key in renderer.config}, "output": output}
	except (LiquidError, ValueError, SchemaError) as error:
		raise AutoError(f"{loaded.path}: {error}") from error


def conform(output, contract: dict):
	"""Validate an output against its format and schema contract."""
	try:
		if contract.get("format") == "json" and isinstance(output, str):
			def invalidConstant(value):
				raise ValueError(f"Invalid JSON constant: {value}")
			def uniqueKeys(pairs):
				result = {}
				for key, value in pairs:
					if key in result:
						raise ValueError("Duplicate JSON key")
					result[key] = value
				return result
			output = json.loads(output, parse_constant=invalidConstant, object_pairs_hook=uniqueKeys)
			jsonText(output)
		elif contract.get("format", "markdown") in {"text", "markdown"} and not isinstance(output, str):
			raise AutoError("Expected textual output", "output")
		if "schema" in contract:
			Draft202012Validator(contract["schema"]).validate(output)
		return output
	except (ValueError, ValidationError) as error:
		raise AutoError(f"Output does not conform: {error}", "output") from error


def ended(kind, reason, status=None) -> dict:
	"""Build the terminal `ScriptEnded` event for a failure."""
	return {"type": Event.SCRIPT_ENDED, "status": status or statusFor(kind), "output": None, "failure": {"kind": failureFor(kind), "reason": reason}}


COMPLETE_USAGE_ENGINES = frozenset({"opencode", "pi"})


def executePrepared(context: Context, request: dict, journal: dict | None = None, *, transform: bool = False):
	"""Drive one engine stream; answers are sent back through the generator."""
	config = request["config"]
	name = config.get("engine", RUNTIME_DEFAULTS["engine"])
	if context.observe:
		context.observe({"type": Event.EXECUTION_STARTED, "config": {**config, "engine": name}, "output": request["output"], "transform": transform,
			**{key: (journal or {}).get(key) for key in ("task", "kind", "check") if (journal or {}).get(key)}})
	entry = {"ts": timestamp(), "project": context.project.name, "plan": context.plan.name if context.plan else None,
		"kind": "script", "run": None, "task": None, **(journal or {}),
		"engine": name, "provider": config.get("provider"), "model": config.get("model"), "status": RunStatus.CANCELLED, "failure": None}
	before, reported, started = dict(context.limits.usage) if context.limits else None, False, time.monotonic()
	if config.get("access") == "read" and name not in READ_ONLY_ENGINES:
		yield {"type": Event.SCRIPT_META, "name": "warning", "value": f"The {name} engine cannot enforce access: read; the run may change files"}
	stream, answer = _executePrepared(context, request, name), None
	try:
		while True:
			try:
				event = stream.send(answer)
			except StopIteration:
				return
			if event["type"] == Event.SCRIPT_META and event.get("name") == "usage":
				reported = True
			elif event["type"] == Event.SCRIPT_META and event.get("name") == "tokens" and isinstance(event.get("value"), dict):
				# Cumulative for this stream: the last report is the run's breakdown.
				entry.update({key: event["value"][key] for key in DETAIL if isinstance(event["value"].get(key), int)})
			elif event["type"] == Event.SCRIPT_META and event.get("name") == "runtime" and isinstance(event.get("value"), dict):
				value = event["value"]
				entry.update({key: value[source] for key, source in (("provider", "provider"), ("provider", "resolved_provider"), ("model", "model"))
					if isinstance(value.get(source), str)})
			elif event["type"] == Event.SCRIPT_ENDED:
				entry.update(status=event["status"], failure=event.get("failure"))
			# A stream closed while asking is suspended, not cancelled.
			if event["type"] != Event.SCRIPT_ENDED:
				entry["status"] = RunStatus.WAITING if event["type"] in (Event.SCRIPT_QUESTION, Event.POLICY_REQUEST) else RunStatus.CANCELLED
			answer = yield event
	finally:
		stream.close()
		usage = dict(context.limits.usage) if context.limits else None
		delta = {key: usage.get(key, 0) - before.get(key, 0) for key in ("tokens", "cost")} if usage is not None and reported else {}
		entry.update(duration_s=round(time.monotonic() - started, 3), tokens=delta.get("tokens"),
			cost=round(delta["cost"], 6) if "cost" in delta else None, usage_complete=reported and name in COMPLETE_USAGE_ENGINES)
		try:
			UsageRecord(context, entry)
		except OSError:
			pass  # Accounting must never fail a run.


def _executePrepared(context: Context, request: dict, engineName: str):
	engine = Engine(context, engineName)
	stream, answer = None, None
	try:
		if context.cancelled():
			raise AutoError("Execution cancelled", FailureKind.CANCELLED)
		if context.limits:
			context.limits.check()
			for name in ("tokens", "cost"):
				if name in context.limits.budget and context.limits.usage.get(name, 0) >= context.limits.budget[name]:
					raise AutoError(f"{name} budget exhausted before engine execution", FailureKind.BUDGET)
		stream = engine.start(context, request)
		while True:
			if context.cancelled():
				raise AutoError("Execution cancelled", FailureKind.CANCELLED)
			try:
				event = stream.send(answer)
			except StopIteration:
				raise AutoError("Engine ended without a terminal event", "system") from None
			if context.cancelled():
				raise AutoError("Execution cancelled", FailureKind.CANCELLED)
			if context.limits:
				context.limits.check()
			answer = None
			if event["type"] == Event.SCRIPT_ENDED:
				if event["status"] == RunStatus.SUCCEEDED:
					event["output"] = conform(event.get("output", ""), request["output"])
				yield event
				return
			answer = yield event
	except (AutoError, KeyboardInterrupt) as error:
		kind = error.kind if isinstance(error, AutoError) else FailureKind.CANCELLED
		yield {"type": Event.SCRIPT_ENDED, "status": statusFor(kind), "output": None,
			"failure": {"kind": failureFor(kind), "reason": str(error)}}
	finally:
		if stream is not None:
			stream.close()


def relay(stream):
	"""Forward events and answers, returning the terminal `ScriptEnded` event."""
	answer = None
	try:
		while True:
			event = stream.send(answer)
			if event["type"] == Event.SCRIPT_ENDED:
				return event
			answer = yield event
	finally:
		stream.close()


def runLifecycle(context: Context, script: Script):
	"""Run input checks, inference, output checks, transformations and effects."""
	meta = script.meta
	if not any(meta.get(name) for name in ("pre", "post", "transform", "apply")):
		yield from runScript(context, script)
		return
	try:
		if context.cancelled():
			raise AutoError("Execution cancelled", FailureKind.CANCELLED)
		renderer = Renderer(context, script, role="script")
		namespace = renderer.bindings()
		# Inputs are resolved once, so shell values do not run again for the prompt.
		context = context.derive(resolvedInputs={**context.resolvedInputs, **namespace["inputs"]})
		renderer.context = context
		def shell(step, command, stdin=""):
			command = renderer.shellArgument(command, namespace)
			yield {"type": Event.SCRIPT_ACTION, "name": step, "content": command, "lifecycle": True}
			if context.limits:
				context.limits.check()
			stdout, stderr, code = Shell(context, command, renderer.config, stdin)
			return code, stdout, stderr
		for command in commands(meta.get("pre")):
			code, stdout, stderr = yield from shell("pre", command)
			if code:
				yield ended(FailureKind.INPUT, (stdout + stderr).strip() or "pre command failed", RunStatus.SKIPPED)
				return
		final = yield from relay(runScript(context, script, renderer))
		if final["status"] != RunStatus.SUCCEEDED:
			yield final
			return
		output = final["output"]
		namespace = {**namespace, "output": output}
		jsonOutput = renderer.config.get("output", {}).get("format") == "json"
		text = jsonText(output) if jsonOutput else output
		for step, kind in (("post", FailureKind.OUTPUT), ("transform", FailureKind.OUTPUT), ("apply", FailureKind.SYSTEM)):
			for command in commands(meta.get(step)):
				code, stdout, stderr = yield from shell(step, command, text)
				if code:
					reason = (stderr if meta.get("transform") else stdout + stderr).strip() or f"{step} command failed ({code})"
					yield {**ended(kind, reason), "output": None if meta.get("transform") else output}
					return
				if step == "transform":
					if len(stdout.encode("utf-8")) > context.maxBytes:
						raise AutoError("Transformed output exceeds byte limit", FailureKind.OUTPUT)
					# The schema describes the model response, not the projected result.
					output = conform(stdout, {"format": renderer.config.get("output", {}).get("format", "markdown")})
					namespace = {**namespace, "output": output}
					text = jsonText(output) if jsonOutput else output
		yield {"type": Event.SCRIPT_ENDED, "status": RunStatus.SUCCEEDED, "output": output}
	except (AutoError, KeyboardInterrupt) as error:
		yield ended(error.kind if isinstance(error, AutoError) else FailureKind.CANCELLED, str(error))
	except LiquidError as error:
		yield ended(FailureKind.INPUT, f"{script.path}: {error}")


def runScript(context: Context, script, renderer: Renderer | None = None):
	"""Run the prompt through the engine, streaming its events."""
	stream = None
	try:
		if context.cancelled():
			raise AutoError("Execution cancelled", FailureKind.CANCELLED)
		loaded = loadScript(context, script)
		request = prepare(context, loaded, renderer, role="script")
		stream = executePrepared(context, request, transform=bool(loaded.meta.get("transform")))
		answer = None
		while True:
			event = stream.send(answer)
			answer = yield event
			if event["type"] == Event.SCRIPT_ENDED:
				return
			if event["type"] == Event.SCRIPT_QUESTION and answer is None:
				if context.question:
					answer = context.question(event)
				else:
					raise AutoError("Question requires interactive input", FailureKind.INPUT)
	except (AutoError, KeyboardInterrupt) as error:
		kind = error.kind if isinstance(error, AutoError) else FailureKind.CANCELLED
		yield {"type": Event.SCRIPT_ENDED, "status": statusFor(kind), "output": None, "failure": {"kind": failureFor(kind), "reason": str(error)}}
	finally:
		if stream is not None:
			stream.close()




# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------


def ScriptExpand(context: Context, script) -> str:
	"""Render instructions and output contract without invoking an engine."""
	return prepare(context, script)["prompt"]


def ScriptStart(context: Context, script):
	"""Stream an ephemeral execution, once per `each` item; closing the stream cancels its engine."""
	context = context.derive(limits=context.limits or Limits())
	try:
		if context.cancelled():
			raise AutoError("Execution cancelled", FailureKind.CANCELLED)
		loaded = loadScript(context, script)
		validateConfig(loaded.meta)
		each = loaded.meta.get("each")
		if not each:
			yield from runLifecycle(context, loaded)
			return
		name, value = next(iter(each.items()))
		value = Value(context, value, loaded.path.parent, Renderer(context, loaded, role="script").config)
		items = [line for line in value.splitlines() if line.strip()] if isinstance(value, str) else value
		if not isinstance(items, list):
			raise AutoError("each must give a list or lines of text", FailureKind.INPUT)
	except (AutoError, KeyboardInterrupt) as error:
		yield ended(error.kind if isinstance(error, AutoError) else FailureKind.CANCELLED, str(error))
		return
	except LiquidError as error:
		yield ended(FailureKind.INPUT, str(error))
		return
	results = []
	for index, item in enumerate(items):
		yield {"type": Event.SCRIPT_ITEM_STARTED, "name": name, "value": item, "index": index, "count": len(items)}
		# Items are data: escaping keeps `!` and `PATH` forms from being resolved.
		ctx = context.derive(inputs={**context.inputs, name: "\\" + item if isinstance(item, str) else item})
		final = yield from relay(runLifecycle(ctx, loaded))
		result = {"name": name, "value": item, "status": final["status"], "output": final.get("output")}
		if final.get("failure"):
			inputs = {**loaded.meta.get("inputs", {}), **context.inputs, name: item}
			final = {**final, "failure": {**final["failure"], "reason":
				f"{loaded.ref or loaded.path} · inputs: {jsonText(inputs)}\n{final['failure']['reason']}"}}
			result["failure"] = final["failure"]
		results.append(result)
		yield {"type": Event.SCRIPT_ITEM_ENDED, "index": index, **result}
		if final["status"] in {RunStatus.CANCELLED, RunStatus.TIMED_OUT}:
			yield {**ended(final["failure"]["kind"], final["failure"]["reason"], final["status"]), "output": results}
			return
	failed = [result for result in results if result["status"] not in {RunStatus.SUCCEEDED, RunStatus.SKIPPED}]
	if failed:
		reasons = "\n".join(result["failure"]["reason"] if result.get("failure") else
			f"{loaded.ref or loaded.path} · {result['name']}: {jsonText(result['value'])} · {result['status']}" for result in failed)
		yield {**ended(FailureKind.OUTPUT, f"{len(failed)} of {len(results)} items failed\n{reasons}"), "output": results}
	else:
		yield {"type": Event.SCRIPT_ENDED, "status": RunStatus.SUCCEEDED, "output": results}


def ScriptInputs(context: Context, script, *, definitions=None, expandDefinitions=True, missingOnly=False) -> dict:
	"""Discover declared/implicit inputs and transitive references without shell effects."""
	context = context.derive(allowShell=False, variables={key: value for key, value in context.variables.items() if key != "_policyDeniedAction"})
	names, references, unresolved, visited = set(), set(), [], {}
	def visit(loaded, depth=0, stack=(), inherited=None):
		if depth:
			loaded = Script(loaded.content, loaded.path, composedMeta(loaded.meta, loaded.path), loaded.ref)
		identity = loaded.identity
		if identity in stack:
			raise AutoError(f"Recursive transclusion: {loaded.ref or loaded.path}")
		key = (identity, documentText(merge(loaded.meta, inherited or {}), loaded.content))
		if key in visited:
			return visited[key]
		if depth >= context.maxDepth:
			raise AutoError("Input discovery depth exceeded")
		renderer = Renderer(context, loaded, inherited)
		if not missingOnly:
			names.update(loaded.meta.get("inputs", {}))
			names.update(loaded.meta.get("each", {}) if depth == 0 else {})
		else:
			# Required inputs (null defaults) are missing until given.
			names.update(name for name, value in loaded.meta.get("inputs", {}).items()
				if value is None and name not in context.inputs and name not in (inherited or {}).get("inputs", {}))
		transclusions = list(renderer.transclusions())
		paths = renderer.template.global_variable_paths(include_partials=False)
		for argument, inline, conditional in transclusions:
			paths.extend(renderer.env.from_string(argument).global_variable_paths(include_partials=False))
		for name in paths:
			root = name.removeprefix("inputs.").split(".", 1)[0]
			if missingOnly and (root in renderer.defaults.get("inputs", {}) or root in context.inputs or root in renderer.defaults.get("each", {})):
				continue
			if root not in renderer.scope and (name.startswith("inputs.") or root not in {"inputs", "params", "run", "plan", "forloop"}):
				names.add(root)
		values = []
		for group in ("inputs", "params", "output"):
			current = getattr(context, group) if group != "output" else {}
			if group == "params" and renderer.defaults.get("from"):
				current = {}
			for name, value in loaded.meta.get(group, {}).items():
				if name in (inherited or {}).get(group, {}) or name in current:
					continue
				values.append((value, group != "inputs", loaded.path.parent, None))
			values.extend((value, group != "inputs", context.project, None) for value in current.values())
		values += [(value, True, loaded.path.parent, None if inline else not conditional)
			for value, inline, conditional in transclusions]
		for value, sourceOwned, origin, contributes in values:
			if not isReference(value):
				continue
			if "{{" in value:
				unresolved.append(value)
				continue
			references.add(value)
			# Outputs remain future data. Definition values and inline insertions
			# are data too; full definition transclusions compose as scripts.
			definition = re.fullmatch(r"(?:task:|auto:[^/#]+/task/)[^/#]+/definition(?:#.*)?", value)
			if re.fullmatch(r"(?:task:|auto:[^/#]+/task/)[^/#]+(?:#.*)?", value) or definition and (contributes is None or not expandDefinitions):
				continue
			context.variables.pop("_policyDeniedAction", None)
			try:
				target, _, selector = value.partition("#")
				content = definitions(target) if definition and definitions is not None and not renderer.sources.record(value, origin) else None
				if content is not None:
					if len(content.data) > context.maxBytes:
						raise AutoError("Reference exceeds byte limit: " + value)
					content = Select(content, selector, value)
				else:
					content = renderer.sources.resolve(value, origin) if sourceOwned else Resolve(context, value, origin)
			except AutoError:
				if "_policyDeniedAction" not in context.variables:
					raise
				unresolved.append(value)
				continue
			if contributes is not None and content.path.suffix == ".md":
				meta, body = document(content.text)
				defaults = visit(Script(body, content.path, meta, content.ref), depth + 1, (*stack, identity), renderer.defaults)
				if contributes:
					renderer.defaults = merge({key: value for key, value in defaults.items() if key in {*RUNTIME_KEYS, "output"}}, renderer.defaults)
		visited[key] = renderer.defaults
		return renderer.defaults
	loaded = loadScript(context, script)
	visit(loaded)
	# Known parameter/loop-selected paths can remain as literal binding
	# expressions in the root. Their captured Markdown still contributes inputs.
	renderer = Renderer(context, loaded)
	for ref, record in renderer.sources.records.items():
		if record.get("expand"):
			content = renderer.sources.resolve(ref, loaded.path.parent)
			meta, body = document(content.text)
			visit(Script(body, content.path, meta, content.ref), 1, inherited=renderer.defaults)
	return {"inputs": sorted(names), "references": sorted(references), "unresolved": unresolved}

# EOF
