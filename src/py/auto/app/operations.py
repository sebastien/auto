"""Module: operations — Shared exposure metadata and result projection."""

from __future__ import annotations

import importlib
import re
from dataclasses import dataclass

from auto.utils.data import AutoError, jsonText, yamlLoad


@dataclass(frozen=True, slots=True)
class Exposure:
	"""Describe one exposed operation and the need it serves."""
	name: str
	module: str
	arguments: tuple[str, ...]
	need: str
	mcp: bool = False
	payload: str | None = None
	stream: bool = False
	optional: tuple[str, ...] = ()
	noun: str = ""
	verb: str = ""

	def __post_init__(self):
		if self.noun and self.verb:
			return
		parts = re.findall(r"[A-Z][a-z]*", self.name)
		if len(parts) != 2:
			raise AutoError(f"Exposure must be two CamelCase words or set noun/verb: {self.name}")
		object.__setattr__(self, "noun", parts[0].lower())
		object.__setattr__(self, "verb", "run" if parts[1] == "Start" else parts[1].lower())

	@property
	def words(self) -> tuple[str, str]:
		return self.noun, self.verb

	@property
	def schema(self):
		properties = {name: {"type": "string"} for name in (*self.arguments, *self.optional, "plan", "engine", "provider", "model", "agent")}
		if "filter" in properties:
			properties["filter"] = {"type": "object"}
		if "budget" in properties:
			properties["budget"] = {"anyOf": [{"type": "string"}, {"type": "object"}]}
		if "since" in properties:
			properties["since"] = {"type": "integer"}
		if "answer" in properties:
			properties["answer"] = {"anyOf": [{"type": "string"}, {"type": "number"}, {"type": "boolean"}, {"type": "object"}, {"type": "array"}]}
		if self.payload:
			properties[self.payload] = {"anyOf": [{"type": "string"}, {"type": "object"}]}
		if self.name in {"ScriptStart", "ScriptExpand", "TaskStart", "PlanCreate"}:
			properties["inputs"] = {"type": "object"}
		if self.name in {"ScriptStart", "ScriptExpand"}:
			properties["params"] = {"type": "object"}
		return {"type": "object", "properties": properties, "required": list(self.arguments), "additionalProperties": False}

	def call(self, context, arguments):
		module = importlib.import_module("auto.features." + self.module)
		return getattr(module, self.name)(context, **arguments)


EXPOSURES = [
	Exposure("ScriptStart", "script.operations", ("script",), "Run a script standalone", True, "script", True),
	Exposure("ScriptExpand", "script.operations", ("script",), "Inspect a script prompt", payload="script"),
	Exposure("PlanCreate", "elaboration.operations", ("goal",), "Start a plan from a goal", optional=("budget",)),
	Exposure("PlanRefine", "elaboration.operations", ("reason",), "Refine a plan or decompose a task", optional=("task",)),
	Exposure("PlanStatus", "plan.operations", (), "Follow plan progress", True),
	Exposure("TaskStart", "task.operations", ("task",), "Run a task by hand"),
	Exposure("RunJournal", "task.operations", ("run",), "Follow a run", stream=True, optional=("since",)),
	Exposure("GapList", "plan.gaps", (), "Find what a plan needs", True, optional=("filter",)),
	Exposure("GapDecide", "plan.gaps", ("gap", "answer", "rationale"), "Resolve a gap with a human decision"),
	Exposure("CatalogueList", "elaboration.catalogue", (), "List the available framework", True, optional=("kind", "query")),
	Exposure("CatalogueGet", "elaboration.catalogue", ("identity",), "Read a framework entry", True),
	Exposure("ElaborationValidate", "elaboration.operations", ("elaboration",), "Validate an elaboration before applying it", True, "elaboration"),
]


def coerceArguments(exposure: Exposure, arguments: dict, context=None) -> dict:
	"""Apply interface-neutral coercions before calling an operation."""
	result = dict(arguments)
	for name in ("budget", "filter"):
		if name in result and isinstance(result[name], str):
			result[name] = yamlLoad(result[name])
	if "since" in result and isinstance(result["since"], str):
		try:
			result["since"] = int(result["since"])
		except ValueError as error:
			raise AutoError("since must be an integer") from error
	if exposure.name == "GapDecide" and isinstance(result.get("answer"), str):
		result["answer"] = yamlLoad(result["answer"])
	if context is not None:
		if exposure.name == "PlanCreate" and context.inputs:
			result["inputs"] = context.inputs
		if exposure.name in {"PlanRefine", "ElaborationValidate"}:
			result["plan"] = context.plan
	return result


def exitStatus(result, operation: str = "") -> int:
	if operation == "ElaborationValidate":
		return 1 if result else 0
	if isinstance(result, dict):
		if "run" in result and isinstance(result["run"], dict):
			return exitStatus(result["run"])
		if result.get("failure") or result.get("pass") is False:
			return {"TimedOut": 124, "Cancelled": 130, "Skipped": 3, "Blocked": 3, "Waiting": 3}.get(result.get("status"), 1)
		return {"Failed": 1, "Waiting": 3, "Blocked": 3, "Skipped": 3, "TimedOut": 124, "Cancelled": 130}.get(result.get("status"), 0)
	return 0


def render(result, format: str = "text") -> str:
	if format == "json":
		return jsonText(result)
	if isinstance(result, str):
		return result
	if isinstance(result, dict) and result.get("type") == "ScriptMessage":
		return result["content"]
	if isinstance(result, dict) and result.get("type") == "ScriptEnded" and result.get("output") is not None:
		return render(result["output"], format)
	if format == "markdown":
		from auto.features.script.templates import fence
		return fence(jsonText(result), "json")
	import json
	return json.dumps(result, indent=2, ensure_ascii=False)

# EOF
