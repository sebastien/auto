"""Module: memo — Auto records through Memo's optimistic-write storage API."""

from __future__ import annotations

from pathlib import Path

from auto.runtime.model import Content
from auto.utils.data import AutoError, documentText
from auto.utils.layout import KINDS, namespace, stem, storePath

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------


# Record fields kept as the entry body: the question, then a rationale section.
TEXT = ("question", "rationale")
# Record fields derived from the identity, and fields kept even when empty.
DERIVED = ("id", "plan")
KEPT = ("answer",)
RATIONALE = "## Rationale\n\n"


def textBody(value: dict) -> str:
	"""Render a record's question and rationale as its Markdown body."""
	question, rationale = value.get("question"), value.get("rationale")
	parts = [part for part in (question, RATIONALE + rationale if rationale else None) if part]
	return "\n\n".join(parts) + "\n" if parts else ""


def bodyText(body: str, decision: bool) -> dict:
	"""Read a record's question and rationale back from its body (see `textBody`)."""
	body = body.removesuffix("\n")
	if body.startswith(RATIONALE):
		question, rationale = "", body.removeprefix(RATIONALE)
	else:
		question, separator, rationale = body.partition("\n\n" + RATIONALE)
		rationale = rationale if separator else ""
	# A decision always has a rationale, possibly empty; a gap has one once explained.
	return {**({"question": question} if question else {}), **({"rationale": rationale} if rationale or decision else {})}



class MemoKnowledge:
	"""Keep plan records in the plan store, and read project knowledge from `.memo`.

	A gap or decision of plan `P` is the Memo document `P/gap:ID` or `P/dec:ID`
	of the project's plan store, whose Memo `identity` is its canonical Auto
	reference (eg. `auto:P/gap/ID`), so it is read by path rather than by a scan.
	"""

	def __init__(self, project: Path):
		try:
			from memo.integrations.filesystem.backend import LocalBackend
		except ImportError as error:
			raise AutoError("Memo Python package is required for gaps: install sebastien/memo in this environment", "system") from error
		self.backend = LocalBackend(str(project / ".memo"))
		self.records = LocalBackend(str(storePath(project)))
		self.project = project

	def memorySnapshot(self):
		"""Capture a stable view for wake allocation without writing learning material."""
		from auto.integration.memo.memory import MemoMemorySnapshot
		return MemoMemorySnapshot(self.project)

	def consolidation(self, agent: str):
		from auto.integration.memo.consolidation import MemoConsolidation
		return MemoConsolidation(self.project, agent)

	def entryId(self, plan: str, kind: str, id: str) -> str:
		"""Return the Memo entry of record `id` of `kind` in `plan` (`auto:P`)."""
		if kind not in KINDS:
			raise AutoError(f"Unknown Auto record kind: {kind}")
		return f"{namespace(plan.removeprefix('auto:'))}/{KINDS[kind]}:{stem(id)}"

	def locate(self, ref: str) -> str | None:
		"""Map a canonical record reference (`auto:P/gap/ID`) to its Memo entry."""
		plan, _, rest = ref.removeprefix("auto:").partition("/")
		kind, _, id = rest.partition("/")
		return self.entryId(plan, kind, id) if ref.startswith("auto:") and kind in KINDS and id and "/" not in id else None

	def record(self, entry):
		# Memo owns these fields; they are neither Auto payload nor write intent.
		value = {key: item for key, item in entry.meta.items() if key not in {"uuid", "created", "updated"}}
		value.update(bodyText(entry.body or "", entry.kind == KINDS["decision"]))
		# The plan and id are parts of the identity (`auto:P/gap/ID`), stated once.
		plan, _, id = str(value.get("identity", "")).rpartition("/")
		value = {"id": id, "plan": plan.rsplit("/", 1)[0], **value}
		if "needed_by" in value:
			value["needed_by"] = [ref.removeprefix(value["plan"] + "/task/") for ref in value["needed_by"]]
		return {**value, "digest": entry.revision()}

	def entry(self, ref: str):
		"""Read the current entry of a record reference, or `None`."""
		id = self.locate(ref)
		if id is None:
			return None
		self.records.invalidate()
		entry = self.records.get(id)
		if entry is not None and entry.meta.get("identity") != ref:
			raise AutoError(f"Memo record {id} does not hold {ref}")
		return entry

	def list(self, plan: str, kind: str) -> list:
		name = namespace(plan.removeprefix("auto:"))
		self.records.invalidate()
		records = [self.record(entry) for entry in self.records.listEntries(name, KINDS[kind])]
		return [record for record in records if record["plan"] == plan]

	def get(self, ref: str) -> dict:
		entry = self.entry(ref)
		if entry is None:
			raise AutoError(f"Missing or ambiguous Memo identity: {ref}")
		return self.record(entry)

	def write(self, kind: str, value: dict, expected: str = "") -> None:
		from memo.features.repository.model import entryForId
		# Empty fields are not stored: an absent field reads as empty.
		value = {key: item for key, item in value.items() if key != "digest" and (key in KEPT or item not in (None, [], {}))}
		current = self.entry(value["identity"])
		if current is not None:
			if {key: item for key, item in self.record(current).items() if key != "digest"} == value:
				return
			if not expected or current.revision() != expected:
				raise AutoError("Memo record changed; re-read and reconcile before retrying")
		else:
			if expected:
				raise AutoError("Memo record disappeared during update")
			current = entryForId(self.entryId(value["plan"], kind, value["id"]))
		# Free text is the body, stated once; properties hold the structured fields.
		body = textBody(value)
		stored = {key: item for key, item in value.items() if key not in {*TEXT, *DERIVED}}
		if "needed_by" in stored:
			stored["needed_by"] = [ref if ref.startswith("auto:") else value["plan"] + "/task/" + ref for ref in stored["needed_by"]]
		try:
			self.records.put(current.clone(meta={**current.meta, **stored}, body=body), expected=expected)
		except Exception as error:
			raise AutoError(f"Memo write failed: {error}", "system") from error

	def readReference(self, ref: str) -> Content:
		if self.locate(ref):
			entry, backend = self.entry(ref), self.records
		else:
			self.backend.invalidate()
			matches = [entry for entry in self.backend.listEntries() if entry.meta.get("identity") == ref or entry.id == ref]
			if len(matches) > 1:
				raise AutoError(f"Ambiguous Memo reference: {ref}")
			entry, backend = (matches or [None])[0], self.backend
		if entry is None:
			raise AutoError(f"Missing Memo reference: {ref}")
		data = documentText(entry.meta, entry.body).encode()
		return Content(ref if ref.startswith("auto:") else "memo:" + ref, data, entry.revision(), Path(backend.pathFor(entry)))

# EOF
