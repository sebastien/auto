"""Module: model — Plan documents, revisions, gaps and decisions."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from auto.utils.data import document, identifier, sections as splitSections

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
	GRANT = "grant"
	ABANDON = "abandon"


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
		return self.meta.get("goal", "")

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
	gap: str
	answer: object
	rationale: str
	author: str
	date: str
	revision: str
	extra: dict = field(default_factory=dict)

	def asDict(self) -> dict:
		return {**self.extra, "id": self.id, "identity": self.identity, "plan": self.plan, "gap": self.gap, "answer": self.answer,
			"rationale": self.rationale, "author": self.author, "date": self.date, "revision": self.revision}

# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------


def createPlan(path: Path) -> Plan:
	"""Create a plan from its directory or `plan.md` file."""
	path = Path(path)
	file = path / "plan.md" if path.is_dir() else path
	directory = file.parent
	meta, body = document(file.read_text())
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
	return Decision(value["id"], value["identity"], value["plan"], value["gap"], value.get("answer"), value.get("rationale", ""),
		value.get("author", "human"), value.get("date", ""), value.get("revision", ""),
		{key: item for key, item in value.items() if key not in core})

# EOF
