"""Module: model — Task documents, contracts and serializable definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Callable
from pathlib import Path

from auto.features.script.model import Script
from auto.runtime.model import RunStatus, TERMINAL
from auto.utils.data import AutoError, document, documentText, identifier, sections
from auto.utils.layout import taskId

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------


@dataclass(slots=True)
class Run:
	"""Stored execution of a task, from `Pending` to a terminal status."""

	id: str
	task: str
	status: str = RunStatus.PENDING
	started: str = ""
	ended: str | None = None
	runtime: dict = field(default_factory=dict)
	budget: dict = field(default_factory=dict)
	usage: dict = field(default_factory=lambda: {"tokens": 0, "cost": 0.0, "time": 0.0})
	inputs: list = field(default_factory=list)
	outputFile: str = "output.txt"
	children: dict = field(default_factory=dict)
	failure: dict | None = None
	gap: str | None = None
	question: dict | None = None
	pre: dict | None = None
	post: dict | None = None
	evaluation: dict | None = None
	extra: dict = field(default_factory=dict)

	@property
	def terminal(self) -> bool:
		return self.status in TERMINAL

	def asDict(self) -> dict:
		value = {**self.extra, "id": self.id, "task": self.task, "status": self.status, "started": self.started, "ended": self.ended,
			"runtime": self.runtime, "budget": self.budget, "usage": self.usage, "inputs": self.inputs, "children": self.children}
		if self.outputFile:
			value["outputFile"] = self.outputFile
		for key in ("failure", "gap", "question", "pre", "post", "evaluation"):
			item = getattr(self, key)
			if item is not None:
				value[key] = item
		return value


@dataclass(slots=True)
class Task:
	"""A script decorated with separately executed checks and a plan facet."""

	id: str
	meta: dict
	sections: dict
	path: Path
	description: str = ""
	# Transient authoring state; taskText/asDict never persist this projection.
	projectDefaults: Callable[[dict], dict] | None = field(default=None, repr=False, compare=False)

	def script(self, section: str = "Process") -> Script:
		return Script(self.sections.get(section, ""), self.path, self.meta, str(self.path) + "#" + section)

	def asDict(self) -> dict:
		return {**self.meta, "id": self.id, "sections": self.sections, "description": self.description, "path": str(self.path)}

# -----------------------------------------------------------------------------
#
# UTILITIES
#
# -----------------------------------------------------------------------------


def defaultGoal(meta: dict, description: str) -> str:
	"""Return the goal a task has when it states none: its title, then its description."""
	return "\n".join(part for part in (meta.get("title", ""), description.strip()) if part)


def taskText(task: Task) -> str:
	"""Serialise a task back to its markdown document form, without derivable fields."""
	parts = [task.description, *(f"## {name}\n\n{content}" for name, content in task.sections.items())]
	body = "\n\n".join(part for part in parts if part) + "\n"
	meta = {key: value for key, value in task.meta.items() if key != "goal" or value != defaultGoal(task.meta, task.description)}
	# The root task is named by its plan's directory, not by an `id` field.
	return documentText(meta if task.id == "root" else {**meta, "id": task.id}, body)

# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------


def createTaskFromText(text: str, path: Path, root: bool = False) -> Task:
	"""Create a task from its markdown document, defaulting goal and identifier."""
	meta, body = document(text)
	description, parts = sections(body)
	unknown = set(parts) - {"Pre", "Process", "Post", "Evaluation"}
	if unknown:
		raise AutoError(f"Unknown task sections: {', '.join(sorted(unknown))}")
	id = "root" if root else identifier(meta.get("id", path.stem))
	meta.setdefault("goal", defaultGoal(meta, description))
	return Task(id, meta, parts, path, description.strip())


def canonicalTask(task: Task) -> Task:
	"""Give a plan task its canonical id and parent (see `taskId`)."""
	task.id = taskId(task.id)
	if task.meta.get("parent"):
		task.meta["parent"] = taskId(task.meta["parent"])
	return task


def createTaskFromDict(value: dict, path: Path) -> Task:
	"""Create a task from its serialised definition."""
	meta = {key: item for key, item in value.items() if key not in {"sections", "description", "path"}}
	return Task(identifier(value["id"]), meta, value.get("sections", {}), path, value.get("description", ""))


def createRunFromDict(value: dict) -> Run:
	"""Create a run from its stored receipt or active state."""
	core = {"id", "task", "status", "started", "ended", "runtime", "budget", "usage", "inputs", "outputFile",
		"children", "failure", "gap", "question", "pre", "post", "evaluation"}
	return Run(value["id"], value["task"], value.get("status", RunStatus.PENDING), value.get("started", ""), value.get("ended"),
		value.get("runtime", {}), value.get("budget", {}), value.get("usage", {"tokens": 0, "cost": 0.0, "time": 0.0}),
		list(value.get("inputs", [])), value.get("outputFile", "output.txt"), value.get("children", {}), value.get("failure"),
		value.get("gap"), value.get("question"), value.get("pre"), value.get("post"), value.get("evaluation"),
		{key: item for key, item in value.items() if key not in core})

# EOF
