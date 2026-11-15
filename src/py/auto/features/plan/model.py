"""Module: model — Plan documents, revisions, gaps and decisions."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from auto.utils.data import document, identifier, sections as splitSections
from auto.utils.layout import planFile

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------


class PlanState(StrEnum):
	"""Lifecycle state of a plan, derived from its root task and journal."""

	DRAFT = "Draft"
	ACTIVE = "Active"
	SUCCEEDED = "Succeeded"
	FAILED = "Failed"
	ABANDONED = "Abandoned"


class GapState(StrEnum):
	"""Resolution state of a gap."""

	OPEN = "Open"
	RESOLVED = "Resolved"
	DISMISSED = "Dismissed"


class GapKind(StrEnum):
	"""What a gap asks for."""

	INPUT = "input"
	INFORMATION = "information"
	DECISION = "decision"
	APPROVAL = "approval"


GAP_KINDS = frozenset(GapKind)


class RevisionOp(StrEnum):
	"""Operations recorded in a plan revision."""

	ADD = "add"
	REMOVE = "remove"
	REPLACE = "replace"
	EDIT = "edit"
	UPDATE = "update"
	RAISE = "raise"
	RESOLVE = "resolve"
	DISMISS = "dismiss"
	RECORD = "record"
	GRANT = "grant"
	ABANDON = "abandon"
	HOLD = "hold"
	RELEASE = "release"


class PlanEventKind(StrEnum):
	"""Durable event names exposed to plan supervisors."""

	REVISED = "PlanRevised"
	RUN_STARTED = "RunStarted"
	RUN_WAITING = "RunWaiting"
	RUN_ENDED = "RunEnded"
	GAP_RAISED = "GapRaised"
	GAP_RESOLVED = "GapResolved"
	DECISION_RECORDED = "DecisionRecorded"
	GAP_DISMISSED = "GapDismissed"
	HELD = "PlanHeld"
	RELEASED = "PlanReleased"
	CONTINUATION_STARTED = "ContinuationStarted"
	CONTINUATION_STOPPED = "ContinuationStopped"
	ENDED = "PlanEnded"


class ContinuationStop(StrEnum):
	"""Reasons a sequential plan continuation stops."""

	WAITING = "waiting"
	FAILED = "failed"
	BUDGET = "budget"
	HELD = "held"
	ENDED = "ended"
	BLOCKED = "blocked"
	CANCELLED = "cancelled"


@dataclass(slots=True)
class Hold:
	"""A human's durable suspension of plan progression."""

	mode: str
	author: str
	reason: str
	revision: str

	def asDict(self) -> dict:
		return {"mode": self.mode, "author": self.author, "reason": self.reason, "revision": self.revision}


@dataclass(slots=True)
class Continuation:
	"""A single sequential progression, with a durable stop record."""

	id: str
	started: str
	ended: str | None = None
	runs: list = field(default_factory=list)
	stop: str | None = None

	def asDict(self) -> dict:
		return {"id": self.id, "started": self.started, "ended": self.ended, "runs": self.runs, "stop": self.stop}


@dataclass(slots=True)
class PlanEvent:
	"""One append-only plan event, sequenced under the plan writer lock."""

	seq: int
	date: str
	kind: str
	ref: str
	data: dict = field(default_factory=dict)
	author: str | None = None

	def asDict(self) -> dict:
		return {"seq": self.seq, "date": self.date, "kind": self.kind, "ref": self.ref, "data": self.data,
			**({"author": self.author} if self.author is not None else {})}


@dataclass(slots=True)
class Plan:
	"""A plan directory: root task metadata plus its description and sections."""

	id: str
	meta: dict
	path: Path | None = None
	description: str = ""
	sections: dict = field(default_factory=dict)

	@property
	def goal(self) -> str:
		return self.meta.get("goal") or self.description

	@property
	def budget(self) -> dict:
		return self.meta.get("budget", {})

	def asDict(self) -> dict:
		return {**self.meta, "id": self.id, "description": self.description, "sections": self.sections, "path": str(self.path) if self.path else None}


