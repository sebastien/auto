"""Module: references — Canonical and plan-scoped content references."""

from __future__ import annotations

from pathlib import Path

from auto.features.plan.model import Plan
from auto.features.plan.store import planPath
from auto.runtime.model import Content, RunStatus
from auto.utils.data import AutoError, digest, document, identifier, jsonText, readJson


def latestRun(path: Path, task: str, succeeded: bool = False, seen: tuple = ()):
	runs = [readJson(file) for file in (path / "runs").glob("*/receipt.json")]
	if not succeeded:
		runs += [readJson(file) for file in (path / "runs").glob("*/active.json") if not (file.parent / "receipt.json").exists()]
	runs = [run for run in runs if run["task"] == task and (not succeeded or run["status"] == RunStatus.SUCCEEDED)]
	if succeeded:
		if task in seen:
			raise AutoError("Aggregate dependency cycle")
		children = []
		for file in (path / "tasks").glob("*.md"):
			meta, _ = document(file.read_text())
			if meta.get("parent", "root") == task:
				children.append(meta.get("id", file.stem))
		expected = {child: (latestRun(path, child, True, (*seen, task)) or {}).get("id") for child in children}
		runs = [run for run in runs if run.get("children", {}) == expected and all(expected.values())]
	return max(runs, key=lambda run: run["started"]) if runs else None


def planContent(context, ref: str) -> Content:
	scheme, _, rest = ref.partition(":")
	if scheme == "auto":
		parts = rest.split("/")
		plan = identifier(parts.pop(0))
		path = context.project / ".auto/plans" / plan
		if context.plan and Plan.read(context.plan).id == plan:
			path = context.plan
		if not parts:
			file = path / "plan.md"
			data = file.read_bytes()
			return Content(ref, data, digest(data), file)
		scheme = parts.pop(0)
		rest = "/".join(parts)
		if scheme == "input":
			scheme = "plan"
	else:
		path = planPath(context)
	planId = Plan.read(path).id
	name, _, part = rest.partition("/")
	identifier(name)
	canonical = f"auto:{planId}/{ 'input' if scheme == 'plan' else scheme}/{rest}"
	if scheme == "task":
		if part == "definition":
			file = path / "plan.md" if name == "root" else path / "tasks" / (name + ".md")
		elif not part:
			run = latestRun(path, name, True)
			if run is None:
				raise AutoError(f"Task has no succeeded run: {name}")
			file = path / "runs" / run["id"] / run["outputFile"]
		else:
			raise AutoError(f"Unknown task reference part: {part}")
	elif scheme == "run":
		root = path / "runs" / name
		if part not in {"", "receipt", "journal"}:
			raise AutoError(f"Unknown run reference part: {part}")
		file = root / ({"receipt": "receipt.json", "journal": "journal.jsonl"}.get(part) or (readJson(root / "receipt.json") or readJson(root / "active.json") or {}).get("outputFile", "output.txt"))
	elif scheme == "revision":
		for line in (path / "journal.jsonl").read_bytes().splitlines():
			import json
			if json.loads(line)["id"] == name:
				return Content(canonical, line, digest(line), path / "journal.jsonl")
		raise AutoError(f"Unknown revision: {name}")
	elif scheme == "plan":
		from auto.runtime.operations import Value
		meta = Plan.read(path).meta
		if name not in meta.get("inputs", {}):
			raise AutoError(f"Unknown plan input: {name}")
		value = Value(context, meta["inputs"][name], path)
		data = (value if isinstance(value, str) else jsonText(value)).encode()
		return Content(canonical, data, digest(data), path / "plan.md")
	elif scheme in {"gap", "decision"}:
		from auto.runtime.operations import Knowledge
		return Knowledge(context).readReference(canonical)
	else:
		raise AutoError(f"Unknown plan reference: {ref}")
	try:
		data = file.read_bytes()
	except OSError as error:
		raise AutoError(f"Cannot resolve {ref}: {error}") from error
	return Content(canonical, data, digest(data), file)

# EOF
