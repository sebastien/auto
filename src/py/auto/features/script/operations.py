"""Module: operations — Ephemeral script expansion, discovery and execution."""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from liquid.exceptions import LiquidError

from auto.features.script.config import commands, validateConfig
from auto.features.script.model import Script
from auto.features.script.templates import Renderer
from auto.runtime.model import Context, FailureKind, Limits, RunStatus, failureFor, statusFor
from auto.runtime.operations import Engine, Resolve, Shell, Value, isReference
from auto.utils.data import AutoError, document, jsonText


def loadScript(context: Context, script: Script | str | dict) -> Script:
	if isinstance(script, Script):
		return script
	if isinstance(script, dict):
		meta, body = (script["meta"], script["content"]) if "meta" in script else document(script["content"])
		return Script(body, Path(script.get("path", context.project / "script.md")), meta)
	content = Resolve(context, script if isReference(script) else "./" + script)
	meta, body = document(content.text)
	return Script(body, content.path, meta)


def prepare(context: Context, script) -> dict:
	loaded = loadScript(context, script)
	try:
		renderer = Renderer(context, loaded)
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
		return {"prompt": prompt, "config": {key: renderer.config[key] for key in ("engine", "provider", "model", "agent", "temperature", "cwd", "env", "prompt", "isolated") if key in renderer.config}, "output": output}
	except (LiquidError, ValueError, SchemaError) as error:
		raise AutoError(f"{loaded.path}: {error}") from error


def ScriptExpand(context: Context, script) -> str:
	"""Render instructions and output contract without invoking an engine."""
	return prepare(context, script)["prompt"]


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
			if value.startswith("task:"):
				continue
			content = Resolve(context, value, loaded.path.parent)
			if value in transclusions and content.path.suffix == ".md":
				meta, body = document(content.text)
				visit(Script(body, content.path, meta), depth + 1, (*stack, str(loaded.path)))
	visit(loadScript(context, script))
	return {"inputs": sorted(names), "references": sorted(references), "unresolved": unresolved}


def conform(output, contract: dict):
	try:
		if contract.get("format") == "json" and isinstance(output, str):
			def invalidConstant(value):
				raise ValueError(f"Invalid JSON constant: {value}")
			output = json.loads(output, parse_constant=invalidConstant)
			jsonText(output)
		elif contract.get("format", "markdown") in {"text", "markdown"} and not isinstance(output, str):
			raise AutoError("Expected textual output", "output")
		if "schema" in contract:
			Draft202012Validator(contract["schema"]).validate(output)
		return output
	except (ValueError, ValidationError) as error:
		raise AutoError(f"Output does not conform: {error}", "output") from error


def executePrepared(context: Context, request: dict):
	"""Drive one engine stream; answers are sent back through the generator."""
	engine = Engine(context, request["config"].get("engine", "pi"))
	stream = engine.start(context, request)
	answer = None
	try:
		while True:
			try:
				event = stream.send(answer)
			except StopIteration:
				raise AutoError("Engine ended without a terminal event", "system") from None
			answer = None
			if event["type"] == "ScriptEnded":
				if event["status"] == RunStatus.SUCCEEDED:
					event["output"] = conform(event.get("output", ""), request["output"])
				yield event
				return
			answer = yield event
	except (AutoError, KeyboardInterrupt) as error:
		kind = error.kind if isinstance(error, AutoError) else FailureKind.CANCELLED
		yield {"type": "ScriptEnded", "status": statusFor(kind), "output": None,
			"failure": {"kind": failureFor(kind), "reason": str(error)}}
	finally:
		stream.close()


