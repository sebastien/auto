"""Module: gaps — Memo-backed decisions and idempotent journal reconciliation."""

from __future__ import annotations

from auto.features.plan.model import Decision, Gap, Plan
from auto.features.plan.store import commit, journal, locked, planPath, recover
from auto.runtime.model import RunStatus
from auto.runtime.operations import Knowledge, Resolve
from auto.utils.data import AutoError, identity, readJson, timestamp


def planRef(path):
	return "auto:" + Plan.read(path).id


def GapList(context, plan=None, filter: dict | None = None) -> list:
	path = planPath(context, plan)
	if context.knowledge is None and not (context.project / ".memo").exists():
		return []
	result = [Gap.fromDict(gap).asDict() for gap in Knowledge(context).list(planRef(path), "gap")]
	for gap in result:
		gap["kind"] = gap.get("gap_kind", gap.get("kind"))
	return [gap for gap in result if all((value in gap["needed_by"] if key == "task" else gap.get(key) == value) for key, value in (filter or {}).items())]


def prepareGap(context, path, gap: dict, revision: str, staged: dict | None = None) -> dict:
	id = identity()
	kind = gap.get("kind", gap.get("gap_kind", "information"))
	if kind not in {"input", "information", "decision", "approval"} or not gap.get("question") or not gap.get("needed_by"):
		raise AutoError("Gap requires a valid kind, question and needed_by tasks")
	if kind == "approval" and not gap.get("subjects"):
		raise AutoError("Approval requires pinned subjects")
	for subject in gap.get("subjects", []):
		expected = (staged or {}).get(subject["ref"])
		if (expected if expected is not None else Resolve(context, subject["ref"]).digest) != subject["digest"]:
			raise AutoError("Approval subject has changed")
	return Gap(id=id, identity=f"{planRef(path)}/gap/{id}", plan=planRef(path), kind=kind, question=gap["question"],
		neededBy=list(gap["needed_by"]), revision=revision, raisedBy=gap.get("raised_by", context.author),
		subjects=list(gap.get("subjects", [])), options=list(gap.get("options", [])),
		extra={key: value for key, value in gap.items() if key not in {"kind", "question", "needed_by", "subjects", "options", "raised_by"}}).asDict()


def GapRaise(context, plan, gap: dict) -> str:
	path = planPath(context, plan)
	with locked(path):
		recover(context, path)
		repairLocked(context, path)
		revision = identity()
		value = prepareGap(context, path, gap, revision)
		commit(context, path, [{"op": "raise", "gap": value["id"]}], records=[{"kind": "gap", "value": value}], revision=revision, reason=value["question"])
	return value["id"]


def repairLocked(context, path) -> None:
	if context.knowledge is None and not (context.project / ".memo").exists():
		return
	store = Knowledge(context)
	rows = journal(path)
	seen = {(op["op"], op.get("gap")) for row in rows for op in row["operations"]}
	decisions = store.list(planRef(path), "decision")
	repairs = {}
	def stage(revision, operation, record=None):
		group = repairs.setdefault(revision, {"operations": [], "records": []})
		group["operations"].append(operation)
		if record:
			group["records"].append(record)
	for gap in store.list(planRef(path), "gap"):
		if ("raise", gap["id"]) not in seen:
			stage(gap["revision"], {"op": "raise", "gap": gap["id"]})
		matching = [decision for decision in decisions if decision["gap"] == gap["id"]]
		if len(matching) > 1:
			raise AutoError("Conflicting decisions require reconciliation")
		if matching and ("resolve", gap["id"]) not in seen:
			decision = matching[0]
			value = {**gap, "status": "Resolved", "decision": decision["id"], "resolved_revision": decision["revision"]}
			stage(decision["revision"], {"op": "resolve", "gap": gap["id"], "decision": decision["id"]},
				{"kind": "gap", "value": value, "expected": gap["digest"], "base": gap})
		elif gap["status"] == "Dismissed" and ("dismiss", gap["id"]) not in seen:
			stage(gap["resolved_revision"], {"op": "dismiss", "gap": gap["id"]})
	for revision, group in repairs.items():
		commit(context, path, group["operations"], records=group["records"], revision=revision if not any(row["id"] == revision for row in rows) else None,
			reason="Repair Memo records", repairedRevision=revision)


