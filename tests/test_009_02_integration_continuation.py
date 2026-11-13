"""Module: test_continuation — Sequential progression, exclusion and durable stops."""

from __future__ import annotations

import tempfile
import threading
import unittest
import selectors
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from auto.app.operations import exitStatus
from auto.features.orchestration.operations import PlanContinue
from auto.features.plan.control import PlanHold, PlanRelease, saveContinuation
from auto.features.plan.events import PlanEvents
from auto.features.plan.gaps import GapDecide, GapList, GapRaise, PlanRepair
from auto.features.plan.operations import PlanRevise, PlanStatus
from auto.features.plan.store import exclusive, locked
from auto.features.task.operations import RunGet, RunList, TaskStart, reconcileRunWorkers, saveRun
from auto.runtime.model import Context
from auto.utils.data import AutoError, documentText, readJson, timestamp
from test_004_01_integration_planning import Fake, MemoryKnowledge


class ContinuationTests(unittest.TestCase):
	def setUp(self):
		temp = tempfile.TemporaryDirectory()
		self.addCleanup(temp.cleanup)
		self.root = Path(temp.name)
		self.plan = self.root / ".auto/plans/demo"
		self.plan.mkdir(parents=True)
		(self.plan / "plan.md").write_text(documentText({"id": "demo", "goal": "Demo"}, "# Demo"))
		self.context = Context(project=self.root, plan=self.plan, engines={"fake": Fake()}, config={"engine": "fake"}, knowledge=MemoryKnowledge())

	def add(self, id, **fields):
		return PlanRevise(self.context, self.plan, [{"op": "add", "task": {"id": id, "sections": {"Process": id}, **fields}}], "Add work")

	def runPlan(self):
		return list(PlanContinue(self.context, self.plan))

	def startWorker(self, *, continuation=False):
		file = self.root / "worker.py"
		file.write_text('''import sys
from pathlib import Path
from auto.features.task.operations import TaskStart
from auto.features.plan.gaps import PlanRepair
from auto.features.orchestration.operations import PlanContinue
from auto.runtime.model import Context
class Store:
    def list(self, plan, kind):
        return []
class Engine:
    def start(self, context, request):
        context.limits.usage["tokens"] = 3
        yield {"type": "ScriptMeta", "name": "usage", "value": {"tokens": 3}}
        print("ready", flush=True)
        sys.stdin.readline()
        yield {"type": "ScriptEnded", "status": "Succeeded", "output": "done"}
root = Path(sys.argv[1])
context = Context(project=root, plan=root / ".auto/plans/demo", knowledge=Store(), engines={"fake": Engine()}, config={"engine": "fake"})
if len(sys.argv) > 2 and sys.argv[2] == "repair":
    PlanRepair(context)
elif len(sys.argv) > 2 and sys.argv[2] == "continue":
    for event in PlanContinue(context, context.plan):
        pass
else:
    TaskStart(context, sys.argv[3] if len(sys.argv) > 3 else "work")
''')
		worker = subprocess.Popen([sys.executable, str(file), str(self.root), "continue" if continuation else "start"], stdin=subprocess.PIPE,
			stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
		def cleanup():
			if worker.poll() is None:
				worker.kill()
			worker.communicate(timeout=10)
		self.addCleanup(cleanup)
		with selectors.DefaultSelector() as selector:
			selector.register(worker.stdout, selectors.EVENT_READ)
			self.assertTrue(selector.select(timeout=10), "Worker did not reach its engine")
		self.assertEqual(worker.stdout.readline().strip(), "ready")
		return worker

	def testRepairKeepsLiveWorkerAndFailsKilledWorkerOnceWithKnownUsage(self):
		self.add("work")
		worker = self.startWorker()
		run = RunList(self.context)[0]
		PlanRepair(self.context)
		self.assertEqual(RunGet(self.context, run)["status"], "Running")
		self.assertIsNone(worker.poll())
		worker.kill()
		worker.communicate(timeout=10)
		self.assertLess(worker.returncode, 0)
		repairs = [subprocess.Popen([sys.executable, str(self.root / "worker.py"), str(self.root), "repair"],
			stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
		try:
			for repair in repairs:
				stdout, stderr = repair.communicate(timeout=10)
				self.assertEqual(repair.returncode, 0, stderr)
		finally:
			for repair in repairs:
				if repair.poll() is None:
					repair.kill()
				repair.communicate(timeout=10)
		repaired = RunGet(self.context, run)
		self.assertEqual(repaired["status"], "Failed")
		self.assertEqual(repaired["failure"]["kind"], "system")
		self.assertEqual(repaired["usage"]["tokens"], 3)
		self.assertIn("interrupted", repaired["failure"]["reason"])
		receipt = self.plan / "runs" / run["id"] / "receipt.json"
		original = receipt.read_bytes()
		PlanRepair(self.context)
		self.assertEqual(receipt.read_bytes(), original)
		ended = [event for event in PlanEvents(self.context) if event["kind"] == "RunEnded" and event["data"]["run"] == run["id"]]
		self.assertEqual(len(ended), 1)
		self.assertEqual(self.runPlan()[-1]["data"]["stop"], "failed")
		self.assertEqual(len(RunList(self.context)), 1)

	def testContinuationReconcilesKilledWorkerBeforeStartingIndependentWork(self):
		self.add("work")
		self.add("independent")
		worker = self.startWorker(continuation=True)
		previous = readJson(self.plan / "continuation.json")["id"]
		worker.kill()
		worker.communicate(timeout=10)
		stale = readJson(self.plan / "continuations" / (previous + ".json"))
		stale["runs"] = []
		saveContinuation(self.plan, stale)
		events = self.runPlan()
		self.assertEqual(events[-1]["data"]["stop"], "failed")
		self.assertEqual(len(RunList(self.context)), 1)
		old = readJson(self.plan / "continuations" / (previous + ".json"))
		self.assertEqual(old["stop"], "cancelled")
		self.assertEqual(old["runs"], [RunList(self.context)[0]["id"]])
		self.assertEqual(sum(event["kind"] == "RunEnded" for event in events), 1)
		self.assertEqual(self.runPlan()[-1]["data"]["stop"], "failed")
		self.assertCountEqual([run["task"] for run in RunList(self.context)], ["work", "independent"])

	def testWorkerLockIsHeldBeforeRunningStateIsPublished(self):
		self.add("work")
		probes = []
		def save(context, path, run, state=None):
			saveRun(context, path, run, state)
			if not probes and run["status"] == "Running":
				results = []
				probe = threading.Thread(target=lambda: results.extend(reconcileRunWorkers(self.context, self.plan)))
				probes.append(probe)
				probe.start()
				probe.join(timeout=2)
				self.assertFalse(probe.is_alive(), "Repair acquired a published run without worker ownership")
				self.assertEqual(results, [])
		with patch("auto.features.task.operations.saveRun", side_effect=save):
			self.assertEqual(TaskStart(self.context, "work")["status"], "Succeeded")
		for probe in probes:
			probe.join(timeout=2)
		self.assertTrue(probes)
		owners = []
		def probePlan():
			try:
				with exclusive(self.plan / ".lock", "Plan writer"):
					owners.append("acquired")
			except AutoError:
				owners.append("busy")
		with locked(self.plan):
			probe = threading.Thread(target=probePlan)
			probe.start()
			probe.join(timeout=2)
		self.assertEqual(owners, ["busy"], "Run completion left a stale reentrant plan owner")

	def testDependencyThenJournalOrderAndAggregateCompletion(self):
		PlanRevise(self.context, self.plan, [
			{"op": "add", "task": {"id": "a", "inputs": {"upstream": "task:c"}, "sections": {"Process": "a"}}},
			{"op": "add", "task": {"id": "b", "sections": {"Process": "b"}}},
			{"op": "add", "task": {"id": "c", "sections": {"Process": "c"}}}], "Ordered work")
		self.context.engines["fake"] = Fake(["b output", "c output", "a output"])
		events = self.runPlan()
		self.assertEqual([row["data"]["task"] for row in events if row["kind"] == "RunStarted"], ["b", "c", "a", "root"])
		self.assertEqual(events[0]["kind"], "ContinuationStarted")
		self.assertEqual(events[-1]["kind"], "ContinuationStopped")
		self.assertEqual(events[-1]["data"]["stop"], "ended")
		self.assertEqual(exitStatus(events[-1]), 0)
		self.assertEqual(PlanStatus(self.context)["status"], "Succeeded")
		self.assertEqual(len(PlanStatus(self.context)["continuation"]["runs"]), 4)
		PlanRepair(self.context)
		self.assertEqual(sum(row["kind"] == "ContinuationStopped" for row in PlanEvents(self.context)), 1)

	def testOutputFailureDoesNotStopIndependentWorkOrAutomaticallyRetry(self):
		class Engine:
			def start(inner, context, request):
				if request["prompt"].startswith("bad"):
					yield {"type": "ScriptEnded", "status": "Failed", "failure": {"kind": "output", "reason": "Bad result"}}
				else:
					yield {"type": "ScriptEnded", "status": "Succeeded", "output": "Good"}
		self.context.engines["fake"] = Engine()
		self.add("bad")
		self.add("dependent", inputs={"bad": "task:bad"})
		self.add("independent")
		events = self.runPlan()
		self.assertEqual([row["data"]["task"] for row in events if row["kind"] == "RunStarted"], ["bad", "independent"])
		self.assertEqual(events[-1]["data"]["stop"], "failed")
		count = len(RunList(self.context))
		self.assertEqual(self.runPlan()[-1]["data"]["stop"], "failed")
		self.assertEqual(len(RunList(self.context)), count)

	def testSystemFailureStopsBeforeIndependentWork(self):
		class Engine:
			def start(inner, context, request):
				yield {"type": "ScriptEnded", "status": "Failed", "failure": {"kind": "system", "reason": "Unavailable engine"}}
		self.context.engines["fake"] = Engine()
		self.add("first")
		self.add("second")
		self.assertEqual(self.runPlan()[-1]["data"]["stop"], "failed")
		self.assertEqual([run["task"] for run in RunList(self.context)], ["first"])

	def testConsecutiveTimeoutThresholdStopsProgression(self):
		class Engine:
			def start(inner, context, request):
				yield {"type": "ScriptEnded", "status": "TimedOut", "failure": {"kind": "budget", "reason": "Time exhausted"}}
		self.context.engines["fake"] = Engine()
		for id in ("one", "two", "three"):
			self.add(id)
		self.assertEqual(self.runPlan()[-1]["data"]["stop"], "failed")
		self.assertEqual([run["task"] for run in RunList(self.context)], ["one", "two"])

	def testBudgetStopsBeforeUnaffordableRunAndCapsUnallocatedWork(self):
		PlanRevise(self.context, self.plan, [{"op": "update", "fields": {"budget": {"tokens": 5}}}], "Small budget")
		self.add("large", budget={"tokens": 10})
		result = self.runPlan()[-1]
		self.assertEqual(result["data"]["stop"], "budget")
		self.assertEqual(exitStatus(result), 3)
		self.assertEqual(RunList(self.context), [])
		PlanRevise(self.context, self.plan, [{"op": "remove", "task": "large"}], "Use small work")
		self.add("small")
		class Engine:
			def start(inner, context, request):
				inner.budget = dict(context.limits.budget)
				context.limits.usage["tokens"] += 2
				yield {"type": "ScriptEnded", "status": "Succeeded", "output": "Done"}
		engine = Engine()
		self.context.engines["fake"] = engine
		self.assertEqual(self.runPlan()[-1]["data"]["stop"], "ended")
		self.assertEqual(engine.budget["tokens"], 5)
		self.assertEqual(next(run for run in RunList(self.context) if run["task"] == "root")["budget"]["tokens"], 3)
		self.assertEqual(PlanStatus(self.context)["usage"]["tokens"], 2)

	def testExactTokenAndCostLimitsStillCompleteTheEmptyAggregate(self):
		PlanRevise(self.context, self.plan, [{"op": "update", "fields": {"budget": {"tokens": 5, "cost": .5}}}], "Exact budget")
		self.add("work", budget={"tokens": 5, "cost": .5})
		class Engine:
			def start(inner, context, request):
				context.limits.usage.update(tokens=5, cost=.5)
				yield {"type": "ScriptEnded", "status": "Succeeded", "output": "Done"}
		self.context.engines["fake"] = Engine()
		self.assertEqual(self.runPlan()[-1]["data"]["stop"], "ended")
		self.assertEqual(PlanStatus(self.context)["status"], "Succeeded")
		runs = {run["task"]: run for run in RunList(self.context)}
		self.assertEqual(runs["work"]["usage"]["tokens"], 5)
		self.assertEqual(runs["work"]["usage"]["cost"], .5)
		self.assertEqual(runs["root"]["budget"]["tokens"], 0)
		self.assertEqual(runs["root"]["usage"]["tokens"], 0)

	def testZeroTokenAndCostPlanCanAssembleWithoutCallingAnEngine(self):
		PlanRevise(self.context, self.plan, [{"op": "update", "fields": {"budget": {"tokens": 0, "cost": 0}}}], "No inference")
		self.assertEqual(self.runPlan()[-1]["data"]["stop"], "ended")
		self.assertEqual(self.context.engines["fake"].calls, [])
		self.assertEqual(PlanStatus(self.context)["status"], "Succeeded")

	def testNestedEmptyAggregateDoesNotReserveItsChildrensSpentAllocation(self):
		PlanRevise(self.context, self.plan, [{"op": "update", "fields": {"budget": {"tokens": 5}}}], "Nested budget")
		self.add("parent", budget={"tokens": 5}, sections={}, hints={"elaborate": "later"})
		self.add("child", parent="parent", budget={"tokens": 5})
		calls = []
		class Engine:
			def start(inner, context, request):
				calls.append(request)
				context.limits.usage["tokens"] += 5
				yield {"type": "ScriptEnded", "status": "Succeeded", "output": "Done"}
		self.context.engines["fake"] = Engine()
		self.assertEqual(self.runPlan()[-1]["data"]["stop"], "ended")
		self.assertEqual(len(calls), 1)
		self.assertEqual(PlanStatus(self.context)["usage"]["tokens"], 5)
		self.assertEqual({run["task"]: run["budget"]["tokens"] for run in RunList(self.context)}, {"parent": 0, "child": 5, "root": 0})

	def testZeroBudgetStopsBeforeStartingModelWork(self):
		PlanRevise(self.context, self.plan, [{"op": "update", "fields": {"budget": {"tokens": 0}}}], "No inference")
		self.add("work")
		self.assertEqual(self.runPlan()[-1]["data"]["stop"], "budget")
		self.assertEqual(RunList(self.context), [])
		self.assertEqual(self.context.engines["fake"].calls, [])

	def testExactTokenLimitAllowsShellChecksButNoFurtherModelRequest(self):
		marker = self.root / "checked.txt"
		self.add("shell", budget={"tokens": 1}, post="printf checked > " + str(marker))
		self.add("model", budget={"tokens": 1}, sections={"Process": "Model work", "Post": "Check quality"})
		calls = []
		class Engine:
			def start(inner, context, request):
				calls.append(request)
				context.limits.usage["tokens"] += 1
				yield {"type": "ScriptEnded", "status": "Succeeded", "output": "Done"}
		self.context.engines["fake"] = Engine()
		self.assertEqual(TaskStart(self.context, "shell")["status"], "Succeeded")
		self.assertEqual(marker.read_text(), "checked")
		result = TaskStart(self.context, "model")
		self.assertEqual(result["status"], "Failed")
		self.assertEqual(result["failure"]["kind"], "budget")
		self.assertEqual(len(calls), 2)

	def testUsageAboveTheLimitFailsAndRetainsMeasuredOverspend(self):
		self.add("work", budget={"tokens": 5})
		class Engine:
			def start(inner, context, request):
				context.limits.usage["tokens"] = 6
				yield {"type": "ScriptEnded", "status": "Succeeded", "output": "Done"}
		self.context.engines["fake"] = Engine()
		run = TaskStart(self.context, "work")
		self.assertEqual(run["status"], "Failed")
		self.assertEqual(run["failure"]["kind"], "budget")
		self.assertEqual(run["usage"]["tokens"], 6)

	def testIndependentManualProcessCannotOvercommitAnActiveRunAllocation(self):
		PlanRevise(self.context, self.plan, [{"op": "update", "fields": {"budget": {"tokens": 10}}}], "Shared capacity")
		self.add("work", budget={"tokens": 6})
		self.add("other", budget={"tokens": 6})
		worker = self.startWorker()
		stream = PlanContinue(self.context, self.plan)
		next(stream)
		try:
			second = subprocess.run([sys.executable, str(self.root / "worker.py"), str(self.root), "start", "other"],
				input="done\n", capture_output=True, text=True, timeout=10)
			self.assertNotEqual(second.returncode, 0)
			self.assertIn("Plan tokens budget cannot cover", second.stderr)
			self.assertEqual(len(RunList(self.context)), 1)
			self.assertEqual(RunList(self.context)[0]["budget"]["tokens"], 6)
			worker.communicate(input="done\n", timeout=10)
			self.assertEqual(worker.returncode, 0)
			self.assertEqual(TaskStart(self.context, "other")["status"], "Succeeded")
			self.assertEqual(PlanStatus(self.context)["usage"]["tokens"], 3)
		finally:
			stream.close()

	def testManualSpendBetweenSelectionAndStartupProducesBudgetStop(self):
		PlanRevise(self.context, self.plan, [{"op": "update", "fields": {"budget": {"tokens": 10}}}], "Shared capacity")
		self.add("work", budget={"tokens": 6})
		self.add("manual", budget={"tokens": 5})
		class Engine:
			def start(inner, context, request):
				context.limits.usage["tokens"] += 5
				yield {"type": "ScriptEnded", "status": "Succeeded", "output": "Done"}
		self.context.engines["fake"] = Engine()
		def start(context, task):
			TaskStart(self.context, "manual")
			return TaskStart(context, task)
		with patch("auto.features.orchestration.operations.TaskStart", side_effect=start):
			self.assertEqual(self.runPlan()[-1]["data"]["stop"], "budget")
		self.assertEqual([run["task"] for run in RunList(self.context)], ["manual"])
		self.assertEqual(PlanStatus(self.context)["usage"]["tokens"], 5)

	def testUnboundedWorkerReservesCapacityWhenAPlanLimitIsAdded(self):
		self.add("work")
		worker = self.startWorker()
		PlanRevise(self.context, self.plan, [{"op": "update", "fields": {"budget": {"tokens": 10}}}], "New plan limit")
		self.add("other", budget={"tokens": 1})
		with self.assertRaisesRegex(AutoError, "Plan tokens budget cannot cover"):
			TaskStart(self.context, "other")
		self.assertEqual(len(RunList(self.context)), 1)
		worker.communicate(input="done\n", timeout=10)
		self.assertEqual(worker.returncode, 0)
		self.assertEqual(TaskStart(self.context, "other")["status"], "Succeeded")

	def waitingBudgetScenario(self, *, explicit):
		PlanRevise(self.context, self.plan, [{"op": "update", "fields": {"budget": {"tokens": 6 if explicit else 5}}}], "Shared budget")
		self.add("question", **({"budget": {"tokens": 4}} if explicit else {}))
		self.add("independent", budget={"tokens": 3})
		allocations = []
		class Engine:
			def start(inner, context, request):
				allocations.append(dict(context.limits.budget))
				if request["prompt"].startswith("question") and "answer" not in request:
					context.limits.usage["tokens"] += 1
					yield {"type": "ScriptQuestion", "content": "Choose"}
				else:
					context.limits.usage["tokens"] += 3 if explicit or request["prompt"].startswith("independent") else 1
					yield {"type": "ScriptEnded", "status": "Succeeded", "output": "Done"}
		self.context.engines["fake"] = Engine()
		self.assertEqual(self.runPlan()[-1]["data"]["stop"], "waiting")
		run = next(run for run in RunList(self.context) if run["task"] == "question")
		self.assertEqual(run["usage"]["tokens"], 1)
		GapDecide(self.context, run["gap"], "yes", "Chosen")
		return run, allocations

	def testExplicitResumeAllocationMustFitAfterIndependentWork(self):
		run, allocations = self.waitingBudgetScenario(explicit=True)
		self.assertEqual(self.runPlan()[-1]["data"]["stop"], "budget")
		self.assertEqual(RunGet(self.context, run)["status"], "Waiting")
		self.assertEqual(len(allocations), 2)
		PlanRevise(self.context, self.plan, [{"op": "update", "fields": {"budget": {"tokens": 7}}}], "Cover remaining allocation")
		self.assertEqual(self.runPlan()[-1]["data"]["stop"], "ended")
		self.assertEqual(RunGet(self.context, run)["usage"]["tokens"], 4)
		self.assertEqual(PlanStatus(self.context)["usage"]["tokens"], 7)

	def testImplicitResumeCapUsesRemainingPlanCapacityWithoutRechargingUsage(self):
		run, allocations = self.waitingBudgetScenario(explicit=False)
		self.assertEqual(self.runPlan()[-1]["data"]["stop"], "ended")
		self.assertEqual(allocations[-1]["tokens"], 2)
		self.assertEqual(RunGet(self.context, run)["budget"]["tokens"], 2)
		self.assertEqual(RunGet(self.context, run)["usage"]["tokens"], 2)
		self.assertEqual(PlanStatus(self.context)["usage"]["tokens"], 5)
		self.assertEqual(sum(item["task"] == "question" for item in RunList(self.context)), 1)

	def testWaitingRunResumesOnlyAtNextContinuationAfterDecision(self):
		class Engine:
			def start(inner, context, request):
				if request["prompt"].startswith("question") and "answer" not in request:
					yield {"type": "ScriptQuestion", "content": "Choose"}
				else:
					yield {"type": "ScriptEnded", "status": "Succeeded", "output": "Done"}
		self.context.engines["fake"] = Engine()
		self.add("question")
		self.add("independent")
		self.assertEqual(self.runPlan()[-1]["data"]["stop"], "waiting")
		run = next(run for run in RunList(self.context) if run["task"] == "question")
		GapDecide(self.context, run["gap"], "yes", "Chosen")
		self.assertEqual(RunGet(self.context, run)["status"], "Waiting")
		self.assertEqual(self.runPlan()[-1]["data"]["stop"], "ended")
		self.assertEqual(RunGet(self.context, run)["status"], "Succeeded")
		self.assertEqual(sum(run["task"] == "question" for run in RunList(self.context)), 1)

	def testResolutionDuringContinuationResumesInTheSameProgression(self):
		class Engine:
			def start(inner, context, request):
				if "answer" not in request:
					yield {"type": "ScriptQuestion", "content": "Choose"}
				else:
					yield {"type": "ScriptEnded", "status": "Succeeded", "output": "Done"}
		self.context.engines["fake"] = Engine()
		self.add("question")
		events = []
		for event in PlanContinue(self.context, self.plan):
			events.append(event)
			if event["kind"] == "RunWaiting":
				GapDecide(self.context, event["data"]["gap"], "yes", "Chosen")
		self.assertEqual(events[-1]["data"]["stop"], "ended")
		self.assertEqual(sum(row["kind"] == "ContinuationStarted" for row in events), 1)

	def testHoldsStopImmediatelyAndReleaseDoesNotRunWork(self):
		self.add("work")
		PlanHold(self.context, self.plan, "Pause", "Review")
		self.assertEqual(self.runPlan()[-1]["data"]["stop"], "held")
		self.assertEqual(RunList(self.context), [])
		PlanRelease(self.context, self.plan, "Ready")
		self.assertEqual(RunList(self.context), [])
		self.assertEqual(self.runPlan()[-1]["data"]["stop"], "ended")

	def testOnlyOneContinuationAndClosingItsStreamRecordsCancellation(self):
		self.add("work")
		first = PlanContinue(self.context, self.plan)
		self.assertEqual(next(first)["kind"], "ContinuationStarted")
		before = list(PlanEvents(self.context))
		with self.assertRaisesRegex(AutoError, "running continuation"):
			next(PlanContinue(self.context, self.plan))
		self.assertEqual(list(PlanEvents(self.context)), before)
		first.close()
		self.assertEqual(list(PlanEvents(self.context))[-1]["data"]["stop"], "cancelled")
		self.assertEqual(self.runPlan()[-1]["data"]["stop"], "ended")
		self.assertEqual(sum(row["kind"] == "ContinuationStarted" for row in PlanEvents(self.context)), 2)

	def testClosingStreamCancelsTheCurrentRunBeforeReleasingLock(self):
		class SlowEngine:
			def start(inner, context, request):
				while not context.cancelled():
					threading.Event().wait(.01)
				raise AutoError("Cancelled", "cancelled")
				yield
		self.context.engines["fake"] = SlowEngine()
		self.add("work")
		stream = PlanContinue(self.context, self.plan)
		self.assertEqual(next(stream)["kind"], "ContinuationStarted")
		self.assertEqual(next(stream)["kind"], "RunStarted")
		stream.close()
		self.assertEqual(RunList(self.context)[0]["status"], "Cancelled")
		self.assertEqual(list(PlanEvents(self.context))[-1]["data"]["stop"], "cancelled")

	def testWritingPlansSharingWorkspaceCannotContinueConcurrently(self):
		self.add("writer", effects=["repo.write"])
		other = self.plan.with_name("other")
		other.mkdir()
		(other / "plan.md").write_text(documentText({"id": "other", "goal": "Other", "effects": ["repo.write"]}, "# Other"))
		first = PlanContinue(self.context, self.plan)
		next(first)
		try:
			with self.assertRaisesRegex(AutoError, "working tree"):
				next(PlanContinue(self.context.derive(plan=other), other))
		finally:
			first.close()
		self.assertEqual(list(PlanEvents(self.context.derive(plan=other))), [])

	def testDeferredElaborationAndAfterHintsAreActedOn(self):
		self.add("deferred", hints={"elaborate": "later"}, sections={})
		def refine(context, plan, reason, task="root"):
			if task == "deferred":
				PlanRevise(context, plan, [{"op": "add", "task": {"id": "child", "parent": "deferred", "hints": {"refine": "after"}, "sections": {"Process": "Child work"}}}], reason)
			return {"run": {"status": "Succeeded"}}
		with patch("auto.features.orchestration.operations.PlanRefine", side_effect=refine) as refineOperation:
			self.assertEqual(self.runPlan()[-1]["data"]["stop"], "ended")
		self.assertEqual([call.kwargs["task"] for call in refineOperation.call_args_list], ["deferred", "root"])
		self.assertEqual([run["task"] for run in RunList(self.context)], ["child", "deferred", "root"])

	def testFailureHasPrecedenceOverOpenGaps(self):
		self.add("bad")
		self.add("waiting")
		GapRaise(self.context, self.plan, {"question": "Choose", "needed_by": ["waiting"]})
		class Engine:
			def start(inner, context, request):
				yield {"type": "ScriptEnded", "status": "Failed", "failure": {"kind": "output", "reason": "Bad output"}}
		self.context.engines["fake"] = Engine()
		self.assertEqual(self.runPlan()[-1]["data"]["stop"], "failed")
		self.assertTrue(GapList(self.context))

	def testRepairDoesNotCloseLiveContinuationButClosesInterruptedSource(self):
		self.add("work")
		stream = PlanContinue(self.context, self.plan)
		next(stream)
		try:
			PlanRepair(self.context)
			self.assertIsNone(PlanStatus(self.context)["continuation"]["ended"])
		finally:
			stream.close()
		value = {"id": "interrupted", "started": timestamp(), "ended": None, "runs": [], "stop": None}
		saveContinuation(self.plan, value)
		PlanRepair(self.context)
		repaired = readJson(self.plan / "continuations/interrupted.json")
		self.assertEqual(repaired["stop"], "cancelled")
		self.assertIsNotNone(repaired["ended"])
		events = [row for row in PlanEvents(self.context) if row["data"].get("continuation") == "interrupted"]
		self.assertEqual([row["kind"] for row in events], ["ContinuationStarted", "ContinuationStopped"])
		PlanRepair(self.context)
		self.assertEqual(readJson(self.plan / "continuations/interrupted.json"), repaired)

	def testManuallyStartedWaitingRunAlsoWaitsForContinuation(self):
		from auto.features.task.operations import TaskStart
		class Engine:
			def start(inner, context, request):
				if "answer" not in request:
					yield {"type": "ScriptQuestion", "content": "Choose"}
				else:
					yield {"type": "ScriptEnded", "status": "Succeeded", "output": "Done"}
		self.context.engines["fake"] = Engine()
		self.add("question")
		run = TaskStart(self.context, "question")
		GapDecide(self.context, run["gap"], "yes", "Chosen")
		PlanRepair(self.context)
		self.assertEqual(RunGet(self.context, run)["status"], "Waiting")
		self.assertEqual(self.runPlan()[-1]["data"]["stop"], "ended")
		self.assertEqual(RunGet(self.context, run)["status"], "Succeeded")
