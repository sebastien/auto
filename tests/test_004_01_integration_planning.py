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

	def testFailedEvaluationDoesNotChangeRunStatus(self):
		self.add("evaluated", eval="false")
		run = TaskStart(self.ctx, "evaluated")
		self.assertEqual(run["status"], "Succeeded")
		self.assertEqual(run["evaluation_failure"]["kind"], "output")
		self.assertIn("TaskEvaluationFailed", (self.plan / "runs" / run["id"] / "journal.jsonl").read_text())

	def testFrontmatterChecksAddToBodyAndApply(self):
		self.add("front", pre="true", sections={"Pre": "!?false", "Process": "never"})
		self.assertEqual(TaskStart(self.ctx, "front")["status"], "Skipped")
		self.add("applied", post=["grep -q done", "test -s {{ run.output }}"], apply="cat > applied.txt",
			eval="printf '{\"score\":0.5,\"feedback\":\"ok\"}'", sections={"Process": "work"})
		run = TaskStart(self.ctx, "applied")
		self.assertEqual(run["status"], "Succeeded", run)
		self.assertEqual((self.root / "applied.txt").read_text(), "done")
		self.assertEqual(run["evaluation"]["score"], .5)
		self.add("unapplied", post="false", apply="touch never.txt", sections={"Process": "work"})
		self.assertEqual(TaskStart(self.ctx, "unapplied")["status"], "Failed")
		self.assertFalse((self.root / "never.txt").exists())

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

	def testRepairReconcilesConcurrentMemoChanges(self):
		gap = GapRaise(self.ctx, self.plan, {"kind": "decision", "question": "Which?", "needed_by": ["root"]})
		ref = f"auto:demo/gap/{gap}"
		write = self.store.write
		def concurrent(kind, value, expected=""):
			if kind == "gap" and expected:
				self.store.records[ref][1]["note"] = "Added independently"
				raise AutoError("stale write")
			write(kind, value, expected)
		with patch.object(self.store, "write", side_effect=concurrent):
			with self.assertRaises(AutoError):
				GapDecide(self.ctx, gap, "yes", "Choice")
		PlanRepair(self.ctx)
		value = self.store.get(ref)
		self.assertEqual(value["status"], "Resolved")
		self.assertEqual(value["note"], "Added independently")
		self.assertEqual([op["op"] for row in journal(self.plan) for op in row["operations"]], ["raise", "resolve"])

	def testRepairRejectsConflictingMemoChanges(self):
		gap = GapRaise(self.ctx, self.plan, {"kind": "decision", "question": "Which?", "needed_by": ["root"]})
		ref = f"auto:demo/gap/{gap}"
		write = self.store.write
		def concurrent(kind, value, expected=""):
			if kind == "gap" and expected:
				self.store.records[ref][1]["status"] = "Dismissed"
				raise AutoError("stale write")
			write(kind, value, expected)
		with patch.object(self.store, "write", side_effect=concurrent):
			with self.assertRaises(AutoError):
				GapDecide(self.ctx, gap, "yes", "Choice")
		with self.assertRaisesRegex(AutoError, "Conflicting Memo update"):
			PlanRepair(self.ctx)
		self.assertEqual(self.store.get(ref)["status"], "Dismissed")
		self.assertTrue((self.plan / ".pending.json").exists())

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

	def testParentBudgetAndPlanIdentity(self):
		self.add("parent", budget={"tokens": 5})
		elaboration = {"task": "parent", "tasks": [{"id": "child", "title": "Child", "budget": {"tokens": 100}, "sections": {"Process": "work"}}]}
		self.assertTrue(ElaborationValidate(self.ctx, self.plan, elaboration))
		PlanRevise(self.ctx, self.plan, [{"op": "update", "fields": {"goal": "Updated"}}], "Change goal")
		self.assertEqual(PlanStatus(self.ctx)["id"], "demo")

	def testElaboratorBudgetIsClamped(self):
		from auto.features.elaboration.operations import ElaborationStart
		elaborator = self.root / "elaborator.md"
		elaborator.write_text(documentText({"title": "Elaborator"}, "## Process\n\nWork"))
		(self.plan / "plan.md").write_text(documentText({"id": "demo", "goal": "Demo", "budget": {"tokens": 10},
			"elaborator": {"task": str(elaborator), "budget": {"tokens": 100}}}, "# Demo"))
		run = ElaborationStart(self.ctx, self.plan, "root")
		self.assertEqual(run["budget"]["tokens"], 10)

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

	def testPolicyCheckpointAndRework(self):
		elaboration = {"task": "root", "tasks": [{"id": "publish", "title": "Publish", "effects": ["vcs.push"], "sections": {"Process": "work"}}]}
		self.assertIn("checkpoint", str(ElaborationValidate(self.ctx, self.plan, elaboration)))
		elaboration["gaps"] = [{"name": "checkpoint", "kind": "approval", "question": "Confirm vcs.push (confirm)?", "needed_by": ["publish"], "effects": ["vcs.push"], "subjects": ["task:publish/definition"]}]
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, elaboration), [])
		ElaborationApply(self.ctx, self.plan, elaboration, "elaborator-run")
		gap = GapList(self.ctx)[0]
		self.assertEqual(TaskStart(self.ctx, "publish")["status"], "Blocked")
		GapDecide(self.ctx, gap["id"], "no", "Rejected")
		self.assertEqual(TaskStart(self.ctx, "publish")["status"], "Blocked")
		from auto.runtime.operations import Resolve
		ref = "auto:demo/task/publish/definition"
		gapId = GapRaise(self.ctx, self.plan, {"kind": "approval", "question": "Confirm vcs.push (confirm)?", "needed_by": ["publish"], "effects": ["vcs.push"], "subjects": [{"ref": ref, "digest": Resolve(self.ctx, ref).digest}]})
		GapDecide(self.ctx, gapId, "approve", "Approved")
		self.assertEqual(TaskStart(self.ctx, "publish")["status"], "Succeeded")

	def testDeniedEffectRequiresExplicitGrant(self):
		from auto.features.plan.policy import PolicyGrant
		self.ctx.defaults["policy"] = {"vcs": "deny"}
		elaboration = {"task": "root", "tasks": [{"id": "draft", "title": "Draft", "effects": ["vcs.push"], "sections": {"Process": "work"}}]}
		self.assertIn("grant decision gap", str(ElaborationValidate(self.ctx, self.plan, elaboration)))
		self.add("publish", effects=["vcs.push"])
		gap = GapRaise(self.ctx, self.plan, {"kind": "decision", "question": "Grant vcs.push (deny)?", "needed_by": ["publish"]})
		decision = GapDecide(self.ctx, gap, {"grant": "vcs.push", "scope": "auto:demo/task/publish"}, "Approved")
		self.assertEqual(TaskStart(self.ctx, "publish")["status"], "Blocked")
		PolicyGrant(self.ctx, self.plan, "vcs.push", "auto:demo/task/publish", decision["id"])
		self.assertEqual(TaskStart(self.ctx, "publish")["status"], "Succeeded")

	def testChangedDefinitionInvalidatesCheckpoint(self):
		from auto.runtime.operations import Resolve
		self.add("publish", effects=["vcs.push"])
		ref = "auto:demo/task/publish/definition"
		gap = GapRaise(self.ctx, self.plan, {"kind": "approval", "question": "Confirm vcs.push (confirm)?", "needed_by": ["publish"], "effects": ["vcs.push"], "subjects": [{"ref": ref, "digest": Resolve(self.ctx, ref).digest}]})
		GapDecide(self.ctx, gap, "approve", "Approved")
		self.assertIn("publish", PlanReady(self.ctx))
		TaskUpdate(self.ctx, "publish", {"title": "Changed"})
		self.assertNotIn("publish", PlanReady(self.ctx))
		self.assertEqual(TaskStart(self.ctx, "publish")["status"], "Blocked")

	def testShellPolicyAppliesToLifecycleCommands(self):
		self.ctx.defaults["policy"] = {"shell.run": "deny"}
		self.add("shell", apply="touch should-not-exist", sections={"Process": "work"})
		run = TaskStart(self.ctx, "shell")
		self.assertEqual(run["status"], "Failed")
		self.assertIn('"PolicyDenied"', (self.plan / "runs" / run["id"] / "journal.jsonl").read_text())
		self.assertFalse((self.root / "should-not-exist").exists())

	def testRuntimePolicyRequestsWaitAndResume(self):
		class PolicyEngine:
			enforceableEffects = {"net.write"}
			def start(inner, context, request):
				if "answer" not in request:
					request["session"] = "policy-session"
					yield {"type": "PolicyRequest", "effect": "net.write", "action": "post message"}
				else:
					yield {"type": "ScriptEnded", "status": "Succeeded", "output": str(request["answer"])}
		self.ctx.engines["fake"] = PolicyEngine()
		self.add("notify")
		from auto.features.plan.policy import PolicyResolve
		self.assertNotIn("net.write", PolicyResolve(self.ctx, self.plan, "notify")["unenforceable"])
		self.assertIn("vcs.push", PolicyResolve(self.ctx, self.plan, "notify")["unenforceable"])
		run = TaskStart(self.ctx, "notify")
		self.assertEqual(run["status"], "Waiting")
		with self.assertRaises(AutoError):
			GapDecide(self.ctx.derive(author="agent"), run["gap"], "approve", "Not human")
		GapDecide(self.ctx, run["gap"], "approve", "Approved")
		self.assertEqual(RunOutput(self.ctx, run), "True")
		self.assertIn('"PolicyRequested"', (self.plan / "runs" / run["id"] / "journal.jsonl").read_text())

	def testRuntimeDeniedEffectNeedsStructuredGrant(self):
		class PolicyEngine:
			def start(inner, context, request):
				if "answer" not in request:
					yield {"type": "PolicyRequest", "effect": "vcs.push", "action": "push"}
				else:
					yield {"type": "ScriptEnded", "status": "Succeeded", "output": str(request["answer"])}
		self.ctx.engines["fake"] = PolicyEngine()
		self.ctx.defaults["policy"] = {"vcs": "deny"}
		self.add("notify")
		run = TaskStart(self.ctx, "notify")
		GapDecide(self.ctx, run["gap"], "approve", "Insufficient")
		self.assertEqual(RunOutput(self.ctx, run), "False")
		self.assertIn('"PolicyDenied"', (self.plan / "runs" / run["id"] / "journal.jsonl").read_text())
		run = TaskStart(self.ctx, "notify")
		decision = GapDecide(self.ctx, run["gap"], {"grant": "vcs.push", "scope": "auto:demo/task/notify"}, "Explicit grant")
		self.assertEqual(RunOutput(self.ctx, run), "True")
		self.assertTrue(any(op.get("source") == decision["identity"] for row in journal(self.plan) for op in row["operations"] if op["op"] == "grant"))

	def testAllowedRuntimeEffectDoesNotWait(self):
		class PolicyEngine:
			def start(inner, context, request):
				answer = yield {"type": "PolicyRequest", "effect": "repo.read", "action": "read"}
				yield {"type": "ScriptEnded", "status": "Succeeded", "output": str(answer)}
		self.ctx.engines["fake"] = PolicyEngine()
		self.add("reader")
		run = TaskStart(self.ctx, "reader")
		self.assertEqual(run["status"], "Succeeded")
		self.assertEqual(RunOutput(self.ctx, run), "True")
		self.assertEqual(GapList(self.ctx), [])

	def testOpenCodePermissionRequestCreatesCheckpoint(self):
		from auto.integration.opencode import OpenCode
		class Stub(OpenCode):
			def __init__(inner):
				inner.calls = []
				inner.replied = False
			def api(inner, context, method, path, data=None, config=None):
				inner.calls.append((method, path, data))
				if "/permission/" in path and path.endswith("/reply"):
					inner.replied = True
				if path.endswith("/permission"):
					return {"data": [] if inner.replied else [{"id": "per1", "action": "edit", "resources": ["file.txt"]}]}
				if path.endswith("/form"):
					return {"data": []}
				if "/message?" in path:
					return {"data": [{"id": "msg", "type": "assistant", "content": [{"type": "text", "text": "Done"}]}, {"id": "idle", "type": "idle", "outcome": "succeeded"}], "cursor": {}}
				return {}
		engine = Stub()
		self.ctx.engines["fake"] = engine
		self.add("edit")
		run = TaskStart(self.ctx, "edit")
		self.assertEqual(run["status"], "Waiting")
		self.assertEqual(GapList(self.ctx)[0]["effects"], ["repo.write"])
		GapDecide(self.ctx, run["gap"], "approve", "Approved")
		self.assertEqual(RunOutput(self.ctx, run), "Done")
		self.assertTrue(any("/permission/per1/reply" in path and data == {"decision": "once"} for _, path, data in engine.calls))

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
