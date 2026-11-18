"""Module: runs — Recorded standalone script runs: receipt, journal and output.

`auto run` keeps each run under `.auto/runs/{id}/`, beside the plans: its
`receipt.json` (status, template, inputs matched for baselines, usage, files
changed), `journal.jsonl` (the script events) and its output. A template
declaring a `baseline` input receives the output of the latest succeeded run
of the same template and scope, so that repeated runs can report changes.
"""

from __future__ import annotations

import json
from pathlib import Path

from auto.features.script.model import Script
from auto.features.script.operations import ScriptStart, ended, loadScript
from auto.features.script.templates import Renderer
from auto.runtime.config import runtimeConfig, validateConfig
from auto.runtime.model import Content, Event, FailureKind, Limits, RunStatus, TERMINAL
from auto.runtime.operations import Resolve, Shell
from auto.utils.data import AutoError, atomicWrite, digest, document, identifier, identity, jsonText, readJson, sections, timestamp
from auto.utils.layout import storePath
from auto.utils.workspace import changes, snapshot

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------

RUNS = "runs"
RUN_PREFIX = "run"
# Recorded runs are referenced as `auto:runs/{id}`: `runs` is not a plan id.
REF_PREFIX = "auto:runs/"
BASELINE = "baseline"
# Inputs that, with the template, identify "the same" run for a baseline.
MATCH = ("facet", "scope")
SUFFIX = {"json": ".json", "text": ".txt"}

# -----------------------------------------------------------------------------
#
# UTILITIES
#
# -----------------------------------------------------------------------------


def runsRoot(project: Path) -> Path:
	"""Return where standalone script runs are kept."""
	return storePath(project) / RUNS


def receipts(project: Path) -> list[dict]:
	"""Return the receipts of the recorded runs, oldest first."""
	root = runsRoot(project)
	found = []
	for directory in sorted(root.iterdir()) if root.is_dir() else []:
		receipt = readJson(directory / "receipt.json") or readJson(directory / "active.json")
		if isinstance(receipt, dict) and receipt.get("id") == directory.name:
			found.append(receipt)
	return sorted(found, key=lambda receipt: receipt.get("started") or "")


def matchKey(script: Script, context) -> dict | None:
	"""Identify the runs a baseline may come from: same template, same scope."""
	if not script.template:
		return None
	values = {**script.meta.get("inputs", {}), **context.inputs}
	return {"from": script.template["ref"], **{name: values[name] for name in MATCH if name in values}}


def latestBaseline(project: Path, key: dict) -> dict | None:
	"""Return the latest succeeded run with the same match key and an output."""
	for receipt in reversed(receipts(project)):
		if receipt.get("status") == RunStatus.SUCCEEDED and receipt.get("match") == key and receipt.get("outputFile"):
			return receipt
	return None


def outputText(output, format: str) -> str:
	return jsonText(output) if format == "json" or not isinstance(output, str) else output


def artifactChecks(context, artifact: str, outputPath: Path, config: dict) -> list[str]:
	"""Collect the shell checks (`!?`) of an artifact's Post, bound to the stored output."""
	content = Resolve(context, artifact)
	_, body = document(content.text)
	_, parts = sections(body)
	if not parts.get("Post"):
		return []
	checks = []
	# Instructions for a model are task checks: a standalone run only runs shell checks.
	quiet = context.derive(inputs={}, params={}, resolvedInputs={}, variables={})
	renderer = Renderer(quiet, Script(parts["Post"], content.path, {}), checks=checks)
	renderer.render({"inputs": {}, "params": {}, "run": {"output": str(outputPath)}, "output": str(outputPath)})
	return checks

# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------


