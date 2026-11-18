"""Module: continuation — Sequential plan progression with durable supervision."""

from __future__ import annotations

import os
import threading
from contextlib import ExitStack
from queue import Empty, SimpleQueue

from auto.features.elaboration.operations import PlanRefine
from auto.features.plan.control import planHold, reconcileContinuations, saveContinuation
from auto.features.plan.events import PlanEvents, eventRows, recordEvent
from auto.features.plan.gaps import applyPending
from auto.features.plan.model import Continuation, ContinuationStop, GapState, PlanEventKind, PlanState, createPlan
from auto.features.plan.operations import PlanReady, PlanStatus, dependencyGraph, tasksAt
from auto.features.plan.policy import PolicyResolve
from auto.features.plan.references import latestRun, readReceipt, runDirectories
from auto.features.plan.store import exclusive, journal, locked, planPath, recover
from auto.features.task.operations import RunList, TaskStart, effectiveTask, hasCheck, reconcileRunWorkers, resumeGap
from auto.runtime.model import Limits, RunStatus, budgetValue
from auto.runtime.operations import Knowledge
from auto.utils.data import AutoError, identity, jsonText, readJson, timestamp
from auto.utils.layout import runsPath


def requiresWorkspace(context, tasks):
	"""Determine whether the declared work or grants include repository writes."""
	effects = {effect for task in tasks.values() for effect in task.meta.get("effects", [])}
	base = "auto:" + createPlan(context.plan).id
	scopes = {base} | {base + "/task/" + id for id in tasks}
	effects.update(grant["effect"] for grant in PolicyResolve(context)["grants"] if grant["scope"] in scopes)
	return any(effect in {"repo", "repo.write"} or effect.startswith("repo.write.") for effect in effects)


def taskOrder(path, tasks):
	"""Return journal addition order; readiness already imposes dependencies first."""
	order = {}
	for row in journal(path):
		for op in row["operations"]:
			if op["op"] in {"add", "replace"}:
				order.setdefault(op["task"], len(order))
	return {task: order.get(task, len(order) + index) for index, task in enumerate(tasks)}


def timeoutCount(context, path):
	"""Read a positive consecutive-timeout threshold from config and plan settings."""
	settings = context.defaults.get("orchestration", {})
	overrides = createPlan(path).meta.get("orchestration", {})
	if not isinstance(settings, dict) or not isinstance(overrides, dict):
		raise AutoError("orchestration must be a mapping")
	count = overrides.get("timeouts", settings.get("timeouts", 2))
	if type(count) is not int or count < 1:
		raise AutoError("orchestration.timeouts must be a positive integer")
	return count


def pendingStop(context, tasks, graph, status):
	"""Classify an idle plan, giving failed dependencies precedence over gaps."""
	remaining = {id for id in tasks if status["tasks"][id] != RunStatus.SUCCEEDED}
	failed = {id for id in tasks if (latestRun(context.plan, id) or {}).get("status") in {RunStatus.FAILED, RunStatus.TIMED_OUT}}
	if failed & remaining or any(graph[id] & failed for id in remaining):
		return ContinuationStop.FAILED
	if any(set(gap["needed_by"]) & remaining for gap in status["gaps"]):
		return ContinuationStop.WAITING
	return ContinuationStop.BLOCKED


