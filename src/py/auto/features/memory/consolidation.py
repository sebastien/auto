"""Agent-scoped Memoriser runs, durable intent and Memo reconciliation."""

from __future__ import annotations

import fcntl
import time
from pathlib import Path

from auto.features.memory.config import agentsFor, memoryPolicy
from auto.features.memory.accounting import AllowanceExceeded, allowanceStatus, recordUsage, reserve
from auto.features.memory.decisions import advanceCursors, decisionInputs
from auto.features.plan.references import readReceipt, runDirectories
from auto.features.plan.store import locked
from auto.features.plan.policy import BUILTINS, ruleFor
from auto.features.script.operations import conform
from auto.features.task.model import Run, createTaskFromDict, createTaskFromText
from auto.features.task.operations import RunExecute, effectiveTask, emit
from auto.runtime.integration import ConsolidationIntegration
from auto.runtime.model import Event, RunStatus, TERMINAL, budgetValue
from auto.runtime.operations import Knowledge, Resolve
from auto.utils.data import AutoError, atomicWrite, identifier, identity, jsonText, merge, readJson, timestamp


PHASES = ("verify", "compress", "derive", "model", "promote", "surface", "evolve")


def agentConfig(context, agent):
	if not isinstance(agent, str):
		raise AutoError("An Auto agent must be a name or auto:agent reference")
	name = identifier(agent.removeprefix("auto:agent/"))
	config = agentsFor(context).get(name)
	if config is None:
		raise AutoError(f"Unknown Auto agent: {agent}")
	return name, config


