"""Module: events — Durable plan supervision and repair from source records."""

from __future__ import annotations

import json
import time

from auto.features.plan.model import PlanEvent, PlanEventKind, PlanState, RevisionOp, createPlan
from auto.features.plan.references import readReceipt, runDirectories
from auto.features.plan.store import journal, locked, planPath
from auto.runtime.model import RunStatus, TERMINAL
from auto.runtime.operations import Knowledge
from auto.utils.data import AutoError, atomicWrite, jsonText, readJson, timestamp


def eventRows(path):
	"""Read a complete event log, refusing corrupt history or reused sequences."""
	file = path / "events.jsonl"
	if not file.exists():
		return []
	try:
		rows = [json.loads(line) for line in file.read_text().splitlines() if line]
		previous = 0
		for row in rows:
			if type(row["seq"]) is not int or row["seq"] <= previous:
				raise ValueError("Invalid sequence")
			previous = row["seq"]
		return rows
	except (ValueError, KeyError, TypeError) as error:
		raise AutoError("Invalid plan event log; refusing to overwrite history", "system") from error


def eventKey(event):
	"""Identify the durable source of an event without exposing an extra key."""
	data = event["data"]
	return (event["kind"], event["ref"], data.get("revision"), data.get("gap"), data.get("run"), data.get("status"), data.get("continuation"))


def recordEvent(context, path, kind, ref, data, *, date=None, author=None):
	"""Publish an event once, after its source is durable; never reuse a sequence."""
	with locked(path):
		rows = eventRows(path)
		candidate = {"kind": kind, "ref": ref, "data": data}
		key = eventKey(candidate)
		for row in rows:
			if eventKey(row) == key:
				return row
		sequence = max(readJson(path / ".event-seq.json", 0), rows[-1]["seq"] if rows else 0) + 1
		row = PlanEvent(sequence, date or timestamp(), kind, ref, data, author or context.author).asDict()
		# Reserve the number first: a crash can leave a gap, never a reused cursor.
		atomicWrite(path / ".event-seq.json", jsonText(sequence))
		old = (path / "events.jsonl").read_text() if (path / "events.jsonl").exists() else ""
		atomicWrite(path / "events.jsonl", old + jsonText(row) + "\n")
		context.runtime.planEventRecorded(path)
		return row


def revisionEvents(context, path, row):
	"""Project a committed revision and its knowledge changes to events."""
	base = "auto:" + createPlan(path).id
	result = [(PlanEventKind.REVISED, f"{base}/revision/{row['id']}",
		{"revision": row["id"], "operations": row["operations"]})]
	for op in row["operations"]:
		data = {"revision": row["id"]}
		if op["op"] in {RevisionOp.RAISE, RevisionOp.RESOLVE, RevisionOp.DISMISS}:
			ref = f"{base}/gap/{op['gap']}"
			data["gap"] = op["gap"]
			if op["op"] == RevisionOp.RAISE:
				gap = Knowledge(context).get(ref)
				data.update(kind=gap.get("gap_kind", gap.get("kind")), needed_by=gap["needed_by"])
				kind = PlanEventKind.GAP_RAISED
			elif op["op"] == RevisionOp.RESOLVE:
				data["decision"], kind = op["decision"], PlanEventKind.GAP_RESOLVED
			else:
				kind = PlanEventKind.GAP_DISMISSED
		elif op["op"] == RevisionOp.RECORD:
			ref, kind = f"{base}/decision/{op['decision']}", PlanEventKind.DECISION_RECORDED
			data["decision"] = op["decision"]
		elif op["op"] in {RevisionOp.HOLD, RevisionOp.RELEASE}:
			ref, kind = base, PlanEventKind.HELD if op["op"] == RevisionOp.HOLD else PlanEventKind.RELEASED
			data.update({key: op[key] for key in ("mode", "reason") if key in op})
		else:
			continue
		result.append((kind, ref, data))
	return [{"kind": kind, "ref": ref, "data": data, "date": row["date"], "author": row["author"]} for kind, ref, data in result]


def recordRevision(context, path, row):
	"""Record a newly durable revision and any resulting plan end."""
	for event in revisionEvents(context, path, row):
		recordEvent(context, path, **event)
	recordPlanStatus(context, path)


