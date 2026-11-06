"""Module: test_planning — Run lifecycle, revisions, elaboration and repair."""

from __future__ import annotations

import copy
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from auto.features.elaboration.operations import ElaborationApply, ElaborationParse, ElaborationValidate
from auto.features.plan.gaps import GapDecide, GapList, GapRaise, PlanRepair
from auto.features.plan.operations import PlanRevise, PlanStatus, PlanReady
from auto.features.plan.store import journal
from auto.features.task.operations import TaskStart, RunGet, RunOutput, TaskUpdate, RunCheck
from auto.runtime.model import Context
from auto.utils.data import AutoError, digest, documentText, jsonText


class MemoryKnowledge:
	def __init__(self):
		self.records = {}
		self.fail = False

	def list(self, plan, kind):
		return [self.get(ref) for ref, (category, value) in self.records.items() if category == kind and value["plan"] == plan]

	def get(self, ref):
		value = copy.deepcopy(self.records[ref][1])
		return {**value, "digest": digest(jsonText(value).encode())}

	def write(self, kind, value, expected=""):
		if self.fail:
			self.fail = False
			raise AutoError("Injected storage failure", "system")
		value = {key: item for key, item in value.items() if key != "digest"}
		ref = value["identity"]
		if ref in self.records:
			if self.records[ref][1] == value:
				return
			if self.get(ref)["digest"] != expected:
				raise AutoError("stale write")
		elif expected:
			raise AutoError("missing record")
		self.records[ref] = kind, copy.deepcopy(value)


class Fake:
	def __init__(self, outputs=None):
		self.outputs = iter(outputs or ["done"])
		self.calls = []

	def start(self, context, request):
		self.calls.append(request["prompt"])
		yield {"type": "ScriptEnded", "status": "Succeeded", "output": next(self.outputs)}