class AgentRunScope:
	"""Use the ordinary task lifecycle without plan mutations or human gaps."""

	def __init__(self, name, config, session):
		self.name, self.config, self.session = name, config, session

	def directory(self, context, run):
		return context.project / ".auto/agents" / self.name / "runs" / identifier(run["id"] if isinstance(run, dict) else run)

	def variables(self, context, path, run):
		return {"instance": f"auto:agent/{self.name}/run/{run['id']}"}

	def save(self, context, path, run, state=None):
		run["usage_at"] = timestamp()
		if state is not None:
			atomicWrite(path / "state.json", jsonText(state))
		atomicWrite(path / ("receipt.json" if run["status"] in TERMINAL else "active.json"), jsonText(run))
		if run["status"] in TERMINAL:
			(path / "active.json").unlink(missing_ok=True)
		recordUsage(context, self.name, run)

	def policy(self, context, run, state):
		return createTaskFromDict(state["task"], Path(state["task"]["path"])), {
			"rules": {effect: {"rule": rule, "source": "memory policy"} for effect, rule in memoryPolicy(context, self.name).items()} |
				{effect: {"rule": "allow" if self.authorizeRequest(context, effect) else "deny", "source": "Memoriser read scope"} for effect in BUILTINS},
			"default": "deny", "effects": ["memory.consolidate"], "grants": [],
			"unenforceable": list(BUILTINS),
			"instructions": "Read material and produce a change set. Do not write Memo, repository files, VCS, networks or delete data. Auto applies validated changes."}

	def authorizeRequest(self, context, effect):
		if effect not in {"repo.read", "net.read"}:
			return False
		layers = context.policyLayers or ([("configuration", context.defaults["policy"])] if "policy" in context.defaults else [])
		return ruleFor(effect, layers)["rule"] == "allow"

	def check(self, context, path, run, state):
		value = conform((path / run["outputFile"]).read_text(), {"format": "json"})
		if not isinstance(value, dict) or value.get("id") != run["id"] or value.get("snapshot") != state["inputs"]["due"]["log_revision"]:
			raise AutoError("Consolidation must retain the supplied run id and captured log revision", "output")
		preview = self.session.validate(value)
		run["validation"], run["report"] = preview, value["report"]
		state["consolidation"] = {"agent": self.config["agent"], "value": value, "effects": preview["effects"],
			"proposal_effects": preview.get("proposal_effects", []), "cursors": state.get("decisionCursors", {})}
		state["consolidation"]["grants"] = state.get("grants", [])
		state["consolidation"]["accepted"] = state.get("accepted", [])
		self.authorize(context, preview["effects"], state)
		self.authorizeProposals(context, state["consolidation"])

	def finish(self, context, path, run, state):
		staged = readJson(path / "staged.json")
		if staged is None:
			staged = state["consolidation"]
			self.authorize(context, staged["effects"], staged)
			self.authorizeProposals(context, staged)
			atomicWrite(path / "staged.json", jsonText(staged))
		if staged["agent"] != self.config["agent"]:
			raise AutoError("Agent reference changed; reconcile the staged consolidation against its original Memo agent", "system")
		self.authorize(context, staged["effects"], staged)
		self.authorizeProposals(context, staged)
		firstAttempt = not staged.get("attempted", False)
		staged["attempted"] = True
		atomicWrite(path / "staged.json", jsonText(staged))
		try:
			application = self.session.apply(staged["value"])
			if not isinstance(application, dict) or application.get("dry") is not False or type(application.get("published")) is not bool or any(not isinstance(application.get(key), list) for key in ("completed", "pending", "uncertain")):
				raise AutoError("Memo returned invalid consolidation application progress", "system")
		except AutoError as error:
			application = getattr(error, "progress", None) or {"published": False, "uncertain": True}
			if firstAttempt and getattr(error, "stale", False) and not getattr(error, "progress", None):
				# Memo validates a fresh pass before retaining or publishing its units.
				application = {"published": False, "complete": True, "rejected": "stale before application"}
			atomicWrite(path / "application.json", jsonText({**application, "error": str(error)}))
			run["application"] = application
			raise
		application["complete"] = bool(application.get("published") or not application.get("pending") and not application.get("uncertain"))
		atomicWrite(path / "application.json", jsonText(application))
		run["application"], run["report"] = application, staged["value"]["report"]
		if not application["complete"]:
			raise AutoError("Memo has not completed the staged consolidation; reconcile before retrying", "system")
		advanceCursors(path.parent.parent, staged.get("cursors", {}))
		consumeAccepted(path.parent.parent, staged.get("accepted", []))
		emit(path, {"type": Event.CONSOLIDATION_APPLIED, "application": application})

	def authorization(self, context, state=None):
		"""The memory policy, with the effects granted by accepted proposals for this pass."""
		policy = memoryPolicy(context, self.name)
		return {effect: "allow" if effect in (state or {}).get("grants", []) and rule == "confirm" else rule for effect, rule in policy.items()}

	def authorize(self, context, effects, state=None):
		policy = self.authorization(context, state)
		blocked = {effect: policy.get(effect, "deny") for effect in effects if policy.get(effect) != "allow"}
		if blocked:
			raise AutoError(f"Consolidation changes require proposals or removal under memory policy: {blocked}", "output")

	def authorizeProposals(self, context, staged):
		policy = memoryPolicy(context, self.name)
		if any(policy[effect] == "deny" for proposal in staged.get("proposal_effects", []) for effect in proposal):
			raise AutoError("Consolidation proposes a denied memory effect", "output")


GRANTABLE = ("memory.promote", "memory.evolve")


def acceptedInputs(context, scope, root):
	"""Accepted proposals not yet taken up by an applied pass, and the effects they grant.

	An accepted proposal grants the effects its policy leaves to confirmation
	(never denied ones) to the next pass only, which re-derives it against
	current memory; once that pass is applied, the proposal is taken up.
	"""
	taken = set(readJson(root / "accepted.json") or [])
	passes = {path.name for path in runDirectories(root)}
	accepted = [proposal for proposal in scope.session.accepted(passes) if proposal["id"] not in taken]
	policy = memoryPolicy(context, scope.name)
	return accepted, [effect for effect in GRANTABLE if accepted and policy.get(effect) == "confirm"]


def consumeAccepted(root, accepted):
	if accepted:
		with requestLock(root):
			taken = list(dict.fromkeys([*(readJson(root / "accepted.json") or []), *accepted]))
			atomicWrite(root / "accepted.json", jsonText(taken))


def sessionFor(context, config):
	store = Knowledge(context)
	if not isinstance(store, ConsolidationIntegration):
		raise AutoError("Knowledge integration does not provide runnable Memo consolidation and reconciliation", "system")
	return store.consolidation(config["agent"])


