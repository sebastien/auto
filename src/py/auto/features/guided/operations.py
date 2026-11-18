"""Module: guided — The goal-to-result loop: plan, confirm, run, ask and summarise.

These operations compose the plan, gap and continuation operations into the
few steps a person takes: start from a goal, continue, answer what the plan
needs, change it, pause or resume it. Questions are asked through
`context.question` when the interface has a person to ask; otherwise the
loop stops where it needs one, as a continuation does.
"""

from __future__ import annotations

import json
from pathlib import Path

from auto.features.elaboration.operations import TRIAGED, PlanCreate, PlanRefine
from auto.features.orchestration.operations import PlanContinue
from auto.features.plan.control import PlanHold, PlanRelease, planHold
from auto.features.plan.events import PlanEvents, eventRows
from auto.features.plan.gaps import GapDecide, GapList, planRef
from auto.features.plan.model import ContinuationStop, GapState, PlanEventKind, RevisionOp, createPlan
from auto.features.plan.operations import PlanStatus, tasksAt
from auto.features.plan.policy import PlanApprove
from auto.features.plan.store import journal, planPath
from auto.features.task.operations import RunAnswer, RunGet, RunList, RunOutput, TaskStart
from auto.features.usage.operations import UsageModels, UsageStages
from auto.runtime.model import Event, RunStatus
from auto.runtime.operations import Knowledge
from auto.utils.data import AutoError
from auto.utils.layout import setCurrentPlan, storePath, taskId
from auto.utils.workspace import changes, snapshot

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------

ANSWERED = "Answered interactively"
# Events the guided loop adds to the plan events it streams.
PROPOSED = "PlanProposed"
SUMMARY = "PlanSummary"
# The question asking whether to run a proposed plan: answered `run`, `change` or `no`.
CONFIRM = "PlanConfirm"
DECLINED = "declined"
# Why a guided plan stopped before it had tasks: its planning run did not succeed.
UNPLANNED = "unplanned"

# -----------------------------------------------------------------------------
#
# UTILITIES
#
# -----------------------------------------------------------------------------


def openQuestions(context, path) -> list:
	"""Return the open gaps of a plan, in the order they were raised."""
	return GapList(context, path, filter={"status": GapState.OPEN})


def answerValue(gap: dict, answer):
	"""Turn a yes to a runtime policy request into the grant its rule requires."""
	policy = gap.get("runtime_policy")
	if policy and answer is True and policy.get("rule") != "confirm":
		return {"grant": policy["effect"], "scope": policy["scope"]}
	return answer


def askGap(context, gap: dict) -> dict:
	"""Ask the person for the answer to one gap and record it as a decision."""
	run = next((run for run in RunList(context, {"status": RunStatus.WAITING}) if run.get("gap") == gap["id"]), None)
	single = False
	if run and isinstance(run.get("question"), dict):
		event = run["question"]
	else:
		event = {"type": Event.SCRIPT_QUESTION, "content": gap["question"]}
		if gap.get("fields"):
			event["fields"] = gap["fields"]
		elif gap.get("options"):
			event["fields"] = [{"id": "answer", "label": "Answer", "options": gap["options"], "custom": True}]
			single = True
	answer = context.question(event)
	if single and isinstance(answer, dict):
		answer = answer.get("answer")
	return GapDecide(context, gap["id"], answerValue(gap, answer), ANSWERED)


def answerOpen(context, path) -> int:
	"""Ask for every open gap of a plan; return how many were answered."""
	count = 0
	for gap in openQuestions(context, path):
		askGap(context, gap)
		count += 1
	return count


def settle(context, run: dict) -> dict:
	"""Answer the questions of a waiting run (eg. an elaboration) while a person can be asked."""
	while run["status"] == RunStatus.WAITING and context.question:
		gap = next((gap for gap in openQuestions(context, context.plan) if gap["id"] == run.get("gap")), {})
		RunAnswer(context, run, answerValue(gap, context.question(run["question"])), ANSWERED)
		run = RunGet(context, run)
	return run


