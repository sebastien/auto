"""Memo's mechanical consolidation operations and retained application contract."""

import json
import importlib

from auto.utils.data import AutoError


class MemoConsolidation:
	def __init__(self, project, agent):
		from memo import Memo
		from memo.backend.local import LocalBackend
		from memo.clock import SystemClock
		try:
			for module, names in (("due", ("agentDue",)), ("consolidation", ("agentConsolidate", "prepareConsolidation")),
				("consolidation_records", ("_consolidateRetained",))):
				loaded = importlib.import_module("memo.plugins.agent." + module)
				if any(not callable(getattr(loaded, name, None)) for name in names):
					raise ImportError("Missing Memo consolidation operation")
			if any(not callable(getattr(LocalBackend, name, None)) for name in ("lock", "readRecord", "retainRecord", "compactLog")):
				raise ImportError("Missing Memo retained application storage contract")
		except ImportError as error:
			raise AutoError(f"Memo does not provide runnable, restart-safe consolidation: {error}", "system") from error
		self.memo = Memo(LocalBackend(str(project / ".memo")), SystemClock(), cwd=str(project))
		self.agent = agent

	def compact(self):
		from memo.model import MemoError
		from memo.model.entry import entryReference
		from memo.plugins.agent.observations import observationStore
		try:
			_, log, _, _ = observationStore(self.memo, self.agent, self.memo.backend.listEnvelopes())
			return self.memo.compactLog(entryReference(log))
		except (MemoError, OSError) as error:
			raise AutoError(f"Memo observation compaction failed: {error}", "system") from error

	def due(self):
		from memo.model import MemoError
		from memo.model.entry import effectiveIdentity
		from memo.model.meta import canonicalMeta
		from memo.plugins.agent.due import agentDue
		from memo.plugins.agent.observations import observationSnapshot
		try:
			first = agentDue(self.memo, self.agent)
			pages = [first]
			for number in range(2, first["pages"] + 1):
				page = agentDue(self.memo, self.agent, page=number)
				if page["revision"] != first["revision"]:
					raise MemoError("due work changed while paging; retry from fresh inputs")
				pages.append(page)
			snapshot = observationSnapshot(self.memo, self.agent)
			if snapshot["log"].revision() != first["log_revision"] or snapshot["owner"].revision() != first["revisions"][snapshot["owner"].id]:
				raise MemoError("observations changed while capturing due work; retry")
			owner = snapshot["owner"]
			if effectiveIdentity(owner) is None:
				raise MemoError("assign the Memo agent an explicit identity before consolidation")
			dueIds = {row["id"] for page in pages for phase in page["phases"] for row in phase["items"] if "id" in row}
			record = list(dict.fromkeys(source for item in snapshot["log"].log.items if item.id in dueIds for source in item.sources))
			return {"agent": {"id": owner.id, "revision": owner.revision(), "properties": json.loads(canonicalMeta(owner.meta)), "content": owner.body},
				"due": {**first, "phases": [{"phase": phase["phase"], "total": phase["total"], "items": [item
					for page in pages for part in page["phases"] if part["phase"] == phase["phase"] for item in part["items"]]}
					for phase in first["phases"]]}, "record": record}
		except (MemoError, OSError) as error:
			raise AutoError(f"Memo due work unavailable: {error}", "system") from error

	def accepted(self, passes):
		"""Proposals accepted by a resolution not written by a consolidation pass, and not dismissed.

		`passes` are the run ids of this agent's consolidations, the writers of
		the proposals themselves; their resolutions are withdrawals, not acceptances.
		"""
		from memo.model import MemoError
		from memo.plugins.agent.observations import observationSnapshot
		try:
			items = list(observationSnapshot(self.memo, self.agent)["log"].log.items)
		except (MemoError, OSError) as error:
			raise AutoError(f"Memo observations unavailable: {error}", "system") from error
		proposals = {item.id: item for item in items if item.meta.get("type") == "proposal"}
		resolutions, dismissed = {}, set()
		for item in items:
			for target in item.meta.get("dismisses", []) or []:
				dismissed.add(target["item"] if isinstance(target, dict) else target)
			if item.writer in passes:
				continue
			for target in item.meta.get("resolves", []) or []:
				if target in proposals:
					resolutions.setdefault(target, item)
		return [{"id": key, "text": proposals[key].text, "reason": proposals[key].meta.get("reason", ""),
			"changes": proposals[key].meta.get("change", []), "sources": proposals[key].sources,
			"accepted": {"id": item.id, "text": item.text, "sources": item.sources}}
			for key, item in resolutions.items() if key not in dismissed]

	def validate(self, value):
		from memo.model import MemoError
		from memo.plugins.agent.consolidation import agentConsolidate, prepareConsolidation
		from memo.plugins.agent.prompt import PERSPECTIVE_FIELDS, _coreCandidates
		from memo.plugins.agent.layout import validateConfig
		from memo.query.select import select
		try:
			preview = agentConsolidate(self.memo, self.agent, value, dry=True)
			plan = prepareConsolidation(self.memo, self.agent, value)
			effects = {"memory.consolidate"}
			prospective = {entry.id: entry for entry in plan.entries}
			original = dict(prospective)
			original[plan.log.id], original[plan.agent.id] = plan.log, plan.agent
			for unit in plan.units:
				if unit["kind"] == "checked":
					if unit["before"] is None:
						original.pop(unit["after"].id, None)
					else:
						original[unit["before"].id] = unit["before"]
			def startup(entries, owner):
				config = validateConfig(owner.meta, owner.id)
				return {(candidate["key"], candidate["role"]) for candidate in _coreCandidates(self.memo, owner, config, entries, captured=True)}
			if startup(list(original.values()), plan.agent) != startup(plan.entries, prospective[plan.agent.id]):
				effects.add("memory.promote")
			for unit in plan.units:
				if unit["kind"] != "checked" or not unit["changed"]:
					continue
				before, after = unit["before"], unit["after"]
				old = before.meta if before else {}
				if bool(old.get("core")) != bool(after.meta.get("core")) or any(old.get(key) != after.meta.get(key) for key in ("agent_bindings", "prompt_bytes")):
					effects.add("memory.promote")
				oldCore = {key: data.get("core", False) for key, data in old.get("agent_nodes", {}).items() if isinstance(data, dict)}
				newCore = {key: data.get("core", False) for key, data in after.meta.get("agent_nodes", {}).items() if isinstance(data, dict)}
				if any(oldCore.get(key, False) != newCore.get(key, False) for key in oldCore.keys() | newCore.keys()):
					effects.add("memory.promote")
				if after.id == plan.agent.id:
					if before.body != after.body or any(old.get(key) != after.meta.get(key) for key in (*PERSPECTIVE_FIELDS, "reads")):
						effects.add("memory.evolve")
			proposals = []
			for proposal in value.get("report", {}).get("proposals", []):
				proposed = {"memory.consolidate"}
				for change in proposal["changes"]:
					if not isinstance(change, dict):
						raise MemoError("proposed changes must be objects")
					if any(not isinstance(change.get(key, {}), dict) for key in ("properties", "merge", "policies")):
						raise MemoError("proposed properties, merge and policies must be mappings")
					properties = {**change.get("properties", {}), **change.get("merge", {})}
					if properties.keys() & {"core", "agent_bindings", "agent_nodes", "prompt_bytes", "learnings"} or any("core" in policy for policy in change.get("policies", {}).values() if isinstance(policy, dict)):
						proposed.add("memory.promote")
					matches = select(self.memo.backend, self.memo.clock, change.get("entry", ""), None, entries=plan.entries, fuzzy=False)
					if any(entry.id == plan.agent.id for entry in matches) and (properties.keys() & {*PERSPECTIVE_FIELDS, "reads"} or change.keys() & {"content", "append", "history"}):
						proposed.add("memory.evolve")
				proposals.append(sorted(proposed))
			return {**preview, "effects": sorted(effects), "proposal_effects": proposals}
		except (MemoError, OSError) as error:
			raise AutoError(f"Invalid Memo consolidation: {error}", "output") from error

	def apply(self, value):
		from memo.model import MemoError, StaleState
		from memo.plugins.agent.consolidation import agentConsolidate
		try:
			return agentConsolidate(self.memo, self.agent, value)
		except MemoError as error:
			failure = AutoError(f"Memo consolidation application failed: {error}", "output" if isinstance(error, StaleState) else "system")
			failure.progress = getattr(error, "progress", None)
			failure.stale = isinstance(error, StaleState)
			raise failure from error
