"""Plan-wide budgets across retries, retained work, revisions and resumption."""

from __future__ import annotations

import unittest

from auto.features.guided.operations import PlanGo
from auto.features.orchestration.operations import PlanContinue
from auto.features.plan.gaps import GapDecide
from auto.features.plan.operations import PlanRevise, PlanStatus
from auto.features.task.operations import RunCheck, RunGet, RunList, TaskStart
from auto.utils.data import AutoError
import test_009_02_integration_continuation as fixtures
from test_004_01_integration_planning import Fake


class BudgetCombinationTests(unittest.TestCase):
	setUp = fixtures.ContinuationTests.setUp
	add = fixtures.ContinuationTests.add
	startWorker = fixtures.ContinuationTests.startWorker

	def limit(self, **budget):
		PlanRevise(self.context, self.plan, [{"op": "update", "fields": {"budget": budget}}], "Revise available capacity")

	def testExplicitRetryChargesFailedAttemptAndRetainsSucceededSibling(self):
		self.limit(tokens=8, cost=1)
		self.add("retained", budget={"tokens": 2, "cost": .25})
		self.add("retry", budget={"tokens": 4, "cost": .5})
		calls = []
		class Engine:
			def start(inner, context, request):
				calls.append(request["prompt"].splitlines()[0])
				context.limits.usage.update(tokens=2, cost=.25)
				failed = len(calls) == 2
				yield {"type": "ScriptEnded", "status": "Failed" if failed else "Succeeded",
					**({"failure": {"kind": "output", "reason": "Try again explicitly"}} if failed else {"output": "Done"})}
		self.context.engines["fake"] = Engine()
		retained = TaskStart(self.context, "retained")
		receipt = self.plan / "runs" / retained["id"] / "receipt.json"
		original = receipt.read_bytes()
		failed = TaskStart(self.context, "retry")
		self.assertEqual(failed["status"], "Failed")
		retry = TaskStart(self.context, "retry")
		self.assertEqual(retry["status"], "Succeeded")
		self.assertNotEqual(retry["id"], failed["id"])
		self.assertEqual(list(PlanContinue(self.context, self.plan))[-1]["data"]["stop"], "ended")
		self.assertEqual(calls, ["retained", "retry", "retry"])
		self.assertEqual(PlanStatus(self.context)["usage"]["tokens"], 6)
		self.assertEqual(PlanStatus(self.context)["usage"]["cost"], .75)
		self.assertEqual(receipt.read_bytes(), original)
		self.assertEqual(len(RunList(self.context, {"task": "retained"})), 1)

	def testRetryCannotReuseFailedAttemptsSpentCapacity(self):
		self.limit(tokens=5, cost=.5)
		self.add("work", budget={"tokens": 4, "cost": .5})
		class Engine:
			def start(inner, context, request):
				context.limits.usage.update(tokens=2, cost=.25)
				yield {"type": "ScriptEnded", "status": "Failed", "failure": {"kind": "output", "reason": "Bad output"}}
		self.context.engines["fake"] = Engine()
		self.assertEqual(TaskStart(self.context, "work")["status"], "Failed")
		with self.assertRaises(AutoError) as denied:
			TaskStart(self.context, "work")
		self.assertEqual(denied.exception.kind, "budget")
		self.assertEqual(len(RunList(self.context)), 1)
		self.assertEqual(PlanStatus(self.context)["usage"]["tokens"], 2)

	def testLoweredLimitKeepsLiveReservationAndTerminalUsage(self):
		self.limit(tokens=10)
		self.add("work", budget={"tokens": 6})
		worker = self.startWorker()
		self.limit(tokens=4)
		self.add("other", budget={"tokens": 1})
		with self.assertRaises(AutoError):
			TaskStart(self.context, "other")
		worker.communicate(input="done\n", timeout=10)
		self.assertEqual(worker.returncode, 0)
		run = RunList(self.context)[0]
		self.assertEqual(run["budget"]["tokens"], 6)
		self.assertEqual(run["usage"]["tokens"], 3)
		self.assertEqual(TaskStart(self.context, "other")["status"], "Succeeded")
		self.assertEqual(PlanStatus(self.context)["usage"]["tokens"], 3)

	def testRaisedLimitDoesNotExpandExistingWorkerReservation(self):
		self.context.engines["fake"] = Fake(["Other", "Larger"])
		self.limit(tokens=6)
		self.add("work", budget={"tokens": 6})
		worker = self.startWorker()
		self.limit(tokens=8)
		self.add("other", budget={"tokens": 2})
		self.assertEqual(TaskStart(self.context, "other")["status"], "Succeeded")
		self.add("too-large", budget={"tokens": 3})
		with self.assertRaises(AutoError):
			TaskStart(self.context, "too-large")
		worker.communicate(input="done\n", timeout=10)
		self.assertEqual(worker.returncode, 0)
		self.assertEqual(TaskStart(self.context, "too-large")["status"], "Succeeded")
		self.assertEqual(PlanStatus(self.context)["usage"]["tokens"], 3)

	def testCostAndTimeResumeUseCumulativeUsageAndNewPlanCapacity(self):
		self.limit(tokens=8, cost=1, time=60)
		self.add("question", budget={"tokens": 6, "cost": .75, "time": 30})
		self.add("independent", budget={"tokens": 2, "cost": .25, "time": 10})
		allocations = []
		class Engine:
			def start(inner, context, request):
				allocations.append(dict(context.limits.budget))
				context.limits.usage["tokens"] += 2
				context.limits.usage["cost"] += .25
				if request["prompt"].startswith("question") and "answer" not in request:
					yield {"type": "ScriptQuestion", "content": "Continue?"}
				else:
					yield {"type": "ScriptEnded", "status": "Succeeded", "output": "Done"}
		self.context.engines["fake"] = Engine()
		waiting = TaskStart(self.context, "question")
		self.assertEqual(waiting["status"], "Waiting")
		self.assertEqual(TaskStart(self.context, "independent")["status"], "Succeeded")
		GapDecide(self.context, waiting["gap"], "yes", "Continue")
		self.limit(tokens=8, cost=.75, time=60)
		self.assertEqual(list(PlanContinue(self.context, self.plan))[-1]["data"]["stop"], "budget")
		self.assertEqual(RunGet(self.context, waiting)["status"], "Waiting")
		self.assertEqual(len(allocations), 2)
		self.limit(tokens=8, cost=1, time=60)
		observed = []
		self.assertEqual(list(PlanGo(self.context.derive(observe=observed.append), self.plan))[-1]["data"]["stop"], "ended")
		progress = next(event for event in observed if event["type"] == "RunStarted")
		self.assertEqual(progress["planBudget"], {"tokens": 8, "cost": 1, "time": 60})
		self.assertEqual((progress["planUsage"]["tokens"], progress["usage"]["tokens"]), (2, 2), "other runs plus saved usage of this run")
		resumed = RunGet(self.context, waiting)
		self.assertEqual(resumed["usage"]["tokens"], 4)
		self.assertEqual(resumed["usage"]["cost"], .5)
		self.assertGreater(resumed["usage"]["time"], waiting["usage"]["time"])
		self.assertLessEqual(resumed["usage"]["time"], allocations[-1]["time"])
		self.assertEqual(PlanStatus(self.context)["usage"]["cost"], .75)
		self.assertEqual(len(RunList(self.context, {"task": "question"})), 1)

	def testNestedAggregateChecksAndReassessmentSharePlanCapacity(self):
		self.limit(tokens=8, cost=1, time=60)
		self.add("parent", budget={"tokens": 4, "cost": .5, "time": 30},
			sections={"Post": "Review children"}, hints={"elaborate": "later"})
		self.add("child", parent="parent", budget={"tokens": 2, "cost": .25, "time": 10})
		self.add("sibling", budget={"tokens": 2, "cost": .25, "time": 10})
		calls = []
		class Engine:
			def start(inner, context, request):
				calls.append(request)
				context.limits.usage["tokens"] += 2
				context.limits.usage["cost"] += .25
				yield {"type": "ScriptEnded", "status": "Succeeded",
					"output": {"pass": True, "reason": "Reviewed"} if request["output"]["format"] == "json" else "Produced"}
		self.context.engines["fake"] = Engine()
		self.assertEqual(list(PlanContinue(self.context, self.plan))[-1]["data"]["stop"], "ended")
		runs = {run["task"]: run for run in RunList(self.context)}
		self.assertEqual(PlanStatus(self.context)["usage"]["tokens"], 6)
		path = self.plan / "runs" / runs["tsk-parent"]["id"] / "receipt.json"
		original = path.read_bytes()
		self.assertTrue(RunCheck(self.context, runs["tsk-parent"], "post")["pass"])
		self.assertEqual(PlanStatus(self.context)["usage"]["tokens"], 8)
		self.assertEqual(PlanStatus(self.context)["usage"]["cost"], 1)
		self.assertEqual(path.read_bytes(), original)
		with self.assertRaises(AutoError) as exhausted:
			RunCheck(self.context, runs["tsk-parent"], "post")
		self.assertEqual(exhausted.exception.kind, "budget")
		self.assertEqual(len(calls), 4)


# EOF