def reconcile(context, scope, root):
	"""A free agent lock, never record age, proves that an old worker has stopped."""
	for path in runDirectories(root):
		run = readReceipt(path)
		staged = readJson(path / "staged.json")
		application = readJson(path / "application.json") or {}
		if staged and application.get("complete") and not application.get("rejected"):
			advanceCursors(root, staged.get("cursors", {}))
			consumeAccepted(root, staged.get("accepted", []))
		if staged and not application.get("complete"):
			state = readJson(path / "state.json")
			scope.finish(context, path, run, state)
			if run["status"] in TERMINAL:
				# Terminal receipts remain immutable; the application and journal project repair.
				emit(path, {"type": Event.CONSOLIDATION_RECONCILED, "application": run["application"]})
				continue
		if run["status"] not in TERMINAL:
			progress = run.get("application", application)
			completed = staged and progress.get("complete") and not progress.get("rejected")
			run.update(status=RunStatus.SUCCEEDED if completed else RunStatus.FAILED if progress.get("rejected") else RunStatus.CANCELLED, ended=timestamp())
			if progress.get("rejected"):
				run["failure"] = {"kind": "output", "reason": progress["rejected"]}
			if run["status"] == RunStatus.CANCELLED:
				run["failure"] = {"kind": "cancelled", "reason": "Interrupted agent worker; no live agent lock remains"}
			emit(path, {"type": Event.RUN_ENDED, "status": run["status"]})
			scope.save(context, path, run)


def ConsolidationStart(context, agent: str, phases: list | None = None, reason: str | None = None, overrideAllowance: bool = False):
	"""Request one Memoriser run, coalescing concurrent requests durably."""
	name, config = agentConfig(context, agent)
	if phases is not None and (not isinstance(phases, list) or not phases or any(phase not in PHASES for phase in phases)):
		raise AutoError("Consolidation phases must be a non-empty list of Memoriser phases")
	if reason is not None and not isinstance(reason, str) or type(overrideAllowance) is not bool:
		raise AutoError("Consolidation reason must be text and overrideAllowance must be boolean")
	root = context.project / ".auto/agents" / name
	root.mkdir(parents=True, exist_ok=True)
	queueRequest(root, name, phases or PHASES, [reason] if reason else [])
	return runPending(context, name, config, overrideAllowance)


def runPending(context, name, config, overrideAllowance=False):
	"""Recover stopped work, then claim an existing request without creating another."""
	root = context.project / ".auto/agents" / name
	root.mkdir(parents=True, exist_ok=True)
	with (root / ".lock").open("a+") as lock:
		try:
			fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
		except BlockingIOError:
			return {"agent": "auto:agent/" + name, "status": "Pending", "pending": readJson(root / "pending.json")}
		session = sessionFor(context, config)
		scope = AgentRunScope(name, config, session)
		ctx = context.derive(plan=None, runScope=scope, limits=None, inputs={}, params={}, evidence=[], allowShell=False)
		reconcile(ctx, scope, root)
		with requestLock(root):
			pending = readJson(root / "pending.json")
			if pending is None:
				return {"agent": "auto:agent/" + name, "status": "Idle"}
			if readReceipt(root / "runs" / pending["id"]):
				pending["id"] = identity()
				atomicWrite(root / "pending.json", jsonText(pending))
			path = root / "runs" / pending["id"]
			path.mkdir(parents=True, exist_ok=True)
			atomicWrite(path / "request.json", jsonText(pending))
			run, state = newRun(ctx, scope, config, pending, path)
			(root / "pending.json").unlink()
		return startRun(ctx, scope, config, pending, path, run, state, overrideAllowance)


def requestLock(root):
	path = root / "requests"
	path.mkdir(exist_ok=True)
	return locked(path)


def queueRequest(root, name, phases, reasons, seen=None):
	with requestLock(root):
		pending = readJson(root / "pending.json") or {"id": identity(), "agent": "auto:agent/" + name, "phases": [], "reasons": []}
		pending["phases"] = [phase for phase in PHASES if phase in pending["phases"] or phase in phases]
		pending["reasons"] = list(dict.fromkeys([*pending["reasons"], *reasons]))
		for plan, seq in (seen or {}).items():
			pending.setdefault("trigger_cursors", {})[plan] = max(pending.get("trigger_cursors", {}).get(plan, 0), seq)
		atomicWrite(root / "pending.json", jsonText(pending))
		return pending


