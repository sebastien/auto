"""Module: operations — Shared exposure metadata and result projection."""

from __future__ import annotations

import importlib
import json
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from auto.runtime.model import Event, RunStatus, budgetValue
from auto.runtime.operations import Resolve, isReference
from auto.utils.data import AutoError, document, jsonText, yamlLoad
from auto.utils.layout import planPaths

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Descriptor:
	"""Describe how a human enters an argument (see spec-011)."""

	kind: str = "text"
	help: str = ""
	suffix: str = ""
	candidates: Callable | None = None
	recovery: str = ""
	label: str = ""


def firstLine(text) -> str:
	return str(text or "").strip().partition("\n")[0]


def planCandidates(context, arguments):
	from auto.features.plan.model import createPlan
	result = []
	for path in planPaths(context.project):
		try:
			plan = createPlan(path)
		except (AutoError, OSError, ValueError):
			continue
		result.append((plan.id, firstLine(plan.goal)))
	return result


def taskCandidates(context, arguments):
	from auto.features.task.operations import TaskList
	return [(task["id"], firstLine(task.get("title") or task.get("description"))) for task in TaskList(context)]


def runCandidates(context, arguments):
	from auto.features.task.operations import RunList
	runs = sorted(RunList(context), key=lambda run: run.get("started") or "", reverse=True)
	return [(run["id"], f"{run['task']} · {run['status']}") for run in runs]


def gapCandidates(context, arguments):
	from auto.features.plan.gaps import GapList
	from auto.features.plan.model import GapState
	return [(gap["id"], firstLine(gap["question"])) for gap in GapList(context, filter={"status": GapState.OPEN})]


def answerCandidates(context, arguments):
	if not arguments.get("gap"):
		return []
	from auto.features.plan.gaps import GapList
	gap = next((gap for gap in GapList(context) if gap["id"] == arguments["gap"]), {})
	options = [option.get("value", option.get("id", option.get("label"))) if isinstance(option, dict) else option for option in gap.get("options", [])]
	return [(str(option), "") for option in options if option is not None]


def catalogueCandidates(context, arguments):
	from auto.features.elaboration.catalogue import CatalogueList
	return [(entry["identity"], firstLine(entry.get("title") or entry.get("description"))) for entry in CatalogueList(context)]


def agentCandidates(context, arguments):
	from auto.features.memory.config import agentsFor
	return [("auto:agent/" + name, config["agent"]) for name, config in agentsFor(context).items()]


# Descriptors are keyed by argument name, which is consistent across exposures.
DESCRIPTORS = {
	"plan": Descriptor("choice", "The plan to work in", candidates=planCandidates, recovery='Create a plan with auto plan create "<goal>".'),
	"goal": Descriptor("multiline", "What the plan should achieve"),
	"reason": Descriptor("multiline", "Why the plan or task needs this change"),
	"mode": Descriptor("choice", "How to hold the plan", candidates=lambda context, arguments: [("Pause", "Finish the current run"), ("Stop", "Cancel the current run")]),
	"follow": Descriptor("text", "Keep following new plan events"),
	"task": Descriptor("choice", "A task of the plan", candidates=taskCandidates, recovery="Check the plan tasks with auto plan status."),
	"run": Descriptor("choice", "A run of the plan", candidates=runCandidates, recovery="Start a run with auto task run TASK."),
	"gap": Descriptor("choice", "An open gap of the plan", candidates=gapCandidates, recovery="List gaps with auto gap list."),
	"answer": Descriptor("value", "The decision, as a YAML value", candidates=answerCandidates),
	"rationale": Descriptor("text", "Why this decision"),
	"question": Descriptor("multiline", "What was decided"),
	"concerns": Descriptor("structured", "YAML list of labels describing what the decision affects"),
	"source": Descriptor("text", "Where the decision was made, eg. a conversation reference"),
	"identity": Descriptor("choice", "A catalogue entry", candidates=catalogueCandidates),
	"script": Descriptor("path", "A markdown script", suffix=".md"),
	"elaboration": Descriptor("path", "A task elaboration document", suffix=".md"),
	"audit": Descriptor("path", "An audit report", suffix=".md"),
	"budget": Descriptor("structured", "Optional YAML budget mapping, eg. {cost: 2, time: 1800}"),
	"effects": Descriptor("structured", "YAML list of dotted effect names"),
	"agents": Descriptor("structured", "YAML list of agent references"),
	"agent": Descriptor("choice", "The Auto agent to consolidate", candidates=agentCandidates),
	"phases": Descriptor("structured", "YAML list of Memoriser phases; omitted means all"),
	"overrideAllowance": Descriptor("text", "Explicitly override a consolidation allowance"),
	"filter": Descriptor("structured", "YAML mapping of fields to match"),
	"since": Descriptor("text", "Event index to follow from"),
}