def PlanContinue(context, plan):
	"""Run ready work, streaming durable events through the final stop event."""
	path = planPath(context, plan)
	context = context.derive(plan=path, question=None)
	threshold = timeoutCount(context, path)
	value = Continuation(identity("cnt"), timestamp()).asDict()
	cancel = threading.Event()
	worker, outcome = None, {}
	cursor, timeouts, attempted, refined = 0, 0, set(), set()
	stop, error, finalEvents = ContinuationStop.CANCELLED, None, []
	callerCancelled = context.cancelled
	# Keep live engine feedback on the caller thread, separate from durable plan events.
	progress = SimpleQueue()
	workerContext = context.derive(author=value["id"], observe=progress.put if context.observe else None, tick=None,
		variables={**context.variables, "continuation": value["id"]}, cancelled=lambda: cancel.is_set() or callerCancelled())

	def report():
		while context.observe:
			try:
				event = progress.get_nowait()
			except Empty:
				break
			context.observe(event)
		if context.tick:
			context.tick()

	def events():
		nonlocal cursor
		for event in PlanEvents(context.derive(cancelled=lambda: False), since=cursor):
			cursor = event["seq"]
			if event["kind"] == PlanEventKind.RUN_STARTED:
				run = readReceipt(runsPath(path) / event["data"]["run"])
				if run.get("continuation") == value["id"] and run["id"] not in value["runs"]:
					value["runs"].append(run["id"])
					saveContinuation(path, value)
			yield event

	def perform(action):
		nonlocal worker
		outcome.clear()
		def execute():
			try:
				outcome["result"] = action()
			except BaseException as failure:
				outcome["error"] = failure
		worker = threading.Thread(target=execute)
		worker.start()
		while worker.is_alive():
			report()
			yield from events()
			if callerCancelled() or (planHold(path) or {}).get("mode") == "Stop":
				cancel.set()
			worker.join(.05)
		report()
		yield from events()
		worker = None
		if callerCancelled() or cancel.is_set():
			raise AutoError("Continuation cancelled", "cancelled")
		if "error" in outcome:
			raise outcome["error"]
		return outcome["result"]

	with ExitStack() as locks:
		planLock = locks.enter_context(exclusive(path / "continue.lock", "This plan"))
		workspace = None
		def workspaceLock(tasks):
			nonlocal workspace
			if workspace is None and requiresWorkspace(context, tasks):
				root = next((parent for parent in (context.project, *context.project.parents) if (parent / ".git").exists()), context.project)
				workspace = locks.enter_context(exclusive(root / ".auto/workspace.lock", "This working tree"))
				workspace.seek(0)
				workspace.truncate()
				workspace.write(jsonText({"plan": str(path), "continuation": value["id"]}))
				workspace.flush()
		with locked(path):
			cursor = (eventRows(path) or [{"seq": 0}])[-1]["seq"]
			recover(context, path)
			workspaceLock(tasksAt(path))
			reconcileContinuations(context, path, lockHeld=True)
			planLock.seek(0)
			planLock.truncate()
			planLock.write(jsonText({"plan": str(path), "continuation": value["id"]}))
			planLock.flush()
			os.fsync(planLock.fileno())
			saveContinuation(path, value)
			recordEvent(context, path, PlanEventKind.CONTINUATION_STARTED, "auto:" + createPlan(path).id,
				{"continuation": value["id"]}, date=value["started"], author=value["id"])
		try:
			yield from events()
			while True:
				if callerCancelled() or cancel.is_set():
					stop = ContinuationStop.CANCELLED
					break
				with locked(path):
					recover(context, path)
				interrupted = reconcileRunWorkers(context, path)
				if not interrupted and not planHold(path):
					applied = {row.get("elaborationRun") for row in journal(path)}
					for run in RunList(context, {"task": "elaborator", "status": RunStatus.SUCCEEDED}):
						if run["id"] not in applied and readJson(runsPath(path) / run["id"] / "state.json", {}).get("applyElaboration"):
							yield from perform(lambda run=run: applyPending(workerContext, run))
				with locked(path):
					status = PlanStatus(context)
					if planHold(path):
						stop = ContinuationStop.HELD
						break
					if status["status"] in {PlanState.SUCCEEDED, PlanState.FAILED, PlanState.ABANDONED}:
						stop = ContinuationStop.ENDED
						break
					if interrupted:
						stop = ContinuationStop.FAILED
						break
					tasks = tasksAt(path)
					workspaceLock(tasks)
					graph, runs = dependencyGraph(context, tasks), RunList(context)
					waiting = []
					for run in runs:
						if run["status"] == RunStatus.WAITING:
							gap = Knowledge(context).get(f"auto:{status['id']}/gap/{run['gap']}")
							if gap["status"] == GapState.RESOLVED:
								waiting.append((run, gap))
					remaining = {key: max(0, amount - status["usage"].get(key, 0)) for key, amount in budgetValue(status["budget"]).items()}
					order = taskOrder(path, tasks)
					ready = sorted((id for id in PlanReady(context) if id not in attempted
						and (latestRun(path, id) or {}).get("status") not in {RunStatus.FAILED, RunStatus.TIMED_OUT}), key=order.get)
				if waiting:
					run, gap = waiting[0]
					allocation = budgetValue(readJson(runsPath(path) / run["id"] / "state.json")["task"].get("budget")) if run["task"] != "root" else {}
					if any(amount <= 0 or max(0, allocation.get(key, 0) - run["usage"].get(key, 0)) > amount for key, amount in remaining.items()):
						stop = ContinuationStop.BUDGET
						break
					decision = Knowledge(context).get(f"auto:{status['id']}/decision/{gap['decision']}")
					ctx = workerContext.derive(limits=Limits(remaining))
					result = yield from perform(lambda: resumeGap(ctx, gap["id"], decision["answer"]))
					if result:
						yield from perform(lambda: applyPending(workerContext, result))
				elif ready:
					id = ready[0]
					task = effectiveTask(context, tasks[id])
					allocation = budgetValue(task.meta.get("budget")) if id != "root" else {}
					emptyAggregate = not task.sections.get("Process") and not any(hasCheck(task, phase) for phase in ("Pre", "Post", "Apply", "Evaluation"))
					if emptyAggregate:
						allocation = {key: amount for key, amount in allocation.items() if key == "time"}
					if any((amount <= 0 and (key == "time" or not emptyAggregate)) or allocation.get(key, 0) > amount for key, amount in remaining.items()):
						stop = ContinuationStop.BUDGET
						break
					ctx = workerContext.derive(limits=Limits(remaining))
					children = [child for child in tasks if child != "root" and tasks[child].meta.get("parent", "root") == id]
					if task.meta.get("hints", {}).get("elaborate") == "later" and not children:
						if id in refined:
							stop = ContinuationStop.BLOCKED
							break
						refined.add(id)
						result = yield from perform(lambda: PlanRefine(ctx, path, "Deferred elaboration", task=id))
						result = result.get("run", result) if isinstance(result, dict) else None
					else:
						attempted.add(id)
						result = yield from perform(lambda: TaskStart(ctx, id))
				else:
					if any(run["status"] in {RunStatus.PENDING, RunStatus.RUNNING} for run in runs):
						yield from events()
						cancel.wait(.05)
						continue
					stop = pendingStop(context, tasks, graph, status)
					break
				if result:
					if planHold(path):
						stop = ContinuationStop.HELD
						break
					completed = tasks.get(result.get("task"))
					if result.get("status") == RunStatus.SUCCEEDED and completed and completed.meta.get("hints", {}).get("refine") == "after":
						refinement = yield from perform(lambda: PlanRefine(ctx, path, "Successful task requests refinement", task="root"))
						if isinstance(refinement, dict) and isinstance(refinement.get("run"), dict) and refinement["run"]["status"] != RunStatus.SUCCEEDED:
							result = refinement["run"]
					failure = result.get("failure") or {}
					if result.get("status") == RunStatus.FAILED and (result.get("task") == "elaborator" or failure.get("kind") == "system"):
						stop, error = ContinuationStop.FAILED, failure.get("reason")
						break
					timeouts = timeouts + 1 if result.get("status") == RunStatus.TIMED_OUT else 0
					if timeouts >= threshold:
						stop = ContinuationStop.FAILED
						break
		except GeneratorExit:
			cancel.set()
			raise
		except KeyboardInterrupt:
			cancel.set()
			stop = ContinuationStop.CANCELLED
		except AutoError as failure:
			if planHold(path):
				stop = ContinuationStop.HELD
			elif callerCancelled() or cancel.is_set() or failure.kind == "cancelled":
				stop = ContinuationStop.CANCELLED
			elif PlanStatus(context)["status"] in {PlanState.SUCCEEDED, PlanState.FAILED, PlanState.ABANDONED}:
				stop = ContinuationStop.ENDED
			elif failure.kind == "budget":
				stop = ContinuationStop.BUDGET
			else:
				stop, error = ContinuationStop.FAILED, str(failure)
		except Exception as failure:
			stop, error = ContinuationStop.FAILED, f"{type(failure).__name__}: {failure}"
		finally:
			if worker is not None:
				cancel.set()
				worker.join()
			with locked(path):
				for directory in runDirectories(path):
					run = readReceipt(directory)
					if run.get("continuation") == value["id"] and run["id"] not in value["runs"]:
						value["runs"].append(run["id"])
				status = PlanStatus(context)
				value.update(ended=timestamp(), stop=stop, plan_status=status["status"], **({"error": error} if error else {}))
				saveContinuation(path, value)
				event = recordEvent(context, path, PlanEventKind.CONTINUATION_STOPPED, "auto:" + createPlan(path).id,
					{"continuation": value["id"], "stop": stop, "status": status["status"], **({"error": error} if error else {})},
					date=value["ended"], author=value["id"])
				finalEvents = [row for row in PlanEvents(context.derive(cancelled=lambda: False), since=cursor) if row["seq"] <= event["seq"]]
	yield from finalEvents
