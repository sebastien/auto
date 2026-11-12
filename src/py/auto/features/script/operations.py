"""Module: operations — Ephemeral script expansion, discovery and execution."""

from __future__ import annotations

import json
import re
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from liquid.exceptions import LiquidError

from auto.features.script.config import commands, validateConfig
from auto.features.script.model import Script, createScript
from auto.features.script.templates import Renderer
from auto.runtime.config import RUNTIME_DEFAULTS
from auto.runtime.model import Context, Event, FailureKind, Limits, RunStatus, failureFor, statusFor
from auto.runtime.operations import Engine, Resolve, Shell, Value, isReference
from auto.utils.config import RUNTIME_KEYS
from auto.utils.data import AutoError, document, jsonText


# -----------------------------------------------------------------------------
#
# UTILITIES
#
# -----------------------------------------------------------------------------


def loadScript(context: Context, script: Script | str | dict) -> Script:
	"""Load a script from a model, a mapping or a reference."""
	if isinstance(script, Script):
		return script
	if isinstance(script, dict):
		path = Path(script.get("path", context.project / "script.md"))
		return createScript(script["content"], path, script.get("meta")) if "meta" in script else createScript(script["content"], path)
	content = Resolve(context, script if isReference(script) else "./" + script)
	return createScript(content.text, content.path)


def prepare(context: Context, script, renderer: Renderer | None = None) -> dict:
	"""Render a script and its output contract for one engine request."""
	loaded = loadScript(context, script)
	try:
		renderer = renderer or Renderer(context, loaded)
		prompt = renderer.render()
		output = dict(renderer.config.get("output", {}))
		output.setdefault("format", "markdown")
		if output["format"] not in {"markdown", "text", "json"}:
			raise AutoError("Unsupported output format")
		for name in ("schema", "template"):
			if name in output:
				output[name] = Value(context, output[name], loaded.path.parent, renderer.config)
		if isinstance(output.get("schema"), str):
			output["schema"] = json.loads(output["schema"])
		if "schema" in output:
			Draft202012Validator.check_schema(output["schema"])
		if output:
			prompt += "\n\n## Output contract\n\n" + jsonText(output)
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


def executePrepared(context: Context, request: dict):
	"""Drive one engine stream; answers are sent back through the generator."""
	engine = Engine(context, request["config"].get("engine", RUNTIME_DEFAULTS["engine"]))
	stream = engine.start(context, request)
	answer = None
	try:
		while True:
			try:
				event = stream.send(answer)
			except StopIteration:
				raise AutoError("Engine ended without a terminal event", "system") from None
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
		renderer = Renderer(context, script)
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
		loaded = loadScript(context, script)
		request = prepare(context, loaded, renderer)
		if context.observe:
			context.observe({"type": Event.EXECUTION_STARTED, "config": request["config"], "output": request["output"], "transform": bool(loaded.meta.get("transform"))})
		stream = executePrepared(context, request)
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
		loaded = loadScript(context, script)
		validateConfig(loaded.meta)
		each = loaded.meta.get("each")
		if not each:
			yield from runLifecycle(context, loaded)
			return
		name, value = next(iter(each.items()))
		value = Value(context, value, loaded.path.parent, Renderer(context, loaded).config)
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
			result["failure"] = final["failure"]
		results.append(result)
		yield {"type": Event.SCRIPT_ITEM_ENDED, "index": index, **result}
		if final["status"] in {RunStatus.CANCELLED, RunStatus.TIMED_OUT}:
			yield {**ended(final["failure"]["kind"], final["failure"]["reason"], final["status"]), "output": results}
			return
	failed = [result for result in results if result["status"] not in {RunStatus.SUCCEEDED, RunStatus.SKIPPED}]
	if failed:
		yield {**ended(FailureKind.OUTPUT, f"{len(failed)} of {len(results)} items failed"), "output": results}
	else:
		yield {"type": Event.SCRIPT_ENDED, "status": RunStatus.SUCCEEDED, "output": results}


def ScriptInputs(context: Context, script) -> dict:
	"""Discover declared/implicit inputs and transitive references without shell effects."""
	names, references, unresolved, visited = set(), set(), [], set()
	def visit(loaded, depth=0, stack=()):
		if str(loaded.path) in stack:
			raise AutoError(f"Recursive transclusion: {loaded.path}")
		if str(loaded.path) in visited:
			return
		if depth >= context.maxDepth:
			raise AutoError("Input discovery depth exceeded")
		visited.add(str(loaded.path))
		renderer = Renderer(context, loaded)
		names.update(loaded.meta.get("inputs", {}))
		names.update(loaded.meta.get("each", {}) if depth == 0 else {})
		names.update(name for name in renderer.template.global_variable_paths(include_partials=False) if not name.startswith(("params", "run", "plan", "forloop")))
		values = list(loaded.meta.get("inputs", {}).values()) + list(loaded.meta.get("params", {}).values())
		values += list(loaded.meta.get("output", {}).values())
		transclusions = set()
		for kind, argument, inline in renderer.items:
			if kind == "file":
				transclusions.add(argument.strip('"'))
		values += list(transclusions)
		for value in values:
			if not isReference(value):
				continue
			if "{{" in value:
				unresolved.append(value)
				continue
			references.add(value)
			# Task outputs may not exist yet; graph validation checks their identity.
			if value.startswith("task:") or re.fullmatch(r"auto:[^/#]+/task/[^/#]+(?:/definition)?(?:#.*)?", value):
				continue
			content = Resolve(context, value, loaded.path.parent)
			if value in transclusions and content.path.suffix == ".md":
				meta, body = document(content.text)
				visit(Script(body, content.path, meta), depth + 1, (*stack, str(loaded.path)))
	visit(loadScript(context, script))
	return {"inputs": sorted(names), "references": sorted(references), "unresolved": unresolved}

# EOF