@dataclass(frozen=True, slots=True)
class Exposure:
	"""Describe one exposed operation and the need it serves."""

	name: str
	module: str
	arguments: tuple[str, ...]
	need: str
	mcp: bool = False
	payload: str | None = None
	payloadKind: str = "text"
	stream: bool = False
	optional: tuple[str, ...] = ()
	inputs: bool = False
	params: bool = False
	valueArguments: tuple[str, ...] = ()
	resumable: bool = False
	passPlan: bool = False
	violations: bool = False
	messages: bool = False
	effects: bool = False
	needsPlan: bool = False
	ask: tuple[str, ...] = ()
	noun: str = ""
	verb: str = ""
	mcpVerb: str = ""

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
	def toolName(self) -> str:
		return self.noun + "_" + (self.mcpVerb or self.verb)

	@property
	def schema(self):
		properties = {name: {"type": "string"} for name in (*self.arguments, *self.optional, "plan", "engine", "provider", "model", "agent")}
		if "filter" in properties:
			properties["filter"] = {"type": "object"}
		if "budget" in properties:
			properties["budget"] = {"anyOf": [{"type": "string"}, {"type": "object"}]}
		if "effects" in properties:
			properties["effects"] = {"anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]}
		if "agents" in properties:
			properties["agents"] = {"type": "array", "items": {"type": "string"}}
		if "concerns" in properties:
			properties["concerns"] = {"type": "array", "items": {"type": "string"}}
		if "since" in properties:
			properties["since"] = {"type": "integer"}
		if "follow" in properties:
			properties["follow"] = {"type": "boolean"}
		if "overrideAllowance" in properties:
			properties["overrideAllowance"] = {"type": "boolean"}
		if "phases" in properties:
			properties["phases"] = {"type": "array", "items": {"type": "string"}}
		if "answer" in properties:
			properties["answer"] = {"anyOf": [{"type": "string"}, {"type": "number"}, {"type": "boolean"}, {"type": "object"}, {"type": "array"}]}
		if self.payload:
			properties[self.payload] = {"anyOf": [{"type": "string"}, {"type": "object"}]}
		if self.inputs:
			properties["inputs"] = {"type": "object"}
		if self.params:
			properties["params"] = {"type": "object"}
		for name, value in properties.items():
			if DESCRIPTORS.get(name, Descriptor()).help:
				value["description"] = DESCRIPTORS[name].help
			if DESCRIPTORS.get(name, Descriptor()).label:
				value["title"] = DESCRIPTORS[name].label
		return {"type": "object", "properties": properties, "required": list(self.arguments), "additionalProperties": False}

	def call(self, context, arguments):
		module = importlib.import_module("auto.features." + self.module)
		return getattr(module, self.name)(context, **arguments)


# Exposure order follows the interface specification table (see spec-007).
EXPOSURES = [
	Exposure("ScriptStart", "script.operations", ("script",), "Run a script standalone",
		mcp=True, payload="script", payloadKind="script", stream=True, inputs=True, params=True, messages=True, effects=True),
	Exposure("ScriptExpand", "script.operations", ("script",), "Inspect a script prompt", payload="script", payloadKind="script", inputs=True, params=True),
	Exposure("PlanCreate", "elaboration.operations", ("goal",), "Start a plan from a goal", optional=("budget", "effects", "agents"), inputs=True,
		effects=True, ask=("budget",)),
	Exposure("PlanRefine", "elaboration.operations", ("reason",), "Refine a plan or decompose a task", optional=("task",), resumable=True, passPlan=True,
		effects=True, needsPlan=True),
	Exposure("PlanList", "plan.operations", (), "Find the plans of a project", mcp=True, optional=("filter",)),
	Exposure("PlanStatus", "plan.operations", (), "Follow plan progress", mcp=True, needsPlan=True),
	Exposure("PlanContinue", "orchestration.operations", (), "Run a plan until it needs a human", stream=True, passPlan=True, effects=True, needsPlan=True),
	Exposure("PlanHold", "plan.control", ("mode", "reason"), "Pause or stop a plan", passPlan=True, effects=True, needsPlan=True),
	Exposure("PlanRelease", "plan.control", ("reason",), "Release a held plan", passPlan=True, effects=True, needsPlan=True),
	Exposure("PlanEvents", "plan.events", (), "Follow a plan from an event cursor", mcp=True, stream=True,
		optional=("since", "follow"), needsPlan=True),
	Exposure("PolicyResolve", "plan.policy", (), "Inspect plan policy", mcp=True, optional=("task",), needsPlan=True,
		noun="policy", verb="show", mcpVerb="resolve"),
	Exposure("TaskStart", "task.operations", ("task",), "Run a task by hand", inputs=True, resumable=True, effects=True, needsPlan=True),
	Exposure("RunGet", "task.operations", ("run",), "Inspect a run's status, failure and receipt", needsPlan=True),
	Exposure("RunOutput", "task.operations", ("run",), "Read the stored output of a run", needsPlan=True),
	Exposure("RunJournal", "task.operations", ("run",), "Follow a run", stream=True, optional=("since",), needsPlan=True),
	Exposure("GapList", "plan.gaps", (), "Find what a plan needs", mcp=True, optional=("filter",), needsPlan=True),
	Exposure("GapDecide", "plan.gaps", ("gap", "answer", "rationale"), "Resolve a gap with a human decision", valueArguments=("answer",),
		effects=True, needsPlan=True),
	Exposure("DecisionRecord", "plan.gaps", ("question", "answer", "rationale"), "Record a decision made without a gap",
		optional=("concerns", "source"), valueArguments=("answer",), passPlan=True, effects=True, needsPlan=True),
	Exposure("CatalogueList", "elaboration.catalogue", (), "List the available framework", mcp=True, optional=("kind", "query")),
	Exposure("CatalogueGet", "elaboration.catalogue", ("identity",), "Read a framework entry", mcp=True),
	Exposure("ConsolidationStart", "memory.consolidation", ("agent",), "Consolidate an agent's memory",
		optional=("phases", "reason", "overrideAllowance"), effects=True),
	Exposure("ConsolidationStatus", "memory.consolidation", ("agent",), "Inspect agent consolidation and due work", mcp=True),
	Exposure("ElaborationValidate", "elaboration.operations", ("elaboration",), "Validate an elaboration before applying it",
		optional=("task",), mcp=True, payload="elaboration", passPlan=True, violations=True, needsPlan=True),
	Exposure("AuditValidate", "audit.operations", ("audit",), "Validate an audit report", mcp=True, payload="audit", violations=True),
]

STATUS_EXIT = {RunStatus.FAILED: 1, RunStatus.WAITING: 3, RunStatus.BLOCKED: 3, RunStatus.SKIPPED: 3,
	RunStatus.TIMED_OUT: 124, RunStatus.CANCELLED: 130}
STRUCTURED_ARGUMENTS = ("budget", "filter", "effects", "agents", "concerns", "phases")

# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------


def coerceArguments(exposure: Exposure, arguments: dict) -> dict:
	"""Apply interface-neutral coercions before calling an operation."""
	result = dict(arguments)
	for name in STRUCTURED_ARGUMENTS:
		if name in result and isinstance(result[name], str):
			result[name] = yamlLoad(result[name])
	if "budget" in result:
		budgetValue(result["budget"])
	if "filter" in result and not isinstance(result["filter"], dict):
		raise AutoError("filter must be a mapping")
	for name in ("effects", "agents", "concerns", "phases"):
		if name in result and (not isinstance(result[name], list) or any(not isinstance(value, str) for value in result[name])):
			raise AutoError(f"{name} must be a list of strings")
	for name in exposure.valueArguments:
		if isinstance(result.get(name), str):
			result[name] = yamlLoad(result[name])
	if "since" in result and isinstance(result["since"], str):
		try:
			result["since"] = int(result["since"])
		except ValueError as error:
			raise AutoError("since must be an integer") from error
	if "since" in result and (type(result["since"]) is not int or result["since"] < 0):
		raise AutoError("since must be a non-negative integer")
	return result


def resolveContextArguments(context, exposure: Exposure, arguments: dict) -> dict:
	"""Pass the resolved plan to operations that require it explicitly."""
	if not exposure.passPlan:
		return arguments
	return {**arguments, "plan": context.plan}


def resolvePayload(context, exposure: Exposure, arguments: dict) -> dict:
	"""Resolve a payload given as content, a file path or `-` for stdin."""
	name = exposure.payload
	value = arguments.get(name) if name else None
	if not name or not isinstance(value, str):
		return arguments
	result = dict(arguments)
	if exposure.payloadKind == "script":
		# Scripts keep a path so their relative transclusions resolve at their origin.
		if value == "-" or "\n" in value:
			meta, body = document(sys.stdin.read() if value == "-" else value)
			result[name] = {"content": body, "meta": meta, "path": str(Path.cwd() / "stdin.md")}
		return result
	if value == "-" or "\n" in value:
		result[name] = sys.stdin.read() if value == "-" else value
	else:
		result[name] = Resolve(context, value if isReference(value) else "./" + value).text
	return result


def exitStatus(result, exposure: Exposure | None = None) -> int:
	"""Map an operation result to the stable interface exit status."""
	if exposure is not None and exposure.violations:
		return 1 if result else 0
	if isinstance(result, dict):
		if result.get("kind") == "ContinuationStopped":
			data = result["data"]
			if data["stop"] == "ended":
				return 0 if data.get("status") == RunStatus.SUCCEEDED else 1
			return 1 if data["stop"] == "failed" else 3
		if isinstance(result.get("run"), dict):
			return exitStatus(result["run"])
		if result.get("failure") or result.get("pass") is False:
			return STATUS_EXIT.get(result.get("status"), 1)
		return STATUS_EXIT.get(result.get("status"), 0)
	return 0


def render(result, format: str = "text") -> str:
	"""Render a result in `format` for an interface."""
	if format == "json":
		return jsonText(result)
	if isinstance(result, str):
		return result
	if isinstance(result, dict) and result.get("type") == Event.SCRIPT_MESSAGE:
		return result["content"]
	if isinstance(result, dict) and result.get("type") == Event.SCRIPT_ENDED and result.get("output") is not None:
		return render(result["output"], format)
	if format == "markdown":
		from auto.features.script.templates import fence
		return fence(jsonText(result), "json")
	return json.dumps(result, indent=2, ensure_ascii=False)


def resumeResult(context, exposure: Exposure, result, ask):
	"""Answer a nested Waiting run when the interface can ask the user."""
	if not ask or not exposure.resumable:
		return result
	from auto.features.plan.store import journal
	from auto.features.task.operations import RunAnswer, RunCancel, RunGet
	nested = isinstance(result, dict) and isinstance(result.get("run"), dict)
	if nested:
		run = result["run"]
		context = context.derive(plan=Path(result["plan"]))
	else:
		run = result
	while run["status"] == RunStatus.WAITING:
		try:
			RunAnswer(context, run, ask(run["question"]), "Answered interactively")
		except KeyboardInterrupt:
			RunCancel(context, run)
			raise
		run = RunGet(context, run)
	if not nested:
		return run
	revision = next((row for row in journal(context.plan) if row.get("elaborationRun") == run["id"]), None)
	return {**result, "run": run, "applied": revision is not None, **({"revision": revision} if revision else {})}

# EOF