def recordPlanStatus(context, path):
	"""Record a transition into an end state, without repeating ended decisions."""
	from auto.features.plan.operations import PlanStatus
	status = PlanStatus(context, path)
	previous = readJson(path / ".event-status.json", {})
	transitions = previous.get("transitions", [])
	state = {"status": status["status"], "transitions": transitions}
	if status["status"] in {PlanState.SUCCEEDED, PlanState.FAILED, PlanState.ABANDONED}:
		root = next((readReceipt(directory) for directory in reversed(runDirectories(path))
			if readReceipt(directory).get("task") == "root"), {})
		data = previous.get("ended") if previous.get("status") == status["status"] else None
		data = data or {"status": status["status"], "revision": status["revision"], "run": root.get("id")}
		if not any(event["data"] == data for event in transitions):
			transitions.append({"kind": PlanEventKind.ENDED, "ref": "auto:" + createPlan(path).id,
				"data": data, "date": timestamp(), "author": context.author})
		state["ended"] = data
	# Keep a durable projection checkpoint before publishing the transition event.
	atomicWrite(path / ".event-status.json", jsonText(state))
	for event in transitions:
		recordEvent(context, path, **event)


def recordRun(context, path, run):
	"""Project a durable run creation, waiting state or terminal receipt."""
	base = "auto:" + createPlan(path).id
	ref = f"{base}/run/{run['id']}"
	recordEvent(context, path, PlanEventKind.RUN_STARTED, ref, {"run": run["id"], "task": run["task"]},
		date=run["started"], author=run.get("author", context.author))
	if run["status"] == RunStatus.WAITING:
		recordEvent(context, path, PlanEventKind.RUN_WAITING, ref, {"run": run["id"], "gap": run["gap"]}, author=run["id"])
	elif run["status"] in TERMINAL:
		recordEvent(context, path, PlanEventKind.RUN_ENDED, ref,
			{"run": run["id"], "status": run["status"], "usage": run["usage"], "failure": run.get("failure")},
			date=run["ended"], author=run["id"])
	recordPlanStatus(context, path)


def repairEvents(context, path):
	"""Append events missing for durable revisions and runs, in record order."""
	with locked(path):
		candidates = [event for row in journal(path) for event in revisionEvents(context, path, row)]
		candidates.extend(readJson(path / ".event-status.json", {}).get("transitions", []))
		base = "auto:" + createPlan(path).id
		for file in sorted((path / "continuations").glob("*.json")):
			value = readJson(file)
			candidates.append({"kind": PlanEventKind.CONTINUATION_STARTED, "ref": base, "date": value["started"],
				"data": {"continuation": value["id"]}, "author": value["id"]})
			if value.get("ended"):
				candidates.append({"kind": PlanEventKind.CONTINUATION_STOPPED, "ref": base, "date": value["ended"],
					"data": {"continuation": value["id"], "stop": value["stop"], "status": value.get("plan_status"),
						**({"error": value["error"]} if value.get("error") else {})}, "author": value["id"]})
		for directory in runDirectories(path):
			run = readReceipt(directory)
			ref = f"{base}/run/{run['id']}"
			candidates.append({"kind": PlanEventKind.RUN_STARTED, "ref": ref, "date": run["started"],
				"data": {"run": run["id"], "task": run["task"]}, "author": run.get("author", "human")})
			file = directory / "journal.jsonl"
			for event in [json.loads(line) for line in file.read_text().splitlines()] if file.exists() else []:
				if event["type"] == "GapRaised":
					gap = Knowledge(context).get(f"{base}/gap/{event['gap']}")
					date = next((row["date"] for row in journal(path) if row["id"] == gap["revision"]), run["started"])
					candidates.append({"kind": PlanEventKind.RUN_WAITING, "ref": ref, "date": date,
						"data": {"run": run["id"], "gap": event["gap"]}, "author": run["id"]})
			if run["status"] == RunStatus.WAITING:
				candidates.append({"kind": PlanEventKind.RUN_WAITING, "ref": ref, "date": run["started"],
					"data": {"run": run["id"], "gap": run["gap"]}, "author": run["id"]})
			elif run["status"] in TERMINAL:
				candidates.append({"kind": PlanEventKind.RUN_ENDED, "ref": ref, "date": run["ended"],
					"data": {"run": run["id"], "status": run["status"], "usage": run["usage"], "failure": run.get("failure")}, "author": run["id"]})
		for event in sorted(candidates, key=lambda item: item["date"]):
			recordEvent(context, path, **event)
		recordPlanStatus(context, path)


def PlanEvents(context, plan=None, since: int | None = None, follow: bool = False):
	"""Stream events after a sequence cursor, optionally following new appends."""
	if since is not None and (type(since) is not int or since < 0):
		raise AutoError("since must be a non-negative event sequence")
	if type(follow) is not bool:
		raise AutoError("follow must be a boolean")
	path, cursor = planPath(context, plan), since or 0
	while not context.cancelled():
		with locked(path):
			rows = [row for row in eventRows(path) if row["seq"] > cursor]
		for row in rows:
			cursor = row["seq"]
			yield row
		if not follow:
			return
		time.sleep(.05)
