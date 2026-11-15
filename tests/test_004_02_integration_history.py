"""Historical definitions and immutable completed-run reassessment."""

import unittest
from unittest.mock import patch

from auto.features.plan.operations import PlanRevise
from auto.features.plan.gaps import GapDecide, GapRaise
from auto.features.elaboration.operations import ElaborationApply
from auto.features.elaboration.sources import TemplateSources
from auto.features.elaboration.model import createElaborationFromDict
from auto.features.task.operations import RunCheck, RunEvaluate, RunGet, TaskDependencies, TaskGet, TaskStart, TaskValidate
from auto.runtime.operations import Resolve, Shell
from auto.features.script.operations import ScriptExpand
from auto.runtime.model import Content
from auto.utils.data import AutoError, digest, documentText
import test_004_01_integration_planning as fixtures
from support import rootFile


class HistoryTests(unittest.TestCase):
	setUp = fixtures.PlanningTests.setUp
	add = fixtures.PlanningTests.add

	def testRemovedDefinitionResolvesThroughBothReferenceForms(self):
		self.add("work")
		original = (self.plan / "tsk/work.md").read_bytes()
		TaskStart(self.ctx, "work")
		PlanRevise(self.ctx, self.plan, [{"op": "remove", "task": "work"}], "Remove completed work")
		for ref in ("task:work/definition", "auto:demo/task/work/definition"):
			content = Resolve(self.ctx, ref)
			self.assertEqual(content.ref, "auto:demo/task/tsk-work/definition")
			self.assertEqual(content.data, original)
			self.assertEqual(content.digest, digest(original))
			self.assertEqual(content.path, self.plan / "tsk/work.md")
		with self.assertRaisesRegex(AutoError, "Task not found"):
			TaskGet(self.ctx, "work")
		with self.assertRaises(AutoError):
			TaskStart(self.ctx, "work")
		with self.assertRaises(AutoError):
			TaskStart(self.ctx, {"id": "work", "path": str(content.path), "sections": {"Process": "Restart removed work"}})
		with self.assertRaises(AutoError):
			self.add("work")

	def testRemovedRunCanBeReassessedWithoutChangingItsReceipt(self):
		self.add("work", sections={"Process": "Produce", "Post": "!?true", "Evaluation": "!?printf '%s' '{\"score\":0.5,\"feedback\":\"Reviewed\"}'"})
		run = TaskStart(self.ctx, "work")
		self.assertEqual(run["status"], "Succeeded")
		receipt = self.plan / "runs" / run["id"] / "receipt.json"
		original = receipt.read_bytes()
		PlanRevise(self.ctx, self.plan, [{"op": "remove", "task": "work"}], "Remove completed work")
		self.assertTrue(RunCheck(self.ctx, run, "Post")["pass"])
		self.assertEqual(RunEvaluate(self.ctx, run), {"score": 0.5, "feedback": "Reviewed"})
		self.assertEqual(receipt.read_bytes(), original)
		self.assertGreaterEqual(RunGet(self.ctx, run)["usage"]["time"], run["usage"]["time"])

	def testArchivedCheckpointsRespectCurrentShellRulesAndCallerRestrictions(self):
		self.ctx.defaults["policy"] = {"shell.run": "confirm"}
		self.add("work", effects=["shell.run"], sections={"Process": "Produce", "Post": "!?true"})
		ref = "auto:demo/task/work/definition"
		gap = GapRaise(self.ctx, self.plan, {"kind": "approval", "question": "Confirm shell.run (confirm)?", "needed_by": ["work"],
			"effects": ["shell.run"], "subjects": [{"ref": ref, "digest": Resolve(self.ctx, ref).digest}]})
		GapDecide(self.ctx, gap, "approve", "Approve this definition")
		run = TaskStart(self.ctx, "work")
		self.assertEqual(run["status"], "Succeeded")
		PlanRevise(self.ctx, self.plan, [{"op": "remove", "task": "work"}], "Archive checked task")
		self.assertTrue(RunCheck(self.ctx, run, "Post")["pass"])
		with self.assertRaisesRegex(AutoError, "Shell execution is disabled"):
			RunCheck(self.ctx.derive(allowShell=False), run, "Post")
		self.ctx.defaults["policy"] = {"shell.run": "deny"}
		with self.assertRaisesRegex(AutoError, "Shell execution is disabled"):
			RunCheck(self.ctx, run, "Post")

	def testArchivedModelReassessmentDoesNotBypassCurrentPermissions(self):
		self.add("work")
		run = TaskStart(self.ctx, "work")
		PlanRevise(self.ctx, self.plan, [{"op": "remove", "task": "work"}], "Archive completed task")
		class CheckEngine:
			acted, closed = False, False
			def start(inner, context, request):
				try:
					answer = yield {"type": "PolicyRequest", "effect": "net.write", "action": "publish review"}
					inner.acted = answer is True
					yield {"type": "ScriptEnded", "status": "Succeeded", "output": {"pass": True, "reason": "Reviewed"}}
				finally:
					inner.closed = True
		engine = CheckEngine()
		self.ctx.engines["fake"] = engine
		self.ctx.defaults["policy"] = {"net.write": "deny"}
		with self.assertRaisesRegex(AutoError, "requires authorization for net.write"):
			RunCheck(self.ctx, run, {"content": "Review output"})
		self.assertFalse(engine.acted)
		self.assertTrue(engine.closed)

	def testArchivedMaterializedSourcesRemainAvailableToRunChecks(self):
		file = self.root / "template.md"
		file.write_text(documentText({"title": "Captured"}, "## Process\nProduce\n## Post\n@./check.md\n"))
		(self.root / "check.md").write_text("!?true\n")
		value = createElaborationFromDict({"task": "root", "tasks": [{"id": "work", "from": str(file)}]})
		ElaborationApply(self.ctx, self.plan, value, "capture-historical-task")
		task = TaskGet(self.ctx, "work")
		source = next(ref for ref in task.meta["_template_sources"] if ref.startswith("auto:demo/task/tsk-work/source/"))
		original = Resolve(self.ctx, source)
		run = TaskStart(self.ctx, "work")
		PlanRevise(self.ctx, self.plan, [{"op": "remove", "task": "work"}], "Archive captured task")
		file.unlink()
		(self.root / "check.md").unlink()
		for ref in (source, source.replace("auto:demo/task/", "task:")):
			self.assertEqual(Resolve(self.ctx, ref).data, original.data)
		self.assertTrue(RunCheck(self.ctx, run, "Post")["pass"])

	def testNewRunDefinitionPinsRemainResolvableAfterRemoval(self):
		self.add("work")
		run = TaskStart(self.ctx, "work")
		ref = "auto:demo/task/tsk-work/definition"
		pin = next(pin for pin in run["inputs"] if pin["ref"] == ref)
		PlanRevise(self.ctx, self.plan, [{"op": "remove", "task": "work"}], "Archive pinned definition")
		self.assertEqual(Resolve(self.ctx, pin["ref"]).digest, pin["digest"])

	def testTaskModelsCannotReplaceStoredDefinitionsAtStartup(self):
		self.engine.outputs = iter(["First", "Second"])
		self.add("work", process="Stored definition")
		TaskStart(self.ctx, "work")
		snapshot = TaskGet(self.ctx, "work")
		snapshot.sections["Process"] = "Unrecorded replacement"
		self.assertEqual(TaskStart(self.ctx, snapshot)["status"], "Succeeded")
		self.assertIn("Stored definition", self.engine.calls[-1])
		self.assertNotIn("Unrecorded replacement", self.engine.calls[-1])

	def testRootSourcesUseTheRootDefinitionAndMissingSourcesReportAutoErrors(self):
		text = "Root source"
		sources = TemplateSources(self.ctx, task="root")
		ref = sources.compiledSource(Content("./fragment.md", text.encode(), digest(text.encode()), self.root / "fragment.md"), text)
		rootFile(self.plan).write_text(documentText({"id": "demo", "goal": "demo", "_template_sources": sources.records}, "Root"))
		self.assertEqual(Resolve(self.ctx, ref).text, text)
		with self.assertRaisesRegex(AutoError, "Cannot resolve"):
			Resolve(self.ctx, "task:missing/source/" + "0" * 64)

	def testArchivedDefinitionKeepsItsOriginalRelativeReferenceOrigin(self):
		(self.plan / "tsk").mkdir(exist_ok=True)
		(self.plan / "tsk/data.txt").write_text("Definition data")
		self.add("work", process="Data={{ data }}", inputs={"data": "./data.txt"})
		PlanRevise(self.ctx, self.plan, [{"op": "remove", "task": "work"}], "Archive relative definition")
		self.assertIn("Data=Definition data", ScriptExpand(self.ctx, "task:work/definition"))

	def testDefinitionsAreAssetsRatherThanOutputDependencies(self):
		self.add("old", process="Retained instructions")
		PlanRevise(self.ctx, self.plan, [{"op": "remove", "task": "old"}], "Archive unused definition")
		self.add("reader", process="Review {{ definition }}", inputs={"definition": "task:old/definition"})
		self.assertEqual(TaskDependencies(self.ctx, "reader"), [])
		self.assertEqual(TaskStart(self.ctx, "reader")["status"], "Succeeded")
		self.assertIn("Retained instructions", self.engine.calls[-1])

	def testDefinitionsOfTasksAddedTogetherDoNotRequireOutputRuns(self):
		PlanRevise(self.ctx, self.plan, [
			{"op": "add", "task": {"id": "producer", "title": "Producer", "sections": {"Process": "Produce later"}}},
			{"op": "add", "task": {"id": "reader", "title": "Reader", "inputs": {"definition": "auto:demo/task/producer/definition"},
				"sections": {"Process": "Read {{ definition }}"}}}], "Add definition reader")
		self.assertEqual(TaskDependencies(self.ctx, "reader"), [])
		self.assertEqual(TaskStart(self.ctx, "reader")["status"], "Succeeded")
		with self.assertRaises(AutoError):
			self.add("missing", inputs={"definition": "task:unknown/definition"})

	def testASelfDefinitionInputDoesNotCreateAnOutputCycle(self):
		self.add("work", process="Review {{ definition }}", inputs={"definition": "task:work/definition"})
		self.assertEqual(TaskDependencies(self.ctx, "work"), [])
		self.assertEqual(TaskStart(self.ctx, "work")["status"], "Succeeded")

	def testDefinitionPinInspectionCannotExecuteShellBackedResolvers(self):
		self.add("work")
		def resolve(context, ref):
			data = Shell(context, "printf definition")[0].encode()
			return Content(ref, data, digest(data), self.plan / "tsk/work.md")
		context = self.ctx.derive(resolvers={"task:": resolve})
		with patch("auto.integration.process.capture") as shell:
			with self.assertRaisesRegex(AutoError, "Shell execution is disabled"):
				TaskStart(context, "work")
			self.assertEqual(shell.call_count, 0)

	def testMissingCrossPlanDefinitionsReturnDomainErrors(self):
		for ref in ("auto:missing", "auto:missing/task/work/definition"):
			with self.subTest(ref=ref), self.assertRaises(AutoError):
				Resolve(self.ctx, ref)
		self.assertTrue(TaskValidate(self.ctx, {"id": "reader", "inputs": {"definition": "auto:missing/task/work/definition"}, "sections": {"Process": "Read"}}))