def ScriptRun(context, script):
	"""Run a script on its own, keeping its receipt, journal and output under `.auto/runs/`."""
	context = context.derive(limits=context.limits or Limits())
	try:
		loaded = loadScript(context, script)
		validateConfig(loaded.meta)
		config = runtimeConfig(context, loaded.meta, "script")
		missing = [name for name, value in loaded.meta.get("inputs", {}).items() if value is None and name not in context.inputs]
		if missing:
			# Nothing ran: no record is kept.
			raise AutoError("Missing required input: " + ", ".join(missing))
	except (AutoError, OSError, ValueError) as error:
		yield ended(getattr(error, "kind", FailureKind.INPUT), str(error))
		return
	id = identity(RUN_PREFIX)
	path = runsRoot(context.project) / identifier(id)
	path.mkdir(parents=True)
	template = loaded.template or {}
	run = {"id": id, "task": None, "status": RunStatus.RUNNING, "started": timestamp(), "ended": None,
		"script": template.get("script") or str(loaded.path), "inputs": [],
		**({"from": {key: template[key] for key in ("ref", "digest")}} if template else {}),
		**({"artifact": template["artifact"]} if template.get("artifact") else {})}
	key = matchKey(loaded, context)
	if key is not None:
		run["match"] = key
	atomicWrite(path / "active.json", jsonText(run))
	journal = (path / "journal.jsonl").open("a", encoding="utf-8")
	final, warnings = None, []
	def record(event):
		try:
			journal.write(jsonText(event) + "\n")
			journal.flush()
		except (TypeError, ValueError):
			pass
	def warn(text):
		warnings.append(text)
		event = {"type": Event.SCRIPT_META, "name": "warning", "value": text}
		record(event)
		return event
	try:
		inputs = loaded.meta.get("inputs", {})
		if key is not None and BASELINE in inputs and BASELINE not in context.inputs and inputs[BASELINE] in ("", None):
			previous = latestBaseline(context.project, key)
			if previous:
				text = (runsRoot(context.project) / previous["id"] / previous["outputFile"]).read_text(encoding="utf-8")
				context = context.derive(inputs={**context.inputs, BASELINE: "\\" + text})
				run["baseline"] = previous["id"]
				event = {"type": Event.SCRIPT_META, "name": BASELINE, "value": previous["id"]}
				record(event)
				yield event
		access = config.get("access")
		before = snapshot(context.project) if access == "read" else None
		if access == "read" and before is None:
			yield warn("Not a git working tree: changes made by this read-only run cannot be checked")
		stream, answer = ScriptStart(context, loaded), None
		try:
			while True:
				try:
					event = stream.send(answer)
				except StopIteration:
					break
				record(event)
				if event["type"] == Event.SCRIPT_ENDED:
					final = event
					break
				answer = yield event
		finally:
			stream.close()
		if final is None:
			final = ended(FailureKind.SYSTEM, "Script ended without a terminal event")
		format = (config.get("output") or {}).get("format", "markdown")
		if final.get("output") is not None:
			file = path / ("output" + SUFFIX.get(format if isinstance(final["output"], str) else "json", ".md"))
			atomicWrite(file, outputText(final["output"], format))
			run["outputFile"] = file.name
			if final["status"] == RunStatus.SUCCEEDED and template.get("artifact"):
				for command in artifactChecks(context, template["artifact"], file, config):
					event = {"type": Event.SCRIPT_ACTION, "name": "post", "content": command, "lifecycle": True}
					record(event)
					yield event
					stdout, stderr, code = Shell(context, command, config)
					if code:
						final = {**ended(FailureKind.OUTPUT, (stdout + stderr).strip() or f"Artifact check failed ({code})"), "output": final["output"]}
						break
		if before is not None:
			changed = changes(context.project, before) or []
			if changed:
				run["changes"] = changed
				yield warn("This read-only run changed files: " + ", ".join(item["path"] for item in changed))
	except (AutoError, OSError) as error:
		final = ended(getattr(error, "kind", FailureKind.SYSTEM), str(error))
	finally:
		final = final or ended(FailureKind.CANCELLED, "Execution cancelled", RunStatus.CANCELLED)
		run.update(status=final["status"], ended=timestamp(), usage=dict(context.limits.usage) if context.limits else {})
		if final.get("failure"):
			run["failure"] = final["failure"]
		if warnings:
			run["warnings"] = warnings
		final = {**final, "run": id, **({"outputPath": str(path / run["outputFile"])} if run.get("outputFile") else {})}
		record(final)
		journal.close()
		atomicWrite(path / "receipt.json", jsonText(run))
		(path / "active.json").unlink(missing_ok=True)
	yield final


def scriptRunContent(context, ref: str) -> Content:
	"""Resolve `auto:runs/{id}` to a recorded run's output, and `/receipt` or `/journal` to those files."""
	parts = ref.removeprefix(REF_PREFIX).split("/")
	if len(parts) not in (1, 2) or len(parts) == 2 and parts[1] not in {"receipt", "journal"}:
		raise AutoError(f"Unknown script run reference: {ref}", recovery=f"Use {REF_PREFIX}ID, {REF_PREFIX}ID/receipt or {REF_PREFIX}ID/journal.")
	path = runsRoot(context.project) / identifier(parts[0])
	receipt = readJson(path / "receipt.json") or readJson(path / "active.json")
	if not receipt:
		raise AutoError(f"Script run not found: {parts[0]}", recovery="List recorded runs with auto x script list.")
	if len(parts) == 2:
		file = path / ("receipt.json" if parts[1] == "receipt" else "journal.jsonl")
	elif receipt.get("status") not in TERMINAL or not receipt.get("outputFile"):
		raise AutoError(f"Script run has no output yet: {parts[0]}")
	else:
		file = path / receipt["outputFile"]
	data = file.read_bytes()
	return Content(ref, data, digest(data), file)


def ScriptList(context, filter: dict | None = None) -> list:
	"""List recorded script runs, newest first, filtered by field."""
	rows = [{"id": receipt["id"], "status": receipt.get("status"), "started": receipt.get("started"), "script": receipt.get("script"),
		**({"from": receipt["from"]["ref"]} if receipt.get("from") else {})} for receipt in receipts(context.project)]
	return [row for row in reversed(rows) if all(str(row.get(name)) == str(value) for name, value in (filter or {}).items())]


def ScriptOutput(context, record: str):
	"""Read the stored output of a recorded script run."""
	path = runsRoot(context.project) / identifier(record)
	receipt = readJson(path / "receipt.json") or readJson(path / "active.json")
	if not receipt:
		raise AutoError(f"Script run not found: {record}", recovery="List recorded runs with auto x script list.")
	if receipt.get("status") not in TERMINAL:
		raise AutoError(f"Script run is still {receipt.get('status')}: {record}")
	if not receipt.get("outputFile"):
		raise AutoError(f"Script run has no output: {record}")
	text = (path / receipt["outputFile"]).read_text(encoding="utf-8")
	return json.loads(text) if receipt["outputFile"].endswith(".json") else text

# EOF
