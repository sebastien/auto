"""Module: layout — Where plans live: a Memo store with one namespace per plan.

The project's `.auto/` directory is a Memo store. A plan is the namespace
`.auto/{plan}/`, whose Memo entries are grouped by three-letter kinds (`tsk`
for task definitions, the root task included, `gap` and `dec` for gaps and
decisions), named by their Auto identifiers, without the prefix their folder states (eg.
task `tsk-build-site` is `tsk/build-site.md`). Runs,
journals and locks are plain files beside them, which Memo does not scan.
"""

from __future__ import annotations

import re
from pathlib import Path

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------


STORE = ".auto"
PLAN_PREFIX = "pln-"
TASKS = "tsk"
TASK_PREFIX = "tsk-"
ROOT = "root"
RUNS = "runs"
HISTORY = ".history"
# Memo kinds of the knowledge records Auto keeps per plan.
KINDS = {"gap": "gap", "decision": "dec"}

# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------


def storePath(project: Path) -> Path:
	"""Return the project's plan store, a Memo store."""
	return project / STORE


def stem(id: str) -> str:
	"""Map an Auto identifier to a Memo topic, which most identifiers already are.

	Topics are lowercase words joined by single `_` or `-`: a run of separators
	becomes `-` if it has one, else `_`.
	"""
	value = re.sub(r"[^a-z0-9]+", lambda match: "-" if "-" in match[0] else "_", id.lower())
	return value.strip("_-") or "_"


def namespace(plan: str) -> str:
	"""Map a plan id to its Memo namespace: lowercase tokens joined by `-`, led by a letter."""
	value = re.sub(r"[^a-z0-9]+", "-", plan.lower()).strip("-")
	return value if re.match(r"[a-z]", value) else PLAN_PREFIX + value


def taskId(id: str) -> str:
	"""Return a plan task's canonical id, led by `tsk-` (eg. `build-site` → `tsk-build-site`).

	Callers and models may name a task without its prefix: every lookup accepts
	both forms. The root task keeps its reserved id, `root`.
	"""
	return id if not isinstance(id, str) or id == ROOT or id.startswith(TASK_PREFIX) else TASK_PREFIX + id


def taskRef(ref: str) -> str:
	"""Canonicalise the task named by a plan reference (`task:X…` or `auto:P/task/X…`)."""
	match = re.fullmatch(r"(task:|auto:[^/#]+/task/)([^/#]+)(.*)", ref, re.S) if isinstance(ref, str) else None
	return match[1] + taskId(match[2]) + match[3] if match else ref


def taskStem(id: str) -> str:
	"""Return a task's name without its prefix, as in its file name (the `tsk/` folder states the kind)."""
	return id.removeprefix(TASK_PREFIX) if isinstance(id, str) and id != TASK_PREFIX else id


def planFile(path: Path) -> Path:
	"""Return the root task definition of the plan at `path`."""
	return path / TASKS / (ROOT + ".md")


def taskName(id: str) -> str:
	"""Return a task definition's path relative to its plan."""
	return f"{TASKS}/{ROOT if id == 'root' else stem(taskStem(id))}.md"


def taskFile(path: Path, id: str) -> Path:
	"""Return the definition file of task `id` in the plan at `path`."""
	return path / taskName(id)


def taskFiles(path: Path) -> list[Path]:
	"""Return the child task definitions of a plan, without its root."""
	return [file for file in sorted((path / TASKS).glob("*.md")) if file.stem != ROOT]


def historyFile(path: Path, id: str) -> Path:
	"""Return where a removed task definition is archived."""
	return path / HISTORY / (stem(taskStem(id)) + ".md")


def runsPath(path: Path) -> Path:
	"""Return the directory holding a plan's runs."""
	return path / RUNS


def isPlan(path: Path) -> bool:
	"""Tell whether `path` is a plan directory."""
	return planFile(path).is_file()


def planPaths(project: Path) -> list[Path]:
	"""List the plan directories of a project, sorted by name."""
	return [file.parent.parent for file in sorted(storePath(project).glob(f"*/{TASKS}/{ROOT}.md"))]


def planNumber(name: str) -> str:
	"""Return the number of a created plan name (`pln-00a-slug` → `00a`), else ``""``."""
	match = re.fullmatch(rf"{PLAN_PREFIX}([0-9a-f]+)(?:-.*)?", name)
	return match[1] if match else ""

# EOF
