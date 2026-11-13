"""Module: memo — Auto records through Memo's optimistic-write storage API."""

from __future__ import annotations

from pathlib import Path

from auto.runtime.model import Content
from auto.utils.data import AutoError, documentText

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------


class MemoKnowledge:
	"""Map canonical Auto identities to ordinary Memo documents."""

	def __init__(self, project: Path):
		try:
			from memo.backend.local import LocalBackend
		except ImportError as error:
			raise AutoError("Memo Python package is required for gaps: install sebastien/memo in this environment", "system") from error
		self.backend = LocalBackend(str(project / ".memo"))
		self.project = project

	def entries(self):
		self.backend.invalidate()
		return self.backend.listEntries()

	def memorySnapshot(self):
		"""Capture a stable view for wake allocation without writing learning material."""
		from auto.integration.memo.memory import MemoMemorySnapshot
		return MemoMemorySnapshot(self.project)

	def consolidation(self, agent: str):
		from auto.integration.memo.consolidation import MemoConsolidation
		return MemoConsolidation(self.project, agent)

	def record(self, entry):
		# Memo owns these fields; they are neither Auto payload nor write intent.
		value = {key: item for key, item in entry.meta.items() if key not in {"uuid", "created", "updated"}}
		if "needed_by" in value:
			value["needed_by"] = [ref.removeprefix(value["plan"] + "/task/") for ref in value["needed_by"]]
		return {**value, "digest": entry.revision()}

	def list(self, plan: str, kind: str) -> list:
		return [self.record(entry) for entry in self.entries() if entry.kind == kind and entry.meta.get("plan") == plan]

	def get(self, ref: str) -> dict:
		matches = [entry for entry in self.entries() if entry.meta.get("identity") == ref]
		if len(matches) != 1:
			raise AutoError(f"Missing or ambiguous Memo identity: {ref}")
		return self.record(matches[0])

	def write(self, kind: str, value: dict, expected: str = "") -> None:
		from memo.operator.resolve import entryForId
		value = {key: item for key, item in value.items() if key != "digest"}
		matches = [entry for entry in self.entries() if entry.meta.get("identity") == value["identity"]]
		if matches:
			current = matches[0]
			if {key: item for key, item in self.record(current).items() if key != "digest"} == value:
				return
			if not expected or current.revision() != expected:
				raise AutoError("Memo record changed; re-read and reconcile before retrying")
		else:
			if expected:
				raise AutoError("Memo record disappeared during update")
			plan = value["plan"].removeprefix("auto:").replace("-", "_")
			current = entryForId(f"auto/{kind}:{plan}+{value['id']}")
		body = "# " + value.get("question", kind.title()) + "\n\n" + value.get("rationale", "") + "\n"
		stored = dict(value)
		if "needed_by" in stored:
			stored["needed_by"] = [ref if ref.startswith("auto:") else stored["plan"] + "/task/" + ref for ref in stored["needed_by"]]
		try:
			self.backend.put(current.clone(meta={**current.meta, **stored}, body=body), expected=expected)
		except Exception as error:
			raise AutoError(f"Memo write failed: {error}", "system") from error

	def readReference(self, ref: str) -> Content:
		matches = [entry for entry in self.entries() if entry.meta.get("identity") == ref or entry.id == ref]
		if len(matches) != 1:
			raise AutoError(f"Missing or ambiguous Memo reference: {ref}")
		entry = matches[0]
		data = documentText(entry.meta, entry.body).encode()
		return Content(ref if ref.startswith("auto:") else "memo:" + ref, data, entry.revision(), Path(self.backend.pathFor(entry)))

# EOF
