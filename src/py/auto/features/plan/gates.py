"""Module: gates — Readiness of tasks guarded by pinned output approvals."""

from __future__ import annotations

from auto.features.plan.model import GapKind, GapState, createPlan
from auto.features.plan.references import latestRun, readReceipt
from auto.features.plan.store import planPath
from auto.runtime.operations import Knowledge, Resolve
from auto.utils.data import AutoError


def outputRun(context, gap):
	"""Return the local run whose output an approval gap pins, if any."""
	if gap.get("gap_kind", gap.get("kind")) != GapKind.APPROVAL:
		return None
	base = "auto:" + createPlan(planPath(context)).id + "/run/"
	for pin in gap.get("subjects", []):
		ref = pin["ref"]
		id = ref[len(base):] if ref.startswith(base) else ref[4:] if ref.startswith("run:") else ""
		if id and "/" not in id and "#" not in id:
			return id
	return None


def taskGapState(context, id, tasks, dependencies, gaps, *, starting=False):
	"""Return Open/Blocked when a gap or the current output's gate holds a task."""
	path = planPath(context)
	outputs = {gap["id"]: outputRun(context, gap) for gap in gaps}
	if any(id in gap["needed_by"] and gap["status"] == GapState.OPEN and not outputs[gap["id"]] for gap in gaps):
		return "Open"
	producers = {task for task in dependencies if tasks[task].meta.get("hints", {}).get("gate") == "approval"}
	for gap in gaps:
		if id in gap["needed_by"] and outputs[gap["id"]]:
			run = readReceipt(path / "runs" / outputs[gap["id"]])
			if run is None:
				return "Blocked"
			if not starting or run["task"] != id:
				producers.add(run["task"])
	for producer in producers:
		current = latestRun(path, producer, True)
		if current is None:
			return "Blocked"
		candidates = [gap for gap in gaps if outputs[gap["id"]] == current["id"]]
		if any(gap["status"] == GapState.OPEN for gap in candidates):
			return "Open"
		accepted = False
		for gap in candidates:
			if gap["status"] == GapState.DISMISSED:
				accepted = True
			elif gap["status"] == GapState.RESOLVED:
				decision = Knowledge(context).get(f"auto:{createPlan(path).id}/decision/{gap['decision']}")
				answer = decision.get("answer")
				approved = decision.get("author") == "human" and (answer is True or isinstance(answer, str) and answer.lower() in {"approve", "approved", "yes"})
				try:
					pinned = all(Resolve(context, pin["ref"]).digest == pin["digest"] for pin in gap.get("subjects", []))
				except AutoError:
					pinned = False
				accepted = accepted or approved and pinned
		if not accepted:
			return "Blocked"
	return None
