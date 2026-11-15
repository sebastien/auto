"""Module: test_planning — Run lifecycle, revisions, elaboration and repair."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from auto.features.elaboration.operations import ElaborationApply, ElaborationParse, ElaborationValidate, ElaborationStart
from auto.features.memory.config import agentsFor, housekeepingFor, memoryPolicy, validateAgents
from auto.features.plan.gaps import DecisionRecord, GapDecide, GapDismiss, GapList, GapRaise, PlanRepair
from auto.features.plan.model import createDecisionFromDict
from auto.features.plan.operations import PlanAbandon, PlanList, PlanRevise, PlanStatus, PlanReady
from auto.features.plan.store import journal
from auto.features.task.operations import TaskStart, RunGet, RunOutput, TaskUpdate, RunAnswer, RunCheck, RunEvaluate
from auto.runtime.model import Content, Context
from auto.runtime.operations import Resolve
from auto.utils.data import AutoError, digest, documentText, jsonText, yamlLoad
from support import rootFile


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
		self.plan = self.root / ".auto/demo"
		self.plan.mkdir(parents=True)
		rootFile(self.plan).write_text(documentText({"id": "demo", "goal": "demo"}, "# Demo"))
		self.engine = Fake()
		self.store = MemoryKnowledge()
		self.ctx = Context(project=self.root, plan=self.plan, engines={"fake": self.engine}, config={"engine": "fake"}, knowledge=self.store)

	def add(self, id, process="work", **meta):
		value = {"id": id, "title": id, "sections": {"Process": process}, **meta}
		PlanRevise(self.ctx, self.plan, [{"op": "add", "task": value}], "test")

	def testAgentRevisionAndConfiguration(self):
		self.ctx.defaults = {"memory": {"agents": {"ux": {"agent": "ux/agent:Designer", "budget": {"tokens": 100}}}},
			"housekeeping": {"allowance": {"tokens": 1000, "per": "day"}}}
		self.assertEqual(housekeepingFor(self.ctx), {"tokens": 1000, "per": "day"})
		self.assertEqual(list(agentsFor(self.ctx)), ["ux"])
		self.assertEqual(validateAgents(self.ctx, ["auto:agent/ux"]), ["auto:agent/ux"])
		PlanRevise(self.ctx, self.plan, [{"op": "agents", "agents": ["auto:agent/ux"]}], "assign agent")
		self.assertEqual(PlanStatus(self.ctx)["status"], "Draft")
		self.assertIn("auto:agent/ux", rootFile(self.plan).read_text())
		with self.assertRaises(AutoError):
			PlanRevise(self.ctx, self.plan, [{"op": "agents", "agents": ["auto:agent/missing"]}], "invalid agent")
		with self.assertRaisesRegex(AutoError, "agents revision"):
			PlanRevise(self.ctx, self.plan, [{"op": "update", "fields": {"agents": ["auto:agent/missing"]}}], "bypass")
		self.assertIn("auto:agent/ux", rootFile(self.plan).read_text())

	def testAgentConfigurationRejectsDuplicateArtifactAndBadAllowance(self):
		self.ctx.defaults = {"memory": {"agents": {"ux": {"agent": "ux/agent:Designer"}, "other": {"agent": "ux/agent:Designer"}}}}
		with self.assertRaises(AutoError):
			agentsFor(self.ctx)
		self.ctx.defaults = {"housekeeping": {"allowance": {"tokens": 100}}}
		with self.assertRaises(AutoError):
			housekeepingFor(self.ctx)

	def testAgentMemoryPolicyCannotLoosenUserRestriction(self):
		self.ctx.defaults = {"memory": {"agents": {"ux": {"agent": "ux/agent:Designer", "policy": {"memory.promote": "allow"}}}}}
		self.ctx.policyLayers = [("user", {"memory.promote": "deny"})]
		self.assertEqual(memoryPolicy(self.ctx, "ux")["memory.promote"], "deny")
		self.assertEqual(memoryPolicy(self.ctx, "ux")["memory.observe"], "allow")
		self.ctx.policyLayers = []
		self.assertEqual(memoryPolicy(self.ctx, "ux")["memory.promote"], "allow")

	def testAutoAgentReferenceReadsMemoArtifact(self):
		class ArtifactKnowledge(MemoryKnowledge):
			def readReference(self, ref):
				self.assertedRef = ref
				return Content("memo:" + ref, b"persona", "revision-1", Path("agent.md"))
		self.ctx.defaults = {"memory": {"agents": {"ux": {"agent": "ux/agent:Designer"}}}}
		self.ctx.knowledge = ArtifactKnowledge()
		content = Resolve(self.ctx, "auto:agent/ux")
		self.assertEqual(self.ctx.knowledge.assertedRef, "ux/agent:Designer")
		self.assertEqual((content.ref, content.text, content.digest), ("auto:agent/ux", "persona", "revision-1"))
		with self.assertRaises(AutoError):
			Resolve(self.ctx, "auto:agent/unknown")

	def testDependencyLifecycleAndAggregate(self):
		self.add("first")
		self.add("second", inputs={"upstream": "task:first"})
		self.assertEqual(PlanReady(self.ctx), ["tsk-first"])
		self.assertEqual(TaskStart(self.ctx, "second")["status"], "Blocked")
		first = TaskStart(self.ctx, "first")
		self.assertEqual(first["status"], "Succeeded", first)
		self.engine.outputs = iter(["second output"])
		second = TaskStart(self.ctx, "second")
		self.assertEqual(second["status"], "Succeeded", second)
		root = TaskStart(self.ctx, "root")
		self.assertEqual(root["status"], "Succeeded", root)
		self.assertEqual(RunOutput(self.ctx, root), {"tsk-first": "done", "tsk-second": "second output"})
		self.assertEqual(PlanStatus(self.ctx)["status"], "Succeeded")
		with self.assertRaises(AutoError):
			TaskUpdate(self.ctx, "first", {"title": "changed"})

	def testPlanListSummarisesAndFilters(self):
		self.add("first")
		broken = self.root / ".auto/broken"
		broken.mkdir()
		rootFile(broken).write_text("---\nid: [unclosed\n---\n")
		plans = {plan["id"]: plan for plan in PlanList(self.ctx)}
		self.assertEqual(set(plans), {"demo", "broken"})
		self.assertEqual({key: plans["demo"][key] for key in ("status", "goal", "tasks", "done", "gaps", "hold")},
			{"status": "Draft", "goal": "demo", "tasks": 2, "done": 0, "gaps": 0, "hold": None})
		revisions = journal(self.plan)
		self.assertLessEqual(plans["demo"]["created"], revisions[0]["date"])
		self.assertGreaterEqual(plans["demo"]["updated"], revisions[-1]["date"])
		run = TaskStart(self.ctx, "first")
		self.assertEqual(PlanList(self.ctx, {"id": "demo"})[0]["updated"], run["ended"])
		self.assertEqual(plans["broken"]["status"], "Unreadable")
		self.assertEqual([plan["id"] for plan in PlanList(self.ctx, {"status": "Active"})], ["demo"])

	def testDecisionWithoutGapIsDurableAndResolvable(self):
		answer = {"database": "sqlite"}
		decision = DecisionRecord(self.ctx, self.plan, "Which database?", answer, "Keep deployment simple",
			concerns=["architecture"], source="chat:session/1")
		self.assertNotIn("gap", decision)
		self.assertEqual(decision["author"], "human")
		self.assertEqual(decision["answer"], answer)
		self.assertEqual(decision["source"], "chat:session/1")
		self.assertEqual(decision["concerns"], ["architecture"])
		self.assertEqual(createDecisionFromDict(decision).asDict(), decision)
		self.assertEqual(self.store.get(decision["identity"])["answer"], answer)
		self.assertEqual(journal(self.plan)[-1]["operations"], [{"op": "record", "decision": decision["id"]}])
		self.assertEqual(journal(self.plan)[-1]["id"], decision["revision"])
		PlanRepair(self.ctx)
		self.assertEqual(len(journal(self.plan)), 1)

	def testDecisionDoesNotReopenEndedPlan(self):
		for status in ("Succeeded", "Abandoned"):
			with self.subTest(status=status):
				if status == "Succeeded":
					self.assertEqual(TaskStart(self.ctx, "root")["status"], status)
				else:
					PlanAbandon(self.ctx, self.plan, "No further work")
				DecisionRecord(self.ctx, self.plan, "Archive records?", True, "Retain the history")
				self.assertEqual(PlanStatus(self.ctx)["status"], status)

	def testRepairRecordsOrphanDecisionsOnceAndKeepsGapsIndependent(self):
		gap = GapRaise(self.ctx, self.plan, {"question": "Choose?", "needed_by": ["root"]})
		decision = DecisionRecord(self.ctx, self.plan, "Use local storage?", True, "Fits the scope")
		# Simulate a durable Memo record whose journal append was lost.
		(self.plan / "journal.jsonl").write_text(jsonText(journal(self.plan)[0]) + "\n")
		PlanRepair(self.ctx)
		PlanRepair(self.ctx)
		rows = journal(self.plan)
		self.assertEqual([row["operations"][0]["op"] for row in rows], ["raise", "record"])
		self.assertEqual(rows[-1]["id"], decision["revision"])
		self.assertEqual(GapList(self.ctx)[0]["status"], "Open")
		resolved = GapDecide(self.ctx, gap, "yes", "Confirmed")
		self.assertEqual(resolved["gap"], gap)
		self.assertEqual(len(journal(self.plan)), 3)

	def testRepairRunsBeforeNextDecisionWrite(self):
		first = DecisionRecord(self.ctx, self.plan, "First?", "a", "Chosen")
		(self.plan / "journal.jsonl").write_text("")
		second = DecisionRecord(self.ctx, self.plan, "Second?", "b", "Chosen")
		self.assertEqual([row["operations"][0]["decision"] for row in journal(self.plan)], [first["id"], second["id"]])

	def testDecisionStagedWriteRecoversAfterStorageFailure(self):
		self.store.fail = True
		with self.assertRaisesRegex(AutoError, "Injected storage failure"):
			DecisionRecord(self.ctx, self.plan, "Use local storage?", True, "Fits the scope")
		self.assertEqual(journal(self.plan), [])
		PlanRepair(self.ctx)
		PlanRepair(self.ctx)
		self.assertEqual(len(journal(self.plan)), 1)
		self.assertEqual(len(self.store.list("auto:demo", "decision")), 1)

	def testGapDecisionInheritsConcerns(self):
		gap = GapRaise(self.ctx, self.plan, {"question": "Which storage?", "needed_by": ["root"], "concerns": ["architecture"]})
		self.assertEqual(GapDecide(self.ctx, gap, "local", "Simple")["concerns"], ["architecture"])

	def testInvalidDecisionDoesNotWrite(self):
		for fields in ({"question": ""}, {"concerns": "architecture"}, {"concerns": [1]}, {"source": {"ref": "chat"}}):
			with self.subTest(fields=fields), self.assertRaises(AutoError):
				DecisionRecord(self.ctx, self.plan, **{"question": "Which?", "answer": True, "rationale": "Chosen", **fields})
		self.assertEqual(journal(self.plan), [])
		self.assertEqual(self.store.records, {})

	def testCanonicalDependenciesBlockUntilUpstreamSucceeded(self):
		self.add("first")
		self.add("second", inputs={"upstream": "auto:demo/task/first"})
		self.assertEqual(PlanReady(self.ctx), ["tsk-first"])
		self.assertEqual(TaskStart(self.ctx, "second")["status"], "Blocked")
		self.assertEqual(self.engine.calls, [])
		self.assertEqual(TaskStart(self.ctx, "first")["status"], "Succeeded")
		self.assertEqual(PlanReady(self.ctx), ["tsk-second"])
		self.engine.outputs = iter(["second output"])
		self.assertEqual(TaskStart(self.ctx, "second")["status"], "Succeeded")

	def testCanonicalDependenciesAreDiscoveredInTranscludedScripts(self):
		self.add("first")
		fragment = self.root / "fragment.md"
		fragment.write_text("@auto:demo/task/first#Summary\n")
		self.add("second", process="@" + str(fragment))
		self.assertEqual(PlanReady(self.ctx), ["tsk-first"])
		before = journal(self.plan)
		with self.assertRaisesRegex(AutoError, "cycle"):
			PlanRevise(self.ctx, self.plan, [{"op": "edit", "task": {
				"id": "first", "inputs": {"cycle": "auto:demo/task/second"},
				"sections": {"Process": "work"}}}], "introduce cycle")
		self.assertEqual(journal(self.plan), before)

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

	def testRepairRerunsTheProcessWithTheRejectedOutputAndIssues(self):
		self.add("repaired", post="grep -q good || { echo 'Output must say good'; false; }", repair=1, sections={"Process": "Write it"})
		self.engine.outputs = iter(["bad", "good"])
		run = TaskStart(self.ctx, "repaired")
		self.assertEqual(run["status"], "Succeeded", run)
		self.assertEqual(RunOutput(self.ctx, run), "good")
		first, second = self.engine.calls
		self.assertNotIn("## Correction", first)
		self.assertIn("## Correction", second)
		self.assertIn("Output must say good", second)
		self.assertIn("<<<REJECTED\nbad\nREJECTED>>>", second)
		self.assertNotIn("```", second)
		events = [json.loads(line) for line in (self.plan / "runs" / run["id"] / "journal.jsonl").read_text().splitlines()]
		self.assertEqual([event["attempt"] for event in events if event["type"] == "TaskRepairing"], [1])

	def testRepairAttemptsAreBounded(self):
		self.add("unrepaired", post="false", sections={"Process": "Write it"})
		self.add("exhausted", post="false", repair=2, sections={"Process": "Write it"})
		self.engine.outputs = iter(["one", "two", "three", "four"])
		self.assertEqual(TaskStart(self.ctx, "unrepaired")["status"], "Failed")
		self.assertEqual(len(self.engine.calls), 1)
		run = TaskStart(self.ctx, "exhausted")
		self.assertEqual((run["status"], run["failure"]["kind"]), ("Failed", "output"))
		self.assertEqual(len(self.engine.calls), 4)

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
		self.assertEqual(sorted((self.plan / "tsk").glob("*.md")), [rootFile(self.plan)])

	def testElaborationToleratesFenceWithoutNewline(self):
		# Seen live: a model wrote the opening fence and first key on one line.
		elaboration = ElaborationParse(self.ctx, "---task: root\nsummary: S\n---\n## task: a\n```yaml\ntitle: A\n```\n### Process\nWork\n")
		self.assertEqual((elaboration.task, elaboration.summary, [item["id"] for item in elaboration.tasks]), ("root", "S", ["tsk-a"]))

	def testElaborationToleratesAReplyWrappedInOneCodeBlock(self):
		# Seen live: a model fenced its whole reply, as the template is shown.
		body = "---\ntask: root\n---\n## task: a\n```yaml\ntitle: A\n```\n### Process\nWork\n"
		for fence in ("````markdown\n", "```\n"):
			elaboration = ElaborationParse(self.ctx, fence + body + fence.split("markdown")[0].strip() + "\n")
			self.assertEqual((elaboration.task, [item["id"] for item in elaboration.tasks]), ("root", ["tsk-a"]))
		# Inner YAML blocks alone are not mistaken for a wrapping block.
		self.assertEqual(ElaborationParse(self.ctx, body).tasks[0]["title"], "A")

	def testElaborationContextReadsBareRelativePathsAsProjectPaths(self):
		# Seen live: models list files they read without the `./` of a PATH.
		(self.root / "tools").mkdir()
		(self.root / "tools/check.sh").write_text("true\n")
		# Seen live too: prose notes, which are not references and are dropped.
		elaboration = ElaborationParse(self.ctx, "---\ntask: root\ncontext:\n  - tools/check.sh\n  - auto:catalogue/task/audit\n  - ./README.md\n"
			"  - plan goal and state supplied in the prompt\n  - \"confirmed decision: accent `#3a7d44`\"\n"
			"---\n## task: a\n```yaml\ntitle: A\n```\n### Process\nWork\n")
		self.assertEqual(elaboration.context, ["./tools/check.sh", "auto:catalogue/task/audit", "./README.md"])
		self.assertEqual(ElaborationParse(self.ctx, "---\ntask: root\ncontext:\n---\n## task: a\n```yaml\ntitle: A\n```\n### Process\nWork\n").context, [])
		elaboration.context = elaboration.context[:1]
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, elaboration), [])

	def testElaborationSingleRevisionWithGaps(self):
		elaboration = {"task": "root", "tasks": [{"id": "a", "title": "A", "sections": {"Process": "work"}}], "gaps": [{"name": "audience", "kind": "decision", "question": "Who?", "needed_by": ["a"]}]}
		revision = ElaborationApply(self.ctx, self.plan, elaboration, "elaborator-run")
		self.assertEqual(ElaborationApply(self.ctx, self.plan, elaboration, "elaborator-run"), revision)
		self.assertEqual([op["op"] for op in revision["operations"]], ["add", "raise"])
		self.assertEqual(PlanReady(self.ctx), [])
		gap = GapList(self.ctx)[0]
		GapDecide(self.ctx, gap["id"], "developers", "Target audience")
		self.assertEqual(PlanReady(self.ctx), ["tsk-a"])
		PlanRepair(self.ctx)
		self.assertEqual(len(journal(self.plan)), 2)

	def testResolvedGapAnswersReachTheTasksThatNeedThem(self):
		elaboration = {"task": "root", "tasks": [{"id": "a", "title": "A", "sections": {"Process": "Build it"}},
			{"id": "b", "title": "B", "sections": {"Process": "Other work"}}],
			"gaps": [{"name": "colour", "kind": "input", "question": "Which accent\ncolour?", "needed_by": ["a"]}]}
		ElaborationApply(self.ctx, self.plan, elaboration, "elaborator-run")
		GapDecide(self.ctx, GapList(self.ctx)[0]["id"], "#3a7d44", "Brand guide")
		self.engine.outputs = iter(["one", "two"])
		self.assertEqual(TaskStart(self.ctx, "a")["status"], "Succeeded")
		self.assertEqual(TaskStart(self.ctx, "b")["status"], "Succeeded")
		answered, other = self.engine.calls
		self.assertIn("## Decisions", answered)
		self.assertIn("- Question: Which accent colour?\n  Answer: #3a7d44\n  Rationale: Brand guide\n", answered)
		self.assertLess(answered.index("#3a7d44"), answered.index("Build it"))
		self.assertNotIn("## Decisions", other)

	def testGapAnswersReachTheDecompositionOfTheTaskThatNeededThem(self):
		self.add("site", "Build it")
		gap = GapRaise(self.ctx, self.plan, {"kind": "input", "question": "Which colour?", "needed_by": ["site"]})
		GapDecide(self.ctx, gap, "#3a7d44", "Brand guide")
		PlanRevise(self.ctx, self.plan, [{"op": "add", "task": {"id": "styles", "title": "Styles", "parent": "site",
			"sections": {"Process": "Style it"}}}], "Decompose")
		TaskStart(self.ctx, "styles")
		self.assertIn("Answer: #3a7d44", self.engine.calls[0])

	def testElaborationValidationChecksTheTargetAndContextEntries(self):
		self.add("site", "Build it")
		elaboration = {"task": "other", "tasks": [{"id": "a", "title": "A", "sections": {"Process": "work"}}]}
		messages = [item["message"] for item in ElaborationValidate(self.ctx, self.plan, elaboration, "site")]
		self.assertTrue(any("must target tsk-site" in message for message in messages), messages)
		elaboration = {"task": "site", "context": [{"plan": "root"}], "tasks": [{"id": "a", "title": "A", "sections": {"Process": "work"}}]}
		messages = [item["message"] for item in ElaborationValidate(self.ctx, self.plan, elaboration, "site")]
		self.assertTrue(any("Invalid context entry" in message for message in messages), messages)

	def testGapAnswersExcludeApprovals(self):
		self.add("a", "Build it")
		gap = GapRaise(self.ctx, self.plan, {"kind": "approval", "question": "Sign off?", "needed_by": ["a"],
			"subjects": [{"ref": "task:a/definition", "digest": Resolve(self.ctx, "task:a/definition").digest}]})
		GapDecide(self.ctx, gap, "approve", "Looks good")
		TaskStart(self.ctx, "a")
		self.assertNotIn("## Decisions", self.engine.calls[0])

	def testValidationExplainsGapsUsedAsPlanInputs(self):
		elaboration = {"task": "root", "tasks": [{"id": "a", "title": "A", "inputs": {"colour": "plan:colour"}, "sections": {"Process": "{{ colour }}"}}],
			"gaps": [{"name": "colour", "kind": "input", "question": "Which colour?", "needed_by": ["a"]}]}
		messages = [violation["message"] for violation in ElaborationValidate(self.ctx, self.plan, elaboration)]
		self.assertTrue(any("colour is a gap" in message for message in messages), messages)

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
		RunAnswer(self.ctx, run, "developers", "Chosen")
		self.assertEqual(RunGet(self.ctx, run)["status"], "Succeeded")
		self.assertEqual(RunOutput(self.ctx, run), "developers")

	def testQuestionFieldsRemainVisibleInGapRecords(self):
		fields = [{"key": "count", "type": "integer", "minimum": 1}]
		class QuestionEngine:
			def start(inner, context, request):
				yield {"type": "ScriptQuestion", "content": "How many?", "fields": fields}
		self.ctx.engines["fake"] = QuestionEngine()
		self.add("question")
		run = TaskStart(self.ctx, "question")
		self.assertEqual(run["status"], "Waiting")
		self.assertEqual(GapList(self.ctx)[0]["fields"], fields)

	def testCancellingInteractiveQuestionCancelsWaitingEngineSession(self):
		from auto.app.operations import EXPOSURES, resumeResult
		class QuestionEngine:
			cancelled = False
			def start(inner, context, request):
				request["session"] = "question-session"
				yield {"type": "ScriptQuestion", "content": "Choose"}
			def cancel(inner, context, request):
				inner.cancelled = request["session"] == "question-session"
		engine = QuestionEngine()
		self.ctx.engines["fake"] = engine
		self.add("question")
		run = TaskStart(self.ctx, "question")
		def cancelQuestion(event):
			raise KeyboardInterrupt
		with self.assertRaises(KeyboardInterrupt):
			resumeResult(self.ctx, next(item for item in EXPOSURES if item.name == "TaskStart"), run, cancelQuestion)
		self.assertTrue(engine.cancelled)
		self.assertEqual(RunGet(self.ctx, run)["status"], "Cancelled")

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
		rootFile(self.plan).write_text(documentText({"id": "demo", "goal": "Demo", "budget": {"tokens": 100}}, "# Demo"))
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

	def testCustomElaboratorReceivesPolicyAndCompleteGoalChecks(self):
		custom = self.root / "custom-elaborator.md"
		custom.write_text("---\nid: custom\n---\n## Process\n{{ goal }}\n{{ policy }}\n")
		PlanRevise(self.ctx, self.plan, [{"op": "update", "fields": {
			"elaborator": {"task": str(custom)}, "post": ["touch post-marker"], "eval": ["touch eval-marker"]}}], "Configure")
		run = ElaborationStart(self.ctx, self.plan, "root")
		self.assertEqual(run["status"], "Succeeded", run)
		state = json.loads((self.plan / "runs" / run["id"] / "state.json").read_text())
		# The goal text comes first, then its definition of done as YAML.
		text, _, details = state["inputs"]["goal"].partition("\n\n")
		self.assertEqual(text, "demo")
		goal = yamlLoad(details)
		self.assertEqual(goal["post"]["commands"], ["touch post-marker"])
		self.assertEqual(goal["evaluation"]["commands"], ["touch eval-marker"])
		self.assertIn("rules", yamlLoad(state["inputs"]["policy"]))
		self.assertIn("touch post-marker", self.engine.calls[0])
		self.assertFalse((self.root / "post-marker").exists())
		self.assertFalse((self.root / "eval-marker").exists())

	def testElaboratorBudgetIsClamped(self):
		from auto.features.elaboration.operations import ElaborationStart
		elaborator = self.root / "elaborator.md"
		elaborator.write_text(documentText({"title": "Elaborator"}, "## Process\n\nWork"))
		rootFile(self.plan).write_text(documentText({"id": "demo", "goal": "Demo", "budget": {"tokens": 10},
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

	def testRejectedOutputGateKeepsDependentsBlocked(self):
		self.add("draft", hints={"gate": "approval"})
		self.add("publish", inputs={"draft": "task:draft"})
		run = TaskStart(self.ctx, "draft")
		receipt = self.plan / "runs" / run["id"] / "receipt.json"
		original = receipt.read_bytes()
		gap = GapList(self.ctx)[0]
		GapDecide(self.ctx, gap["id"], "reject", "Needs a better draft")
		self.assertEqual(GapList(self.ctx)[0]["status"], "Resolved")
		self.assertEqual(PlanStatus(self.ctx)["tasks"]["tsk-publish"], "Blocked")
		self.assertNotIn("publish", PlanReady(self.ctx))
		self.assertEqual(TaskStart(self.ctx, "publish")["status"], "Blocked")
		self.assertEqual(receipt.read_bytes(), original)
		self.assertEqual(RunGet(self.ctx, run)["status"], "Succeeded")
		# A new affirmative decision about that exact output can approve it.
		second = GapRaise(self.ctx, self.plan, {"kind": "approval", "question": "Approve after discussion?",
			"needed_by": ["publish"], "subjects": gap["subjects"]})
		GapDecide(self.ctx, second, True, "Accepted after discussion")
		self.assertIn("tsk-publish", PlanReady(self.ctx))

	def testRetryOutputRequiresFreshGateAndCanReplaceRejectedOutput(self):
		self.add("draft", hints={"gate": "approval"})
		self.add("publish", inputs={"draft": "task:draft"})
		first = TaskStart(self.ctx, "draft")
		GapDecide(self.ctx, GapList(self.ctx)[0]["id"], "no", "Try again")
		self.engine.outputs = iter(["better draft"])
		second = TaskStart(self.ctx, "draft")
		self.assertNotEqual(first["id"], second["id"])
		self.assertNotIn("publish", PlanReady(self.ctx))
		newGap = next(gap for gap in GapList(self.ctx) if gap["status"] == "Open")
		GapDecide(self.ctx, newGap["id"], "approve", "Improved")
		self.assertIn("tsk-publish", PlanReady(self.ctx))
		self.assertEqual(RunGet(self.ctx, first)["status"], "Succeeded")

	def testRootOutputGateAllowsExplicitRetryAfterRejection(self):
		PlanRevise(self.ctx, self.plan, [{"op": "update", "fields": {"hints": {"gate": "approval"}}}], "Review plan output")
		first = TaskStart(self.ctx, "root")
		GapDecide(self.ctx, GapList(self.ctx)[0]["id"], False, "Retry the result")
		second = TaskStart(self.ctx, "root")
		self.assertEqual(second["status"], "Succeeded")
		self.assertNotEqual(first["id"], second["id"])
		newGap = next(gap for gap in GapList(self.ctx) if gap["status"] == "Open")
		GapDecide(self.ctx, newGap["id"], True, "Accepted new attempt")
		self.assertEqual(PlanStatus(self.ctx)["status"], "Succeeded")

	def testApprovalExpiresWhenPinnedOutputChanges(self):
		self.add("draft", hints={"gate": "approval"})
		self.add("publish", inputs={"draft": "task:draft"})
		run = TaskStart(self.ctx, "draft")
		GapDecide(self.ctx, GapList(self.ctx)[0]["id"], True, "Accepted")
		self.assertIn("tsk-publish", PlanReady(self.ctx))
		(self.plan / "runs" / run["id"] / run["outputFile"]).write_text("Changed after review")
		self.assertNotIn("publish", PlanReady(self.ctx))
		self.assertEqual(PlanStatus(self.ctx)["tasks"]["tsk-publish"], "Blocked")
		self.assertEqual(TaskStart(self.ctx, "publish")["status"], "Blocked")

	def testDismissalExplicitlyWaivesRejectedOutputGate(self):
		self.add("draft", hints={"gate": "approval"})
		self.add("publish", inputs={"draft": "task:draft"})
		TaskStart(self.ctx, "draft")
		gap = GapList(self.ctx)[0]["id"]
		GapDecide(self.ctx, gap, False, "Initially declined")
		with self.assertRaises(AutoError):
			GapDismiss(self.ctx, gap, "")
		with self.assertRaises(AutoError):
			GapDismiss(self.ctx.derive(author="agent"), gap, "Cannot waive")
		GapDismiss(self.ctx, gap, "Human explicitly waives the review requirement")
		PlanRepair(self.ctx)
		self.assertEqual(GapList(self.ctx)[0]["status"], "Dismissed")
		self.assertIn("tsk-publish", PlanReady(self.ctx))

	def testNewDependentsAndMissingGateCannotBypassReview(self):
		self.add("draft", hints={"gate": "approval"})
		TaskStart(self.ctx, "draft")
		gap = GapList(self.ctx)[0]
		self.add("publish", inputs={"draft": "task:draft"})
		self.assertNotIn("publish", gap["needed_by"])
		self.assertEqual(TaskStart(self.ctx, "publish")["status"], "Blocked")
		GapDecide(self.ctx, gap["id"], True, "Accepted")
		self.assertIn("tsk-publish", PlanReady(self.ctx))
		self.store.records.pop(gap["identity"])
		self.assertNotIn("publish", PlanReady(self.ctx))

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

	def testPlanningGrantGapCanBeAnsweredWithoutSeparatePythonCall(self):
		self.ctx.defaults["policy"] = {"vcs": "deny"}
		elaboration = {"task": "root", "tasks": [{"id": "publish", "title": "Publish", "effects": ["vcs.push"], "sections": {"Process": "work"}}],
			"gaps": [{"name": "grant", "kind": "decision", "question": "Grant vcs.push (deny)?", "effects": ["vcs.push"], "needed_by": ["publish"]}]}
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, elaboration), [])
		ElaborationApply(self.ctx, self.plan, elaboration, "elaborator-run")
		gap = GapList(self.ctx)[0]
		answer = {"grant": "vcs.push", "scope": "auto:demo/task/publish"}
		with self.assertRaisesRegex(AutoError, "human decision"):
			GapDecide(self.ctx.derive(author="agent"), gap["id"], answer, "Cannot grant")
		for invalid in ({"grant": "vcs.commit", "scope": answer["scope"]}, {"grant": "vcs.push", "scope": "auto:other"}):
			with self.subTest(answer=invalid), self.assertRaises(AutoError):
				GapDecide(self.ctx, gap["id"], invalid, "Wrong grant")
		self.assertEqual(GapList(self.ctx)[0]["status"], "Open")
		decision = GapDecide(self.ctx, gap["id"], answer, "Publish explicitly authorized")
		self.assertEqual([op["op"] for op in journal(self.plan)[-1]["operations"]], ["resolve", "grant"])
		self.assertEqual(journal(self.plan)[-1]["author"], "human")
		self.assertEqual(journal(self.plan)[-1]["operations"][-1]["source"], decision["identity"])
		self.assertIn("tsk-publish", PlanReady(self.ctx))
		self.assertEqual(TaskStart(self.ctx, "publish")["status"], "Succeeded")

	def testChangedDefinitionInvalidatesCheckpoint(self):
		from auto.runtime.operations import Resolve
		self.add("publish", effects=["vcs.push"])
		ref = "auto:demo/task/publish/definition"
		gap = GapRaise(self.ctx, self.plan, {"kind": "approval", "question": "Confirm vcs.push (confirm)?", "needed_by": ["publish"], "effects": ["vcs.push"], "subjects": [{"ref": ref, "digest": Resolve(self.ctx, ref).digest}]})
		GapDecide(self.ctx, gap, "approve", "Approved")
		self.assertIn("tsk-publish", PlanReady(self.ctx))
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

	def testReassessmentShellChecksRespectCurrentPolicyAndCaller(self):
		self.add("checked", sections={"Process": "work", "Evaluation": "!?printf '{\"score\":1,\"feedback\":\"ok\"}'"})
		run = TaskStart(self.ctx, "checked")
		self.assertEqual(run["status"], "Succeeded")
		receipt = self.plan / "runs" / run["id"] / "receipt.json"
		original = receipt.read_bytes()
		for rule in ("confirm", "deny"):
			with self.subTest(rule=rule):
				rootFile(self.plan).write_text(documentText({"id": "demo", "goal": "demo", "policy": {"shell.run": rule}}, "# Demo"))
				with self.assertRaisesRegex(AutoError, "Shell execution is disabled"):
					RunCheck(self.ctx, run, {"content": "!?touch must-not-exist"})
				with self.assertRaisesRegex(AutoError, "Shell execution is disabled"):
					RunEvaluate(self.ctx, run)
		self.assertFalse((self.root / "must-not-exist").exists())
		rootFile(self.plan).write_text(documentText({"id": "demo", "goal": "demo"}, "# Demo"))
		self.ctx.defaults["policy"] = {"shell.run": "deny"}
		with self.assertRaisesRegex(AutoError, "Shell execution is disabled"):
			RunCheck(self.ctx, run, {"content": "!?touch must-not-exist"})
		self.ctx.defaults.pop("policy")
		with self.assertRaisesRegex(AutoError, "Shell execution is disabled"):
			RunCheck(self.ctx.derive(allowShell=False), run, {"content": "!?touch must-not-exist"})
		self.assertEqual(receipt.read_bytes(), original)
		self.assertGreater(RunGet(self.ctx, run)["usage"]["time"], run["usage"]["time"])

	def testReassessmentModelPermissionsAreCheckedBeforeActions(self):
		self.add("checked")
		run = TaskStart(self.ctx, "checked")
		receipt = self.plan / "runs" / run["id"] / "receipt.json"
		original = receipt.read_bytes()
		class CheckEngine:
			def start(inner, context, request):
				inner.policy = request.get("policy")
				inner.acted, inner.closed = False, False
				try:
					answer = yield {"type": "PolicyRequest", "effect": "net.write", "action": "post review"}
					inner.acted = answer is True
					yield {"type": "ScriptEnded", "status": "Succeeded", "output": {"pass": True, "reason": "Checked"}}
				finally:
					inner.closed = True
		engine = CheckEngine()
		self.ctx.engines["fake"] = engine
		for rule in ("confirm", "deny"):
			with self.subTest(rule=rule):
				rootFile(self.plan).write_text(documentText({"id": "demo", "goal": "demo", "policy": {"net.write": rule}}, "# Demo"))
				with self.assertRaisesRegex(AutoError, "requires authorization for net.write"):
					RunCheck(self.ctx, run, {"content": "Review the output"})
				self.assertEqual(engine.policy["rules"]["net.write"]["rule"], rule)
				self.assertFalse(engine.acted)
				self.assertTrue(engine.closed)
		rootFile(self.plan).write_text(documentText({"id": "demo", "goal": "demo", "policy": {"net.write": "allow"}}, "# Demo"))
		self.assertTrue(RunCheck(self.ctx, run, {"content": "Review the output"})["pass"])
		self.assertTrue(engine.acted)
		self.assertTrue(engine.closed)
		self.assertEqual(GapList(self.ctx), [])
		self.assertEqual(receipt.read_bytes(), original)

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
		RunAnswer(self.ctx, run, "approve", "Approved")
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
		RunAnswer(self.ctx, run, "approve", "Insufficient")
		self.assertEqual(RunOutput(self.ctx, run), "False")
		self.assertIn('"PolicyDenied"', (self.plan / "runs" / run["id"] / "journal.jsonl").read_text())
		run = TaskStart(self.ctx, "notify")
		decision = RunAnswer(self.ctx, run, {"grant": "vcs.push", "scope": "auto:demo/task/notify"}, "Explicit grant")
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
		from auto.integration.opencode.engine import OpenCode
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
		RunAnswer(self.ctx, run, "approve", "Approved")
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
