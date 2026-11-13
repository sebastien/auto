"""Module: control — Human holds on plan progression, recorded as revisions."""

from __future__ import annotations

from contextlib import ExitStack

from auto.features.plan.events import recordEvent
from auto.features.plan.model import ContinuationStop, Hold, PlanEventKind, RevisionOp, createPlan
from auto.features.plan.references import readReceipt, runDirectories
from auto.features.plan.store import exclusive, journal, locked, planPath
from auto.utils.data import AutoError, atomicWrite, jsonText, readJson, timestamp


def saveContinuation(path, value):
	"""Preserve each continuation for event repair and expose the latest facet."""
	with locked(path):
		atomicWrite(path / "continuations" / (value["id"] + ".json"), jsonText(value))
		atomicWrite(path / "continuation.json", jsonText(value))


def reconcileContinuations(context, path, *, lockHeld=False):
	"""Close interrupted continuations only after proving the plan lock is free."""
	if not (path / "continuations").is_dir():
		return
	from auto.features.plan.operations import PlanStatus
	with ExitStack() as locks:
		if not lockHeld:
			try:
				locks.enter_context(exclusive(path / "continue.lock", "This plan"))
			except AutoError:
				return
		with locked(path):
			for file in sorted((path / "continuations").glob("*.json")):
				value = readJson(file)
				if value.get("ended") is None:
					owned = sorted((readReceipt(directory) for directory in runDirectories(path)), key=lambda run: run["started"])
					for run in owned:
						if run.get("continuation") == value["id"] and run["id"] not in value["runs"]:
							value["runs"].append(run["id"])
					value.update(ended=timestamp(), stop=ContinuationStop.CANCELLED, plan_status=PlanStatus(context, path)["status"])
					saveContinuation(path, value)
					recordEvent(context, path, PlanEventKind.CONTINUATION_STARTED, "auto:" + createPlan(path).id,
						{"continuation": value["id"]}, date=value["started"], author=value["id"])
					recordEvent(context, path, PlanEventKind.CONTINUATION_STOPPED, "auto:" + createPlan(path).id,
						{"continuation": value["id"], "stop": value["stop"], "status": value["plan_status"]}, date=value["ended"], author=value["id"])


def planHold(path):
	"""Derive the current hold from the authoritative plan journal."""
	hold = None
	for row in journal(path):
		for op in row["operations"]:
			if op["op"] == RevisionOp.HOLD:
				hold = Hold(op["mode"], row["author"], op["reason"], row["id"]).asDict()
			elif op["op"] == RevisionOp.RELEASE:
				hold = None
	return hold


def PlanHold(context, plan, mode: str, reason: str):
	"""Pause a plan, or stop it and cancel its current runs."""
	from auto.features.plan.operations import PlanRevise
	path = planPath(context, plan)
	return PlanRevise(context.derive(plan=path), path, [{"op": RevisionOp.HOLD, "mode": mode, "reason": reason}], reason)


def PlanRelease(context, plan, reason: str):
	"""Release a hold without starting a continuation or run."""
	from auto.features.plan.operations import PlanRevise
	return PlanRevise(context, plan, [{"op": RevisionOp.RELEASE, "reason": reason}], reason)
