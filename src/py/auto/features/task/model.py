"""Module: model — Task documents, contracts and serializable definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from auto.features.script.model import Script
from auto.runtime.model import RunStatus, TERMINAL
from auto.utils.data import AutoError, document, documentText, identifier, sections

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

	@classmethod
	def fromDict(cls, value: dict) -> Run:
		core = {"id", "task", "status", "started", "ended", "runtime", "budget", "usage", "inputs", "outputFile", "children", "failure", "gap", "question", "pre", "post", "evaluation"}
		return cls(value["id"], value["task"], value.get("status", RunStatus.PENDING), value.get("started", ""), value.get("ended"),
			value.get("runtime", {}), value.get("budget", {}), value.get("usage", {"tokens": 0, "cost": 0.0, "time": 0.0}),
			list(value.get("inputs", [])), value.get("outputFile", "output.txt"), value.get("children", {}), value.get("failure"),
			value.get("gap"), value.get("question"), value.get("pre"), value.get("post"), value.get("evaluation"),
			{key: item for key, item in value.items() if key not in core})


@dataclass(slots=True)
class Task:
	"""A script decorated with separately executed checks and a plan facet."""
	id: str
	meta: dict
	sections: dict
	path: Path
	description: str = ""

	def script(self, section: str = "Process") -> Script:
		return Script(self.sections.get(section, ""), self.path, self.meta)

	def asDict(self) -> dict:
		return {**self.meta, "id": self.id, "sections": self.sections, "description": self.description, "path": str(self.path)}

	@classmethod
	def fromDict(cls, value: dict, path: Path) -> Task:
		meta = {key: item for key, item in value.items() if key not in {"sections", "description", "path"}}
		return cls(identifier(value["id"]), meta, value.get("sections", {}), path, value.get("description", ""))


def parseTask(text: str, path: Path, root: bool = False) -> Task:
	meta, body = document(text)
	description, parts = sections(body)
	unknown = set(parts) - {"Pre", "Process", "Post", "Evaluation"}
	if unknown:
		raise AutoError(f"Unknown task sections: {', '.join(sorted(unknown))}")
	id = "root" if root else identifier(meta.get("id", path.stem))
	meta.setdefault("goal", meta.get("title", "") + ("\n" + description.strip() if description.strip() else ""))
	return Task(id, meta, parts, path, description.strip())


def taskText(task: Task) -> str:
	return documentText({**task.meta, "id": task.id}, task.description + "\n\n" + "\n\n".join(f"## {name}\n\n{body}" for name, body in task.sections.items()) + "\n")


def taskFromDict(value: dict, path: Path) -> Task:
	return Task.fromDict(value, path)

# EOF