class PlanningTests(unittest.TestCase):
	def setUp(self):
		self.temp = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp.cleanup)
		self.root = Path(self.temp.name)
		self.plan = self.root / ".auto/plans/demo"
		self.plan.mkdir(parents=True)
		(self.plan / "plan.md").write_text(documentText({"id": "demo", "goal": "demo"}, "# Demo"))
		self.engine = Fake()
		self.store = MemoryKnowledge()
		self.ctx = Context(project=self.root, plan=self.plan, engines={"fake": self.engine}, config={"engine": "fake"}, knowledge=self.store)

	def add(self, id, process="work", **meta):
		value = {"id": id, "title": id, "sections": {"Process": process}, **meta}
		PlanRevise(self.ctx, self.plan, [{"op": "add", "task": value}], "test")

	def testDependencyLifecycleAndAggregate(self):
		self.add("first")
		self.add("second", inputs={"upstream": "task:first"})
		self.assertEqual(PlanReady(self.ctx), ["first"])
		self.assertEqual(TaskStart(self.ctx, "second")["status"], "Blocked")
		first = TaskStart(self.ctx, "first")
		self.assertEqual(first["status"], "Succeeded", first)
		self.engine.outputs = iter(["second output"])
		second = TaskStart(self.ctx, "second")
		self.assertEqual(second["status"], "Succeeded", second)
		root = TaskStart(self.ctx, "root")
		self.assertEqual(root["status"], "Succeeded", root)
		self.assertEqual(RunOutput(self.ctx, root), {"first": "done", "second": "second output"})
		self.assertEqual(PlanStatus(self.ctx)["status"], "Succeeded")
		with self.assertRaises(AutoError):
			TaskUpdate(self.ctx, "first", {"title": "changed"})

	def testChecksAndUsage(self):
		self.add("skipped", sections={"Pre": "!?false", "Process": "never"})
		self.assertEqual(TaskStart(self.ctx, "skipped")["status"], "Skipped")
		self.assertEqual(len(self.engine.calls), 0)
		self.add("checked", sections={"Process": "work", "Post": "!?test -s {{ run.output }}", "Evaluation": "!?printf '{\"score\":0.2,\"feedback\":\"weak\"}'"})
		run = TaskStart(self.ctx, "checked")
		self.assertEqual(run["status"], "Succeeded", run)
		self.assertEqual(run["evaluation"]["score"], .2)
		self.assertGreater(run["usage"]["time"], 0)

	def testRunObserverReceivesActivityWithoutChangingJournal(self):
		self.add("observed", budget={"cost": 1})
		events = []
		run = TaskStart(self.ctx.derive(observe=events.append), "observed")
		self.assertEqual(run["status"], "Succeeded")
		self.assertEqual([event["type"] for event in events], ["RunStarted", "RunPhase", "ScriptEnded", "RunFinished"])
		self.assertEqual(events[0]["budget"], {"cost": 1})
		self.assertEqual(events[-1]["run"]["id"], run["id"])
		journalText = (self.plan / "runs" / run["id"] / "journal.jsonl").read_text()
		self.assertNotIn("RunStarted", journalText)
		self.assertNotIn("RunPhase", journalText)

	def testCyclesAndInvalidElaborationDoNotWrite(self):
		text = """---
task: root
---
## task: a
```yaml
title: A
inputs: {b: 'task:b'}
```
### Process
Work
## task: b
```yaml
title: B
inputs: {a: 'task:a'}
```
### Process
Work
"""
		elaboration = ElaborationParse(self.ctx, text)
		self.assertTrue(ElaborationValidate(self.ctx, self.plan, elaboration))
		with self.assertRaises(AutoError):
			ElaborationApply(self.ctx, self.plan, elaboration, "test-run")
		self.assertEqual(journal(self.plan), [])
		self.assertFalse((self.plan / "tasks").exists())

	def testElaborationSingleRevisionWithGaps(self):
		elaboration = {"task": "root", "tasks": [{"id": "a", "title": "A", "sections": {"Process": "work"}}], "gaps": [{"name": "audience", "kind": "decision", "question": "Who?", "needed_by": ["a"]}]}
		revision = ElaborationApply(self.ctx, self.plan, elaboration, "elaborator-run")
		self.assertEqual(ElaborationApply(self.ctx, self.plan, elaboration, "elaborator-run"), revision)
		self.assertEqual([op["op"] for op in revision["operations"]], ["add", "raise"])
		self.assertEqual(PlanReady(self.ctx), [])
		gap = GapList(self.ctx)[0]
		GapDecide(self.ctx, gap["id"], "developers", "Target audience")
		self.assertEqual(PlanReady(self.ctx), ["a"])
		PlanRepair(self.ctx)
		self.assertEqual(len(journal(self.plan)), 2)

	def testRecoverStagedRevision(self):
		self.store.fail = True
		with self.assertRaises(AutoError):
			GapRaise(self.ctx, self.plan, {"kind": "information", "question": "Which?", "needed_by": ["root"]})
		self.assertTrue((self.plan / ".pending.json").exists())
		PlanRepair(self.ctx)
		PlanRepair(self.ctx)
		self.assertEqual(len(GapList(self.ctx)), 1)
		self.assertEqual(len(journal(self.plan)), 1)

	def testRepairGroupsOrphanGaps(self):
		from auto.features.plan.gaps import prepareGap
		for question in ("First?", "Second?"):
			value = prepareGap(self.ctx, self.plan, {"kind": "information", "question": question, "needed_by": ["root"]}, "shared-revision")
			self.store.write("gap", value)
		PlanRepair(self.ctx)
		PlanRepair(self.ctx)
		self.assertEqual(len(journal(self.plan)), 1)
		self.assertEqual(len(journal(self.plan)[0]["operations"]), 2)

	def testQuestionResumeWithoutReexecutingProcess(self):
		class QuestionEngine:
			def start(inner, context, request):
				if "session" not in request:
					request["session"] = "one-session"
					yield {"type": "ScriptQuestion", "name": "audience", "content": "Who?"}
				else:
					yield {"type": "ScriptEnded", "status": "Succeeded", "output": request["answer"]}
		self.ctx.engines["fake"] = QuestionEngine()
		self.add("question")
		run = TaskStart(self.ctx, "question")
		self.assertEqual(run["status"], "Waiting")
		GapDecide(self.ctx, run["gap"], "developers", "Chosen")
		self.assertEqual(RunGet(self.ctx, run)["status"], "Succeeded")
		self.assertEqual(RunOutput(self.ctx, run), "developers")

	def testChangedAggregateMustRunAgain(self):
		self.add("first")
		TaskStart(self.ctx, "first")
		firstRoot = TaskStart(self.ctx, "root")
		self.add("second")
		self.assertEqual(PlanStatus(self.ctx)["status"], "Active")
		self.engine.outputs = iter(["second"])
		TaskStart(self.ctx, "second")
		self.assertEqual(PlanReady(self.ctx), ["root"])
		secondRoot = TaskStart(self.ctx, "root")
		self.assertNotEqual(firstRoot["id"], secondRoot["id"])

	def testBudgetAllocationAndExhaustion(self):
		(self.plan / "plan.md").write_text(documentText({"id": "demo", "goal": "Demo", "budget": {"tokens": 100}}, "# Demo"))
		elaboration = {"task": "root", "tasks": [{"id": id, "title": id, "sections": {"Process": "work"}} for id in ("a", "b")]}
		ElaborationApply(self.ctx, self.plan, elaboration, "elaboration")
		from auto.features.task.operations import TaskGet
		self.assertEqual(TaskGet(self.ctx, "a").meta["budget"]["tokens"], 50)
		self.add("zero", budget={"time": 0})
		run = TaskStart(self.ctx, "zero")
		self.assertEqual(run["status"], "TimedOut")
		self.assertEqual(len(self.engine.calls), 0)

	def testApprovalPinsAndReassessment(self):
		self.add("first", hints={"gate": "approval"})
		run = TaskStart(self.ctx, "first")
		self.assertEqual(run["status"], "Succeeded", run)
		gap = GapList(self.ctx)[0]
		self.assertEqual(PlanReady(self.ctx), [])
		before = (self.plan / "runs" / run["id"] / "receipt.json").read_bytes()
		self.assertTrue(RunCheck(self.ctx, run, {"content": "!?true"})["pass"])
		self.assertEqual((self.plan / "runs" / run["id"] / "receipt.json").read_bytes(), before)
		(self.plan / "runs" / run["id"] / run["outputFile"]).write_text("changed")
		with self.assertRaises(AutoError):
			GapDecide(self.ctx, gap["id"], "approve", "Approved")
		self.assertEqual(GapList(self.ctx)[0]["status"], "Open")

	def testRepairAfterMemoCommittedBeforeJournal(self):
		gap = GapRaise(self.ctx, self.plan, {"kind": "decision", "question": "Which?", "needed_by": ["root"]})
		with patch("auto.features.plan.store.append", side_effect=OSError("crash")):
			with self.assertRaises(OSError):
				GapDecide(self.ctx, gap, "yes", "Choice")
		self.assertEqual(GapList(self.ctx)[0]["status"], "Resolved")
		PlanRepair(self.ctx)
		PlanRepair(self.ctx)
		self.assertEqual([op["op"] for row in journal(self.plan) for op in row["operations"]], ["raise", "resolve"])

# EOF