def ended(kind, reason, status=None) -> dict:
	return {"type": "ScriptEnded", "status": status or statusFor(kind), "output": None, "failure": {"kind": failureFor(kind), "reason": reason}}


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
		yield {"type": "ScriptItemStarted", "name": name, "value": item, "index": index, "count": len(items)}
		# Items are data: escaping keeps `!` and `PATH` forms from being resolved.
		ctx = context.derive(inputs={**context.inputs, name: "\\" + item if isinstance(item, str) else item})
		final = yield from relay(runLifecycle(ctx, loaded))
		result = {"name": name, "value": item, "status": final["status"], "output": final.get("output")}
		if final.get("failure"):
			result["failure"] = final["failure"]
		results.append(result)
		yield {"type": "ScriptItemEnded", "index": index, **result}
		if final["status"] in {RunStatus.CANCELLED, RunStatus.TIMED_OUT}:
			yield {**ended(final["failure"]["kind"], final["failure"]["reason"], final["status"]), "output": results}
			return
	failed = [result for result in results if result["status"] not in {RunStatus.SUCCEEDED, RunStatus.SKIPPED}]
	if failed:
		yield {**ended(FailureKind.OUTPUT, f"{len(failed)} of {len(results)} items failed"), "output": results}
	else:
		yield {"type": "ScriptEnded", "status": RunStatus.SUCCEEDED, "output": results}


def runLifecycle(context: Context, script: Script):
	"""Run `pre` checks, the prompt, `post` checks then `apply` effects for one item."""
	meta = script.meta
	if not any(meta.get(name) for name in ("pre", "post", "apply")):
		yield from runScript(context, script)
		return
	try:
		renderer = Renderer(context, script)
		namespace = renderer.bindings()
		# Inputs are resolved once, so shell values do not run again for the prompt.
		context = context.derive(resolvedInputs={**context.resolvedInputs, **namespace["inputs"]})
		def shell(step, command, stdin=""):
			command = renderer.shellArgument(command, namespace)
			yield {"type": "ScriptAction", "name": step, "content": command, "lifecycle": True}
			if context.limits:
				context.limits.check()
			stdout, stderr, code = Shell(context, command, renderer.config, stdin)
			return code, (stdout + stderr).strip() or f"{step} command failed ({code}): {command}"
		for command in commands(meta.get("pre")):
			code, reason = yield from shell("pre", command)
			if code:
				yield ended(FailureKind.INPUT, reason, RunStatus.SKIPPED)
				return
		final = yield from relay(runScript(context, script))
		if final["status"] != RunStatus.SUCCEEDED:
			yield final
			return
		output = final["output"]
		namespace = {**namespace, "output": output}
		text = output if isinstance(output, str) else jsonText(output)
		for step, kind in (("post", FailureKind.OUTPUT), ("apply", FailureKind.SYSTEM)):
			for command in commands(meta.get(step)):
				code, reason = yield from shell(step, command, text)
				if code:
					yield {**ended(kind, reason), "output": output}
					return
		yield {"type": "ScriptEnded", "status": RunStatus.SUCCEEDED, "output": output}
	except (AutoError, KeyboardInterrupt) as error:
		yield ended(error.kind if isinstance(error, AutoError) else FailureKind.CANCELLED, str(error))
	except LiquidError as error:
		yield ended(FailureKind.INPUT, f"{script.path}: {error}")


def relay(stream):
	"""Forward events and answers, returning the terminal `ScriptEnded` event."""
	answer = None
	try:
		while True:
			event = stream.send(answer)
			if event["type"] == "ScriptEnded":
				return event
			answer = yield event
	finally:
		stream.close()


def runScript(context: Context, script):
	"""Run the prompt through the engine, streaming its events."""
	stream = None
	try:
		request = prepare(context, script)
		if context.observe:
			context.observe({"type": "ExecutionStarted", "config": request["config"], "output": request["output"]})
		stream = executePrepared(context, request)
		answer = None
		while True:
			event = stream.send(answer)
			answer = yield event
			if event["type"] == "ScriptEnded":
				return
			if event["type"] == "ScriptQuestion" and answer is None:
				if context.question:
					answer = context.question(event)
				else:
					raise AutoError("Question requires interactive input", FailureKind.INPUT)
	except (AutoError, KeyboardInterrupt) as error:
		kind = error.kind if isinstance(error, AutoError) else FailureKind.CANCELLED
		yield {"type": "ScriptEnded", "status": statusFor(kind), "output": None, "failure": {"kind": failureFor(kind), "reason": str(error)}}
	finally:
		if stream is not None:
			stream.close()

# EOF