def proposal(context, path) -> dict:
	"""Describe a plan for a person deciding whether to run it."""
	status = PlanStatus(context, path)
	details = status["taskDetails"]
	tasks = [{"id": id, "goal": details[id]["goal"], "dependencies": details[id]["dependencies"], "parent": details[id]["parent"]}
		for id in status["tasks"] if id != "root"]
	return {"id": status["id"], "goal": status["goal"], "path": status["path"], "tasks": tasks, "budget": status["budget"],
		"effects": status["policy"]["tasks"]["root"]["effects"] if not tasks else [],
		"questions": [gap["question"] for gap in status["gaps"]], "agents": createPlan(path).meta.get("agents", [])}


def failureText(failure) -> str:
	"""Return why a run failed, in a line: validation violations are listed by their messages."""
	reason = (failure or {}).get("reason") if isinstance(failure, dict) else failure
	try:
		violations = json.loads(reason) if isinstance(reason, str) else reason
	except ValueError:
		violations = None
	if isinstance(violations, list) and violations and all(isinstance(item, dict) and "message" in item for item in violations):
		return "; ".join(dict.fromkeys(str(item["message"]) for item in violations))
	return excerpt(reason or "", 300)


def excerpt(value, size: int = 160) -> str:
	"""Return the first meaningful line of an output, clipped to `size`."""
	text = value if isinstance(value, str) else str(value)
	line = next((line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("---")), "")
	return line if len(line) <= size else line[:size - 1] + "…"


def drive(context, path, before: dict | None = None):
	"""Continue a plan, asking for answers whenever it waits on them, then summarise with what changed since `before`."""
	before = before or snapshot(context.project)
	# The person running a guided plan approves it: the `confirm` effects of its tasks are granted.
	PlanApprove(context, path)
	while True:
		stop, reason = None, None
		for event in PlanContinue(context, path):
			yield event
			if event["kind"] == PlanEventKind.CONTINUATION_STOPPED:
				stop = event["data"]["stop"]
				reason = failureText(event["data"].get("error")) or None
		if stop == ContinuationStop.WAITING and context.question and answerOpen(context, path):
			continue
		# Tasks added by a refinement while it ran are part of the plan the person is running.
		if stop == ContinuationStop.BLOCKED and PlanApprove(context, path):
			continue
		break
	yield {"kind": SUMMARY, "data": PlanSummary(context, path, stop, changes(context.project, before), reason=reason)}

# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------


def PlanDo(context, goal: str, budget: dict | None = None, effects: list | None = None, agents: list | None = None, yes: bool = False,
	route: str | None = None):
	"""Plan a goal, confirm it with the person, run it to the end or until it needs them, and summarise.

	Triage first decides whether the goal runs directly, as one task, or is planned; `route` decides instead."""
	created = PlanCreate(context, goal, budget=budget, effects=effects, agents=agents, approval="plan", route=route or TRIAGED)
	path = Path(created["plan"])
	context = context.derive(plan=path)
	if created["run"] is not None:
		run = settle(context, created["run"])
		if run["status"] not in {RunStatus.SUCCEEDED, RunStatus.WAITING}:
			yield {"kind": SUMMARY, "data": PlanSummary(context, path, UNPLANNED, reason=failureText(run.get("failure")) or run["status"])}
			return
	triage = created.get("triage") or {}
	while True:
		if context.question:
			answerOpen(context, path)
		yield {"kind": PROPOSED, "data": {**proposal(context, path), "route": triage.get("route"), "reason": triage.get("reason")}}
		# Once changed, the goal is planned: the proposal no longer reflects the triage.
		triage = {}
		if yes or not context.question:
			break
		choice = context.question({"type": CONFIRM, "content": "Run it?"})
		if choice == "run":
			break
		if choice != "change":
			yield {"kind": SUMMARY, "data": PlanSummary(context, path, DECLINED)}
			return
		change = context.question({"type": Event.SCRIPT_QUESTION, "content": "What should change?"})
		refined = PlanRefine(context, path, str(change))
		settle(context, refined["run"])
	yield from drive(context, path)


