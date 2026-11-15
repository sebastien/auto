"""Module: model — Task elaboration fields and their serialisation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from auto.utils.data import AutoError
from auto.utils.layout import taskId

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------


@dataclass(slots=True)
class TaskElaboration:
	"""The breakdown of a task goal into children, removals and gaps."""

	task: str
	summary: str = ""
	rationale: str = ""
	methodology: str | None = None
	tasks: list = field(default_factory=list)
	removals: list = field(default_factory=list)
	gaps: list = field(default_factory=list)
	context: list = field(default_factory=list)
	learnings: list = field(default_factory=list)
	extra: dict = field(default_factory=dict)

	def asDict(self) -> dict:
		value = {**self.extra, "task": self.task, "tasks": self.tasks, "removals": self.removals, "gaps": self.gaps}
		for key in ("summary", "rationale", "methodology", "context", "learnings"):
			item = getattr(self, key)
			if item:
				value[key] = item
		return value

# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------


def contextRef(item):
	"""Read a bare relative path relied on (eg. `docs/spec.md`) as a project PATH,
	and prose notes (eg. "goal supplied in the prompt") as no reference at all."""
	ref = item.get("ref") if isinstance(item, dict) else item
	if not isinstance(ref, str) or not ref or ref.startswith(("./", "../", "/")) or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:\S*$", ref):
		return item
	if re.search(r"\s", ref.strip()):
		return None
	return {**item, "ref": "./" + ref} if isinstance(item, dict) else "./" + ref


def createElaborationFromDict(value: dict) -> TaskElaboration:
	"""Create an elaboration from its parsed or serialised fields."""
	if not value.get("task"):
		raise AutoError("Elaboration requires its target task")
	core = {"task", "summary", "rationale", "methodology", "tasks", "removals", "gaps", "context", "learnings"}
	# Plan task ids are canonical (`tsk-…`), however an Elaborator writes them.
	def task(item):
		item = {**item, "id": taskId(item["id"])} if isinstance(item, dict) and isinstance(item.get("id"), str) else item
		return {**item, "replaces": taskId(item["replaces"])} if isinstance(item, dict) and isinstance(item.get("replaces"), str) else item
	def gap(item):
		return {**item, "needed_by": [taskId(id) for id in item["needed_by"]]} if isinstance(item, dict) and isinstance(item.get("needed_by"), list) else item
	value = {**value, "task": taskId(value["task"]), "tasks": [task(item) for item in value.get("tasks", [])],
		"removals": [task(item) for item in value.get("removals", [])], "gaps": [gap(item) for item in value.get("gaps", [])]}
	return TaskElaboration(task=value["task"], summary=value.get("summary", ""), rationale=value.get("rationale", ""),
		methodology=value.get("methodology"), tasks=list(value.get("tasks", [])), removals=list(value.get("removals", [])),
		gaps=list(value.get("gaps", [])), context=[ref for ref in map(contextRef, value.get("context") or []) if ref is not None], learnings=list(value.get("learnings", [])),
		extra={key: item for key, item in value.items() if key not in core})

# EOF
