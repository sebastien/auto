"""Run input overrides and readiness use the same selected task dependencies."""

import unittest

from auto.features.plan.operations import PlanStatus, dependencyGraph, tasksAt
from auto.features.plan.gaps import GapDecide, GapList
from auto.features.task.operations import RunAnswer, RunGet, TaskReady, TaskStart
from auto.utils.data import AutoError
import test_004_01_integration_planning as fixtures


class RunInputTests(unittest.TestCase):
	setUp = fixtures.PlanningTests.setUp
	add = fixtures.PlanningTests.add

	def testDirectInputsCanReplaceAnUnreadyDeclaredDependency(self):
		self.add("producer")
		self.add("reader", process="Read {{ payload }}", inputs={"payload": "task:producer"})
		self.assertFalse(TaskReady(self.ctx, "reader"))
		self.assertTrue(TaskReady(self.ctx.derive(inputs={"payload": "Replacement"}), "reader"))
		run = TaskStart(self.ctx, "reader", {"payload": "Replacement"})
		self.assertEqual(run["status"], "Succeeded", run)
		self.assertIn("Read Replacement", self.engine.calls[-1])
		self.assertFalse(TaskReady(self.ctx, "reader"))
		self.assertEqual(self.ctx.inputs, {})

	def testAddedInputDependenciesBlockBeforeAnyExecution(self):
		self.add("producer")
		marker = self.root / "process-effect"
		self.add("reader", process="!printf effect > " + str(marker) + "\nRead {{ payload }}", inputs={"payload": "Default"})
		run = TaskStart(self.ctx, "reader", {"payload": "task:producer"})
		self.assertEqual(run["status"], "Blocked", run)
		self.assertEqual(self.engine.calls, [])
		self.assertFalse(marker.exists())
		self.engine.outputs = iter(["Produced", "Read"])
		self.assertEqual(TaskStart(self.ctx, "producer")["status"], "Succeeded")
		run = TaskStart(self.ctx, "reader", {"payload": "task:producer"})
		self.assertEqual(run["status"], "Succeeded", run)
		self.assertIn("Read Produced", self.engine.calls[-1])
		self.assertTrue(marker.exists())
		self.assertTrue(any(pin["ref"] == "auto:demo/task/producer" for pin in run["inputs"]))

	def testContextInputsApplyOnlyToTheSelectedTaskAndDirectInputsWin(self):
		self.add("producer")
		self.add("reader", process="Read {{ payload }}", inputs={"payload": "Default"})
		context = self.ctx.derive(inputs={"payload": "task:producer"})
		self.assertFalse(TaskReady(context, "reader"))
		self.engine.outputs = iter(["Replacement", "Produced", "Read"])
		run = TaskStart(context, "reader", {"payload": "Direct"})
		self.assertEqual(run["status"], "Succeeded", run)
		self.assertIn("Read Direct", self.engine.calls[-1])
		self.assertEqual(TaskStart(self.ctx, "producer")["status"], "Succeeded")
		self.assertTrue(TaskReady(context, "reader"))
		run = TaskStart(context, "reader")
		self.assertEqual(run["status"], "Succeeded", run)
		self.assertIn("Read Produced", self.engine.calls[-1])
		self.assertEqual(dependencyGraph(context, tasksAt(self.plan))["reader"], set())
		self.assertEqual(PlanStatus(context)["tasks"], PlanStatus(self.ctx)["tasks"])

	def testRunInputDependencyCyclesFailBeforeExecution(self):
		self.add("reader", process="Read {{ payload }}", inputs={"payload": "Default"})
		self.add("producer", inputs={"source": "task:reader"})
		with self.assertRaisesRegex(AutoError, "Dependency cycle"):
			TaskStart(self.ctx, "reader", {"payload": "task:producer"})
		self.assertEqual(self.engine.calls, [])

	def testDirectInputsOverrideDependenciesInsideDefinitionTransclusions(self):
		self.add("producer")
		self.add("library", process="Read {{ payload }}", inputs={"payload": "task:producer"})
		self.add("reader", process="@task:library/definition#Process")
		run = TaskStart(self.ctx, "reader", {"payload": "Replacement"})
		self.assertEqual(run["status"], "Succeeded", run)
		self.assertIn("Read Replacement", self.engine.calls[-1])
		self.assertFalse(TaskReady(self.ctx, "reader"))

	def testCallerAddedDependencyStillRequiresItsOutputApproval(self):
		self.add("producer", hints={"gate": "approval"})
		self.add("reader", process="Read {{ payload }}", inputs={"payload": "Default"})
		self.engine.outputs = iter(["Produced", "Read"])
		self.assertEqual(TaskStart(self.ctx, "producer")["status"], "Succeeded")
		run = TaskStart(self.ctx, "reader", {"payload": "task:producer"})
		self.assertEqual(run["status"], "Blocked", run)
		self.assertEqual(len(self.engine.calls), 1)
		GapDecide(self.ctx, GapList(self.ctx)[0]["id"], True, "Approved output")
		run = TaskStart(self.ctx, "reader", {"payload": "task:producer"})
		self.assertEqual(run["status"], "Succeeded", run)
		self.assertIn("Read Produced", self.engine.calls[-1])

	def testSelectedInputsSurviveAQuestionWithoutResolvingShellValuesAgain(self):
		class QuestionEngine:
			def start(inner, context, request):
				if "session" not in request:
					request["session"] = "selected-input-session"
					self.assertIn("Read Selected", request["prompt"])
					yield {"type": "ScriptQuestion", "content": "Continue?"}
				else:
					self.assertIn("Read Selected", request["prompt"])
					yield {"type": "ScriptEnded", "status": "Succeeded", "output": request["answer"]}
		self.ctx.engines["fake"] = QuestionEngine()
		self.add("producer")
		self.add("reader", process="Read {{ payload }}", inputs={"payload": "task:producer"})
		counter = self.root / "input-resolutions"
		run = TaskStart(self.ctx, "reader", {"payload": "!printf x >> " + str(counter) + "; printf Selected"})
		self.assertEqual(run["status"], "Waiting", run)
		self.assertEqual(counter.read_text(), "x")
		RunAnswer(self.ctx.derive(inputs={"payload": "Changed"}), run, "Continue", "Confirmed")
		self.assertEqual(RunGet(self.ctx, run)["status"], "Succeeded")
		self.assertEqual(counter.read_text(), "x")

	def testMalformedExplicitInputsFailBeforeCreatingARun(self):
		self.add("reader")
		for inputs in ([], False, "value", {1: "value"}, {"params": "reserved"}):
			with self.subTest(inputs=inputs), self.assertRaises(AutoError):
				TaskStart(self.ctx, "reader", inputs)
		self.assertEqual(self.engine.calls, [])
		self.assertFalse((self.plan / "runs").exists())
