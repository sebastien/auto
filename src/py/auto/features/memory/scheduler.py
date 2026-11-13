"""Durable event triggers and bounded invocation-time housekeeping passes."""

from __future__ import annotations

from auto.features.memory.config import agentsFor
from auto.features.memory.consolidation import PHASES, queueRequest, requestLock, runPending, sessionFor
from auto.features.plan.events import eventRows, repairEvents
from auto.features.plan.model import PlanEventKind, createPlan
from auto.features.plan.store import locked
from auto.runtime.operations import Knowledge
from auto.runtime.model import RunStatus
from auto.utils.data import atomicWrite, jsonText, merge, readJson, timestamp


def diagnostic(root, error):
	value = {"at": timestamp(), "failure": {"kind": getattr(error, "kind", "system"), "reason": str(error)}}
	try:
		atomicWrite(root / "maintenance.json", jsonText(value))
	except OSError:
		# The source event remains durable and unconsumed, so later invocations retry.
		pass
	return value


def triggerCursor(root, base):
	"""Requests and run copies close the crash window between queue and cursor writes."""
	seq = readJson(root / "triggers.json", {}).get(base, 0)
	pending = readJson(root / "pending.json", {})
	seq = max(seq, pending.get("trigger_cursors", {}).get(base, 0))
	for file in (root / "runs").glob("*/request.json"):
		seq = max(seq, readJson(file).get("trigger_cursors", {}).get(base, 0))
	return seq


def queuePlan(context, path):
	"""Called with the plan writer lock: enqueue only, never run a model."""
	live = context.derive(defaults=context.policyDefaults) if context.policyDefaults is not None else context
	configured = agentsFor(live)
	plan = createPlan(path)
	rows, base = eventRows(path), "auto:" + plan.id
	for ref in plan.meta.get("agents", []):
		name = ref.removeprefix("auto:agent/")
		config = configured.get(name, {})
		triggers = config.get("triggers", [])
		if not triggers:
			continue
		root = context.project / ".auto/agents" / name
		root.mkdir(parents=True, exist_ok=True)
		try:
			with requestLock(root):
				cursor = triggerCursor(root, base)
				unseen = [row for row in rows if row["seq"] > cursor]
				session, due = None, None
				for event in unseen:
					kind, match = event["kind"], False
					if kind == PlanEventKind.ENDED and "plan-ended" in triggers:
						match = True
					elif kind in {PlanEventKind.GAP_RESOLVED, PlanEventKind.DECISION_RECORDED} and "decision" in triggers:
						if event["seq"] > readJson(root / "cursors.json", {}).get(base, 0):
							decision = Knowledge(context).get(f"{base}/decision/{event['data']['decision']}")
							labels = decision.get("concerns", [])
							match = not labels or "concerns" not in config or bool(set(labels).intersection(config["concerns"]))
					elif kind == PlanEventKind.RUN_ENDED:
						thresholds = [int(trigger.partition(":")[2]) for trigger in triggers if trigger.startswith("backlog:")]
						if thresholds:
							session = session or sessionFor(live, config)
							due = due or session.due()
							match = due["due"]["counts"]["unrouted"] >= min(thresholds)
					if match:
						session = session or sessionFor(live, config)
						# Confirm the required operations and a usable agent store before
						# describing the request as runnable; availability errors are retained.
						due = due or session.due()
						queueRequest(root, name, PHASES, [f"{kind}: {base} event {event['seq']}"], seen={base: event["seq"]})
				if unseen:
					cursors = readJson(root / "triggers.json", {})
					cursors[base] = unseen[-1]["seq"]
					atomicWrite(root / "triggers.json", jsonText(cursors))
		except Exception as error:
			diagnostic(root, error)


def hasWork(context):
	if context.environ.get("AUTO_HOUSEKEEPING") == "1":
		return False
	for name, config in agentsFor(context).items():
		root = context.project / ".auto/agents" / name
		try:
			needed = agentWork(root)
		except Exception:
			# Inspection errors are work to diagnose, rather than errors in the
			# user's completed command or grounds to discard durable state.
			return True
		if config.get("triggers") or needed:
			return True
	return False


def agentWork(root):
	if (root / "pending.json").exists() or any((root / "runs").glob("*/active.json")):
		return True
	cursors = readJson(root / "cursors.json", {})
	for file in (root / "runs").glob("*/staged.json"):
		application = readJson(file.parent / "application.json") or {}
		if not application.get("complete"):
			return True
		if not application.get("rejected") and any(seq > cursors.get(plan, 0) for plan, seq in readJson(file).get("cursors", {}).items()):
			return True
	return False


def HousekeepingCheck(context, exclude=()):
	"""Check invocation boundaries, running at most one pending pass per agent."""
	if context.environ.get("AUTO_HOUSEKEEPING") == "1":
		return []
	configured, results = agentsFor(context), []
	if not configured:
		return results
	for file in sorted((context.project / ".auto/plans").glob("*/plan.md")):
		try:
			with locked(file.parent):
				repairEvents(context.derive(plan=file.parent, runScope=None), file.parent)
				queuePlan(context, file.parent)
		except Exception as error:
			results.append(diagnostic(context.project / ".auto/housekeeping", error))
	for name, config in configured.items():
		if name in exclude or context.cancelled():
			continue
		root = context.project / ".auto/agents" / name
		try:
			if not agentWork(root):
				continue
			ctx = context.derive(plan=None, runScope=None, author="auto:housekeeping", question=None, observe=None, tick=None, limits=None,
				config=merge(context.config, {"env": {"AUTO_HOUSEKEEPING": "1", "AUTO_PLAN": ""}}),
				environ={**context.environ, "AUTO_HOUSEKEEPING": "1", "AUTO_PLAN": ""})
			result = runPending(ctx, name, config)
			results.append(result)
			atomicWrite(root / "maintenance.json", jsonText({"at": timestamp(), "status": result["status"],
				"run": result.get("id"), **({"failure": result["failure"]} if result.get("failure") else {})}))
			if result.get("status") == RunStatus.CANCELLED:
				break
		except Exception as error:
			results.append(diagnostic(root, error))
	return results