def PlanRepair(context, plan=None) -> dict:
	path = planPath(context, plan)
	context = context.derive(plan=path)
	with locked(path):
		recover(context, path)
		repairLocked(context, path)
	from auto.features.task.operations import resumeGap, RunList, RunOutput, runDirectory
	for gap in GapList(context, path, {"status": "Resolved"}):
		decision = Knowledge(context).get(f"{planRef(path)}/decision/{gap['decision']}")
		resumeGap(context, gap["id"], decision["answer"])
	from auto.features.elaboration.operations import ElaborationApply, ElaborationParse
	for run in RunList(context, {"status": "Succeeded"}):
		state = readJson(runDirectory(context, run) / "state.json", {})
		if state.get("applyElaboration"):
			ElaborationApply(context, path, ElaborationParse(context, RunOutput(context, run)), run)
	return {"revision": (journal(path) or [{}])[-1].get("id")}


def GapDecide(context, gap: str, answer, rationale: str) -> dict:
	path = planPath(context)
	store = Knowledge(context)
	with locked(path):
		recover(context, path)
		repairLocked(context, path)
		value = store.get(f"{planRef(path)}/gap/{gap}")
		if value["status"] != "Open":
			raise AutoError("Gap is not open")
		if value.get("runtime_policy") and context.author != "human":
			raise AutoError("Runtime policy requests require a human decision")
		for pin in value.get("subjects", []):
			if Resolve(context, pin["ref"]).digest != pin["digest"]:
				raise AutoError("Pinned subject changed; raise a new approval gap")
		revision, id = identity(), identity()
		decision = Decision(id=id, identity=f"{planRef(path)}/decision/{id}", plan=planRef(path), gap=gap, answer=answer,
			rationale=rationale, author=context.author, date=timestamp(), revision=revision).asDict()
		updated = {**value, "status": "Resolved", "decision": id, "resolved_revision": revision}
		operations = [{"op": "resolve", "gap": gap, "decision": id}]
		request = value.get("runtime_policy")
		if request and request["rule"] == "deny" and answer == {"grant": request["effect"], "scope": request["scope"]}:
			operations.append({"op": "grant", "effect": request["effect"], "scope": request["scope"], "source": decision["identity"]})
		commit(context, path, operations, revision=revision, reason=rationale,
			records=[{"kind": "decision", "value": decision}, {"kind": "gap", "value": updated, "expected": value["digest"], "base": value}])
	# Resume only after leaving the commit lock; model checks can call Auto themselves.
	from auto.features.task.operations import resumeGap, runDirectory
	result = resumeGap(context, gap, answer)
	if result and result["status"] == RunStatus.SUCCEEDED:
		state = readJson(runDirectory(context, result) / "state.json", {})
		if state.get("applyElaboration"):
			from auto.features.elaboration.operations import ElaborationApply, ElaborationParse
			from auto.features.task.operations import RunOutput
			ElaborationApply(context, context.plan, ElaborationParse(context, RunOutput(context, result)), result)
	return decision


def GapDismiss(context, gap: str, rationale: str) -> dict:
	path = planPath(context)
	with locked(path):
		recover(context, path)
		repairLocked(context, path)
		value = Knowledge(context).get(f"{planRef(path)}/gap/{gap}")
		if value["status"] != "Open":
			raise AutoError("Gap is not open")
		revision = identity()
		updated = {**value, "status": "Dismissed", "rationale": rationale, "resolved_revision": revision}
		return commit(context, path, [{"op": "dismiss", "gap": gap}], records=[{"kind": "gap", "value": updated, "expected": value["digest"], "base": value}], revision=revision, reason=rationale)

# EOF