def PlanGo(context, plan, task: str | None = None):
	"""Continue a plan until it ends or needs a person, asking them on the way; `task` first runs one task again."""
	path = planPath(context, plan)
	context = context.derive(plan=path)
	before = snapshot(context.project)
	if task:
		PlanApprove(context, path)
		cursor = (eventRows(path) or [{"seq": 0}])[-1]["seq"]
		TaskStart(context.derive(question=None), task)
		yield from PlanEvents(context, path, since=cursor)
	if context.question:
		answerOpen(context, path)
	yield from drive(context, path, before)


def PlanResume(context, plan, reason: str | None = None):
	"""Release a paused or stopped plan and continue it."""
	path = planPath(context, plan)
	if planHold(path):
		PlanRelease(context.derive(plan=path), path, reason or "Resumed")
	yield from PlanGo(context, path)


def PlanPause(context, plan, reason: str | None = None) -> dict:
	"""Pause a plan once its current task finishes."""
	return PlanHold(context, plan, "Pause", reason or "Paused")


def PlanStop(context, plan, reason: str | None = None) -> dict:
	"""Stop a plan now, cancelling its current task."""
	return PlanHold(context, plan, "Stop", reason or "Stopped")


def PlanUse(context, id: str) -> dict:
	"""Make a plan of the project the one later commands act on."""
	path = planPath(context, id)
	if path.parent != storePath(context.project).resolve():
		raise AutoError(f"Not a plan of this project: {id}", recovery="Choose one listed by auto plans.")
	setCurrentPlan(context.project, path)
	plan = createPlan(path)
	return {"id": plan.id, "goal": plan.goal.strip().partition("\n")[0], "path": str(path)}


def PlanSummary(context, plan, stop: str | None = None, changed: list | None = None, reason: str | None = None) -> dict:
	"""Summarise progress, cost, decisions, results, files `changed`, and the `reason` for stopping."""
	path = planPath(context, plan)
	context = context.derive(plan=path)
	status = PlanStatus(context, path)
	tasks = [id for id in status["tasks"] if id != "root"] or ["root"]
	# A parent's output only gathers its children's: the results are those of the tasks that did the work.
	parents = {detail["parent"] for detail in status["taskDetails"].values()}
	results = []
	for id in tasks:
		run = status["taskDetails"][id]["run"]
		if run and run["status"] == RunStatus.SUCCEEDED and (id not in parents or id == "root"):
			try:
				output = excerpt(RunOutput(context, run["id"]))
			except (AutoError, OSError, ValueError):
				output = ""
			results.append({"task": id, "goal": status["taskDetails"][id]["goal"], "output": output})
	decided = planDecisions(context, path)
	return {"id": status["id"], "goal": status["goal"], "path": status["path"], "status": status["status"], "stop": stop, "reason": reason,
		"done": sum(status["tasks"][id] == RunStatus.SUCCEEDED for id in tasks), "total": len(tasks), "usage": status["usage"],
		"budget": status["budget"], "decisions": len(decided), "decided": decided, "questions": [gap["question"] for gap in status["gaps"]],
		"results": results, "changed": changed, "failed": [id for id in tasks if status["tasks"][id] in {RunStatus.FAILED, RunStatus.TIMED_OUT}],
		"hold": status["hold"], "agents": createPlan(path).meta.get("agents", []), "spending": UsageStages(context, path.name),
		"models": UsageModels(context, path.name), "written": writtenFiles(context)}


def writtenFiles(context) -> list:
	"""Return the files the runs of the current plan recorded writing, in the order first written."""
	runs = sorted(RunList(context), key=lambda run: run.get("started") or "")
	return list(dict.fromkeys(name for run in runs for name in (run.get("files") or {}).get("written") or []))


