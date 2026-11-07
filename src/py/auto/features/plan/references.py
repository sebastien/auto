"""Module: references — Canonical and plan-scoped content references."""

from __future__ import annotations

import json
from pathlib import Path

from auto.features.plan.model import createPlan
from auto.features.plan.store import planPath
from auto.runtime.model import Content, RunStatus
from auto.runtime.operations import Knowledge, Value
from auto.utils.data import AutoError, digest, document, identifier, jsonText, readJson

# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------


def runDirectories(path: Path) -> list[Path]:
	"""Return the run directories of a plan, oldest first."""
	root = path / "runs"
	if not root.exists():
		return []
	return sorted(directory for directory in root.iterdir() if (directory / "active.json").exists() or (directory / "receipt.json").exists())


def readReceipt(directory: Path) -> dict | None:
	"""Read a run receipt, or its active state while in flight."""
	return readJson(directory / "receipt.json") or readJson(directory / "active.json")


def latestRun(path: Path, task: str, succeeded: bool = False, seen: tuple = ()):
	"""Return the latest run of a task, optionally a succeeded one."""
	runs = [receipt for directory in runDirectories(path) if (receipt := readReceipt(directory))]
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
	"""Resolve a plan-scoped or canonical reference to content."""
	scheme, _, rest = ref.partition(":")
	if scheme == "auto":
		parts = rest.split("/")
		plan = identifier(parts.pop(0))
		path = context.project / ".auto/plans" / plan
		if context.plan and createPlan(context.plan).id == plan:
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
	planId = createPlan(path).id
	name, _, part = rest.partition("/")
	identifier(name)
	canonical = f"auto:{planId}/{'input' if scheme == 'plan' else scheme}/{rest}"
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
		if part:
			file = root / {"receipt": "receipt.json", "journal": "journal.jsonl"}[part]
		else:
			run = readJson(root / "receipt.json") or readJson(root / "active.json") or {}
			file = root / run.get("outputFile", "output.txt")
	elif scheme == "revision":
		for line in (path / "journal.jsonl").read_bytes().splitlines():
			if json.loads(line)["id"] == name:
				return Content(canonical, line, digest(line), path / "journal.jsonl")
		raise AutoError(f"Unknown revision: {name}")
	elif scheme == "plan":
		meta = createPlan(path).meta
		if name not in meta.get("inputs", {}):
			raise AutoError(f"Unknown plan input: {name}")
		value = Value(context, meta["inputs"][name], path)
		data = (value if isinstance(value, str) else jsonText(value)).encode()
		return Content(canonical, data, digest(data), path / "plan.md")
	elif scheme in {"gap", "decision"}:
		return Knowledge(context).readReference(canonical)
	else:
		raise AutoError(f"Unknown plan reference: {ref}")
	try:
		data = file.read_bytes()
	except OSError as error:
		raise AutoError(f"Cannot resolve {ref}: {error}") from error
	return Content(canonical, data, digest(data), file)

# EOF
