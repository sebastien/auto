"""Module: model — Task elaboration fields and their serialisation."""

from __future__ import annotations

from dataclasses import dataclass, field

from auto.utils.data import AutoError

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


def createElaborationFromDict(value: dict) -> TaskElaboration:
	"""Create an elaboration from its parsed or serialised fields."""
	if not value.get("task"):
		raise AutoError("Elaboration requires its target task")
	core = {"task", "summary", "rationale", "methodology", "tasks", "removals", "gaps", "context", "learnings"}
	return TaskElaboration(task=value["task"], summary=value.get("summary", ""), rationale=value.get("rationale", ""),
		methodology=value.get("methodology"), tasks=list(value.get("tasks", [])), removals=list(value.get("removals", [])),
		gaps=list(value.get("gaps", [])), context=list(value.get("context", [])), learnings=list(value.get("learnings", [])),
		extra={key: item for key, item in value.items() if key not in core})

# EOF