def planDecisions(context, path) -> list:
	"""Return the decisions of a plan in the order they were made: the question, its answer and who answered."""
	base, store, result = planRef(path), Knowledge(context), []
	for row in journal(path):
		for op in row["operations"]:
			if op["op"] not in {RevisionOp.RESOLVE, RevisionOp.RECORD}:
				continue
			try:
				decision = store.get(f"{base}/decision/{op['decision']}")
				question = store.get(f"{base}/gap/{op['gap']}").get("question", "") if op.get("gap") else decision.get("question", "")
			except (AutoError, OSError, ValueError):
				decision, question = {}, ""
			result.append({"decision": op["decision"], "gap": op.get("gap"), "question": question, "answer": decision.get("answer"),
				"author": decision.get("author", row.get("author"))})
	return result


def TaskShow(context, task: str) -> dict:
	"""Show a task: what it does, where it stands, its latest attempt and output."""
	path = planPath(context)
	context = context.derive(plan=path)
	id = taskId(task)
	tasks = tasksAt(path)
	if id not in tasks:
		raise AutoError(f"Task not found: {task}", recovery="Run auto status to see the plan's tasks.")
	status = PlanStatus(context, path)
	detail = status["taskDetails"][id]
	runs = sorted((run for run in RunList(context) if run["task"] == id), key=lambda run: run.get("started") or "")
	latest = runs[-1] if runs else None
	output = None
	if latest and latest["status"] in {RunStatus.SUCCEEDED, RunStatus.FAILED}:
		try:
			output = RunOutput(context, latest["id"])
		except (AutoError, OSError, ValueError):
			output = None
	return {"id": id, "goal": detail["goal"], "state": status["tasks"][id], "description": tasks[id].description or tasks[id].sections.get("Process", ""),
		"parent": detail["parent"], "dependencies": detail["dependencies"], "attempts": len(runs),
		"latest": {key: latest[key] for key in ("id", "status", "failure", "question", "usage", "started", "ended", "files") if key in latest} if latest else None,
		"output": output, "questions": [gap["question"] for gap in status["gaps"] if id in gap["needed_by"]]}


def QuestionAnswer(context, number: str | None = None, answer=None, rationale: str | None = None):
	"""Answer an open question of the plan, by number or id; without one, ask every open question."""
	path = planPath(context)
	context = context.derive(plan=path)
	gaps = openQuestions(context, path)
	if number is None:
		if not context.question:
			raise AutoError("Give the number of the question to answer and its answer",
				recovery="Run auto status to see the open questions, then auto answer NUMBER ANSWER.")
		return {"answered": answerOpen(context, path), "remaining": len(openQuestions(context, path))}
	number = str(number)
	gap = gaps[int(number) - 1] if number.isdigit() and 0 < int(number) <= len(gaps) else next((gap for gap in gaps if gap["id"] == number), None)
	if gap is None:
		raise AutoError(f"No open question {number}", recovery="Run auto status to see the open questions." if gaps else "This plan has no open question.")
	if answer is None:
		if not context.question:
			raise AutoError("Give the answer to the question", recovery=f"Run auto answer {number} ANSWER.")
		decision = askGap(context, gap)
	else:
		decision = GapDecide(context, gap["id"], answerValue(gap, answer), rationale or "")
	return {"question": gap["question"], "answer": decision["answer"], "decision": decision["id"], "remaining": len(gaps) - 1}


def LearningStatus(context) -> list:
	"""Summarise what each memory agent of the project has consolidated and what awaits it."""
	from auto.features.memory.config import agentsFor
	from auto.features.memory.consolidation import ConsolidationStatus
	result = []
	for name in agentsFor(context):
		agent = "auto:agent/" + name
		try:
			status = ConsolidationStatus(context, agent)
		except (AutoError, OSError, ValueError) as error:
			result.append({"agent": agent, "error": str(error)})
			continue
		latest = status["latest"] or {}
		result.append({"agent": agent, "consolidated": latest.get("ended") or latest.get("started"), "status": latest.get("status"),
			"pending": bool(status["pending"]), "due": sum(len(phase.get("items", [])) for phase in status["due"].get("phases", [])),
			"questions": len(status["questions"]), "proposals": len(status["proposals"])})
	return result

# EOF