@dataclass(slots=True)
class Revision:
	"""One append-only entry in the plan journal."""

	id: str
	author: str
	date: str
	reason: str
	operations: list
	metadata: dict = field(default_factory=dict)
	seq: int | None = None

	def asDict(self) -> dict:
		return {**self.metadata, "id": self.id, "author": self.author, "date": self.date, "reason": self.reason,
			"operations": self.operations, **({"seq": self.seq} if self.seq is not None else {})}


@dataclass(slots=True)
class Gap:
	"""Something a task or plan needs and does not have yet."""

	id: str
	identity: str
	plan: str
	kind: str
	question: str
	neededBy: list[str]
	status: str = GapState.OPEN
	revision: str = ""
	raisedBy: str = "human"
	subjects: list = field(default_factory=list)
	options: list = field(default_factory=list)
	decision: str | None = None
	rationale: str = ""
	resolvedRevision: str | None = None
	extra: dict = field(default_factory=dict)

	def asDict(self) -> dict:
		value = {**self.extra, "id": self.id, "identity": self.identity, "plan": self.plan, "gap_kind": self.kind,
			"question": self.question, "needed_by": self.neededBy, "status": self.status, "revision": self.revision, "raised_by": self.raisedBy}
		if self.subjects:
			value["subjects"] = self.subjects
		if self.options:
			value["options"] = self.options
		if self.decision:
			value["decision"] = self.decision
		if self.rationale:
			value["rationale"] = self.rationale
		if self.resolvedRevision:
			value["resolved_revision"] = self.resolvedRevision
		return value


@dataclass(slots=True)
class Decision:
	"""The resolution of a gap, with its rationale and author."""

	id: str
	identity: str
	plan: str
	gap: str | None
	answer: object
	rationale: str
	author: str
	date: str
	revision: str
	extra: dict = field(default_factory=dict)

	def asDict(self) -> dict:
		return {**self.extra, "id": self.id, "identity": self.identity, "plan": self.plan, **({"gap": self.gap} if self.gap else {}), "answer": self.answer,
			"rationale": self.rationale, "author": self.author, "date": self.date, "revision": self.revision}

# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------


def createPlan(path: Path) -> Plan:
	"""Create a plan from its directory, named by its id, or its root task file."""
	path = Path(path)
	directory = path if path.is_dir() else path.parent.parent
	meta, body = document(planFile(directory).read_text())
	description, parts = splitSections(body)
	return Plan(identifier(meta.get("id", directory.name)), meta, directory, description.strip(), parts)


def createRevisionFromDict(value: dict) -> Revision:
	"""Create a plan revision from its journal entry."""
	core = {"id", "author", "date", "reason", "operations", "seq"}
	return Revision(value["id"], value.get("author", "human"), value.get("date", ""), value.get("reason", ""), value.get("operations", []),
		{key: item for key, item in value.items() if key not in core}, value.get("seq"))


def createGapFromDict(value: dict) -> Gap:
	"""Create a gap from its stored record."""
	core = {"id", "identity", "plan", "gap_kind", "kind", "question", "needed_by", "status", "revision", "raised_by",
		"subjects", "options", "decision", "rationale", "resolved_revision"}
	return Gap(value["id"], value["identity"], value["plan"], value.get("gap_kind", value.get("kind", GapKind.INFORMATION)),
		value.get("question", ""), list(value.get("needed_by", [])), value.get("status", GapState.OPEN), value.get("revision", ""),
		value.get("raised_by", "human"), list(value.get("subjects", [])), list(value.get("options", [])), value.get("decision"),
		value.get("rationale", ""), value.get("resolved_revision"), {key: item for key, item in value.items() if key not in core})


def createDecisionFromDict(value: dict) -> Decision:
	"""Create a decision from its stored record."""
	core = {"id", "identity", "plan", "gap", "answer", "rationale", "author", "date", "revision"}
	return Decision(value["id"], value["identity"], value["plan"], value.get("gap"), value.get("answer"), value.get("rationale", ""),
		value.get("author", "human"), value.get("date", ""), value.get("revision", ""),
		{key: item for key, item in value.items() if key not in core})

# EOF