def newRun(context, scope, config, pending, path):
	"""Publish the run before consuming its durable request."""
	run = Run(path.name, "memoriser", status=RunStatus.PENDING, started=timestamp(),
		budget=budgetValue(config.get("budget")), inputs=context.evidence,
		extra={"scope": "auto:agent/" + scope.name, "request": pending}).asDict()
	state = {"config": context.config, "defaults": context.defaults, "params": context.params,
		"inputs": {}, "completed": [], "memory": None}
	scope.save(context, path, run, state)
	return run, state


def startRun(context, scope, config, pending, path, run, state, overrideAllowance):
	"""Record inputs before the shared task lifecycle starts a model."""
	started, executionStarted = time.monotonic(), False
	try:
		content = Resolve(context, config.get("memoriser", "auto:catalogue/task/memorise"))
		task = createTaskFromText(content.text, content.path)
		task.meta = merge(task.meta, {key: config[key] for key in ("profile", "budget") if key in config})
		task = effectiveTask(context, task)
		if task.meta.get("apply") or task.meta.get("hints", {}).get("gate"):
			raise AutoError("A Memoriser returns a change set; apply commands and approval gates are not supported")
		state["task"], run["budget"] = task.asDict(), budgetValue(task.meta.get("budget"))
		reserve(context, scope.name, path, run, overrideAllowance)
		session = scope.session
		session.compact()
		inputs = session.due()
		decisions, state["decisionCursors"] = decisionInputs(context, scope.name, config)
		accepted, state["grants"] = acceptedInputs(context, scope, path.parent.parent)
		state["accepted"] = [proposal["id"] for proposal in accepted]
		inputs.update(decisions=decisions, accepted=accepted, phases=pending["phases"], authorization=scope.authorization(context, state), budget=run["budget"],
			instructions=config.get("instructions", ""), consolidation={"id": run["id"], "reason": pending["reasons"]})
		state["inputs"] = inputs
		run["usage"]["time"] += time.monotonic() - started
		scope.save(context, path, run, state)
		executionStarted = True
		return RunExecute(context, path, run, state)
	except (Exception, KeyboardInterrupt) as error:
		if not executionStarted:
			run["usage"]["time"] += time.monotonic() - started
		cancelled = isinstance(error, KeyboardInterrupt)
		blocked = isinstance(error, AllowanceExceeded)
		run.update(status=RunStatus.CANCELLED if cancelled else RunStatus.BLOCKED if blocked else RunStatus.FAILED, ended=timestamp(),
			failure={"kind": "cancelled" if cancelled else getattr(error, "kind", "system"), "reason": str(error) or "Interrupted"})
		if blocked:
			run["allowances"] = error.report
			run["pending"] = queueRequest(path.parent.parent, scope.name, pending["phases"], pending["reasons"])
		scope.save(context, path, run, state)
		return run


def ConsolidationStatus(context, agent: str):
	"""Inspect mechanical due work and durable agent run/application state."""
	name, config = agentConfig(context, agent)
	root = context.project / ".auto/agents" / name
	session = sessionFor(context, config)
	runs = [{**readReceipt(path), "application": readJson(path / "application.json") or {}} for path in runDirectories(root)]
	runs.sort(key=lambda run: run["started"])
	due = session.due()
	opened = [row for phase in due["due"]["phases"] for row in phase["items"] if row.get("group") == "open"]
	return {"agent": "auto:agent/" + name, "due": due["due"], "latest": runs[-1] if runs else None,
		"maintenance": readJson(root / "maintenance.json"),
		"allowances": allowanceStatus(context, name),
		"pending": readJson(root / "pending.json"), "incomplete": [run for run in runs if run["application"] and not run["application"].get("complete")],
		"open": opened, "questions": [row for row in opened if row["type"] == "question"],
		"proposals": [row for row in opened if row["type"] == "proposal"]}
