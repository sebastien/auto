"""Decision-event snapshots consumed only by completed consolidations."""

from auto.features.plan.events import eventRows, repairEvents
from auto.features.plan.model import PlanEventKind, createPlan
from auto.features.plan.store import locked
from auto.runtime.operations import Knowledge
from auto.utils.data import atomicWrite, jsonText, readJson


def decisionInputs(context, name, config):
	root = context.project / ".auto/agents" / name
	previous = readJson(root / "cursors.json", {})
	cursors, decisions, seen = dict(previous), [], set()
	concerns = config.get("concerns")
	for file in sorted((context.project / ".auto/plans").glob("*/plan.md")):
		with locked(file.parent):
			plan = createPlan(file.parent)
			if "auto:agent/" + name not in plan.meta.get("agents", []):
				continue
			repairEvents(context.derive(plan=file.parent, runScope=None), file.parent)
			base = "auto:" + plan.id
			rows = eventRows(file.parent)
			cursors[base] = max(previous.get(base, 0), rows[-1]["seq"] if rows else 0)
			for event in rows:
				if event["seq"] <= previous.get(base, 0) or event["kind"] not in {PlanEventKind.GAP_RESOLVED, PlanEventKind.DECISION_RECORDED}:
					continue
				ref = base + "/decision/" + event["data"]["decision"]
				if ref in seen:
					continue
				decision = Knowledge(context).get(ref)
				labels = decision.get("concerns", [])
				if concerns is not None and labels and not set(labels).intersection(concerns):
					continue
				seen.add(ref)
				decisions.append({"ref": ref, "digest": decision["digest"]})
	return decisions, cursors


def advanceCursors(root, captured):
	"""The caller holds the agent lock; replay may only move cursors forward."""
	previous = readJson(root / "cursors.json", {})
	for plan, seq in captured.items():
		previous[plan] = max(previous.get(plan, 0), seq)
	atomicWrite(root / "cursors.json", jsonText(previous))
