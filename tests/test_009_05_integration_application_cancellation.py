"""Application cancellation preserves successful runs and stops task selection."""

import shlex
import sys
import threading
import time
import unittest
from unittest.mock import patch

from auto.features.elaboration.operations import ElaborationApply, ElaborationStart
from auto.features.orchestration.operations import PlanContinue
from auto.features.plan.gaps import GapDecide, applyPending
from auto.features.plan.control import PlanHold, PlanRelease
from auto.features.plan.store import journal
from auto.features.task.operations import RunGet, RunList
from auto.utils.data import AutoError, documentText, yamlText
import test_004_01_integration_planning as fixtures


class ApplicationCancellationTests(unittest.TestCase):
	setUp = fixtures.PlanningTests.setUp

	def pending(self, parameter="Stored", *, question=False):
		elaborator = self.root / "elaborator.md"
		elaborator.write_text(documentText({"title": "Elaborate"}, "## Process\nProduce tasks\n"))
		(self.plan / "plan.md").write_text(documentText({"id": "demo", "goal": "Demo",
			"elaborator": {"task": str(elaborator)}}, "# Demo"))
		template = self.root / "template.md"
		template.write_text(documentText({"title": "Work", "params": {"label": parameter}}, "## Process\n{{ params.label }}\n"))
		output = documentText({"task": "root"}, "## task: work\n```yaml\n" +
			yamlText({"title": "Work", "from": str(template)}) + "```\n")
		self.engine.outputs = iter([output])
		if question:
			calls = self.engine.calls
			class QuestionEngine:
				def start(inner, context, request):
					calls.append(request["prompt"])
					if "answer" not in request:
						yield {"type": "ScriptQuestion", "content": "Apply the elaboration?"}
					else:
						yield {"type": "ScriptEnded", "status": "Succeeded", "output": output}
			self.ctx.engines["fake"] = QuestionEngine()
		run = ElaborationStart(self.ctx.derive(variables={"applyElaboration": True}), self.plan, "root")
		self.assertEqual(run["status"], "Waiting" if question else "Succeeded", run)
		return run, self.plan / "runs" / run["id"] / "receipt.json", template

	def testCancellationDuringParameterShellStopsWithoutApplyingOrStartingTasks(self):
		marker = self.root / "application-started"
		script = self.root / "slow-parameter.py"
		script.write_text("from pathlib import Path\nimport time\nPath(" + repr(str(marker)) + ").write_text('started')\ntime.sleep(10)\nprint('Stored')\n")
		run, receipt, template = self.pending("!" + shlex.quote(sys.executable) + " " + shlex.quote(str(script)))
		original = receipt.read_bytes()
		events = list(PlanContinue(self.ctx.derive(cancelled=marker.exists), self.plan))
		self.assertTrue(marker.exists())
		self.assertEqual(events[-1]["data"]["stop"], "cancelled")
		self.assertNotIn("error", events[-1]["data"])
		self.assertEqual(journal(self.plan), [])
		self.assertFalse((self.plan / "tasks/work.md").exists())
		self.assertEqual(len(self.engine.calls), 1)
		self.assertEqual(receipt.read_bytes(), original)
		self.assertEqual([value["id"] for value in RunList(self.ctx)], [run["id"]])
		# The successful Elaborator stays available for a later explicit continuation.
		template.write_text(documentText({"title": "Work", "params": {"label": "Stored"}}, "## Process\n{{ params.label }}\n"))
		self.engine.outputs = iter(["Worked"])
		self.assertEqual(list(PlanContinue(self.ctx, self.plan))[-1]["data"]["stop"], "ended")
		self.assertEqual(len(self.engine.calls), 2)
		self.assertIn("Stored", self.engine.calls[-1])
		self.assertEqual(len(journal(self.plan)), 1)
		self.assertEqual(receipt.read_bytes(), original)

	def testCancellationAfterApplicationCommitPreventsTheNextRun(self):
		_, receipt, _ = self.pending()
		original = receipt.read_bytes()
		cancelled = threading.Event()
		def apply(context, run):
			result = applyPending(context, run)
			cancelled.set()
			return result
		with patch("auto.features.orchestration.operations.applyPending", side_effect=apply):
			events = list(PlanContinue(self.ctx.derive(cancelled=cancelled.is_set), self.plan))
		self.assertEqual(events[-1]["data"]["stop"], "cancelled")
		self.assertEqual(RunList(self.ctx, {"task": "work"}), [])
		self.assertEqual(len(self.engine.calls), 1)
		self.assertEqual(len(journal(self.plan)), 1)
		self.assertEqual(receipt.read_bytes(), original)

	def testAlreadyCancelledApplicationDoesNotResolveParameters(self):
		marker = self.root / "must-not-exist"
		template = self.root / "template.md"
		template.write_text(documentText({"title": "Work", "params": {"label": "!touch " + str(marker)}}, "## Process\nWork\n"))
		with patch("auto.integration.process.capture", return_value=("", "", 0)) as shell:
			with self.assertRaises(AutoError) as error:
				ElaborationApply(self.ctx.derive(cancelled=lambda: True), self.plan,
					{"task": "root", "tasks": [{"id": "work", "from": str(template)}]}, "cancelled-application")
			shell.assert_not_called()
		self.assertEqual(error.exception.kind, "cancelled")
		self.assertFalse(marker.exists())
		self.assertEqual(journal(self.plan), [])

	def testStopHoldInterruptsAnApplicationWithoutAnActiveTaskRun(self):
		marker, release = self.root / "application-started", self.root / "release-parameter"
		script = self.root / "held-parameter.py"
		script.write_text("from pathlib import Path\nimport time\nPath(" + repr(str(marker)) + ").write_text('started')\n"
			"while not Path(" + repr(str(release)) + ").exists():\n    time.sleep(.01)\nprint('Stored')\n")
		_, receipt, _ = self.pending("!" + shlex.quote(sys.executable) + " " + shlex.quote(str(script)))
		original = receipt.read_bytes()
		cancelled, result, errors = threading.Event(), [], []
		def continuePlan():
			try:
				result.extend(PlanContinue(self.ctx.derive(cancelled=cancelled.is_set), self.plan))
			except BaseException as error:
				errors.append(error)
		worker = threading.Thread(target=continuePlan)
		worker.start()
		try:
			deadline = time.monotonic() + 5
			while not marker.exists() and worker.is_alive() and time.monotonic() < deadline:
				cancelled.wait(.01)
			self.assertTrue(marker.exists(), errors)
			PlanHold(self.ctx, self.plan, "Stop", "Stop pending application")
			worker.join(3)
			self.assertFalse(worker.is_alive(), "Stop did not interrupt the parameter process")
		finally:
			cancelled.set()
			release.write_text("finish")
			worker.join(5)
		self.assertEqual(errors, [])
		self.assertEqual(result[-1]["data"]["stop"], "held")
		self.assertFalse((self.plan / "tasks/work.md").exists())
		self.assertEqual(len(self.engine.calls), 1)
		self.assertEqual(receipt.read_bytes(), original)
		self.assertFalse(any(row.get("elaborationRun") for row in journal(self.plan)))

	def testStopHoldInterruptsApplicationImmediatelyAfterAnElaboratorQuestionResumes(self):
		marker, release = self.root / "resumed-application", self.root / "release-parameter"
		script = self.root / "resumed-parameter.py"
		script.write_text("from pathlib import Path\nimport time\nPath(" + repr(str(marker)) + ").write_text('started')\n"
			"while not Path(" + repr(str(release)) + ").exists():\n    time.sleep(.01)\nprint('Stored')\n")
		run, receipt, template = self.pending("!" + shlex.quote(sys.executable) + " " + shlex.quote(str(script)), question=True)
		GapDecide(self.ctx, run["gap"], "yes", "Proceed")
		cancelled, events, errors = threading.Event(), [], []
		def continuePlan():
			try:
				events.extend(PlanContinue(self.ctx.derive(cancelled=cancelled.is_set), self.plan))
			except BaseException as error:
				errors.append(error)
		worker = threading.Thread(target=continuePlan)
		worker.start()
		original = None
		try:
			deadline = time.monotonic() + 5
			while not marker.exists() and worker.is_alive() and time.monotonic() < deadline:
				cancelled.wait(.01)
			self.assertTrue(marker.exists(), errors)
			self.assertEqual(RunGet(self.ctx, run)["status"], "Succeeded")
			original = receipt.read_bytes()
			PlanHold(self.ctx, self.plan, "Stop", "Interrupt resumed application")
			worker.join(3)
			self.assertFalse(worker.is_alive(), "Stop did not supervise the resumed Elaborator application")
		finally:
			cancelled.set()
			release.write_text("finish")
			worker.join(5)
		self.assertEqual(errors, [])
		self.assertEqual(events[-1]["data"]["stop"], "held")
		self.assertEqual(receipt.read_bytes(), original)
		self.assertFalse((self.plan / "tasks/work.md").exists())
		self.assertFalse(any(row.get("elaborationRun") for row in journal(self.plan)))
		self.assertEqual(len(self.engine.calls), 2)
		# Recovery applies the successful output rather than running the Elaborator again.
		template.write_text(documentText({"title": "Work", "params": {"label": "Stored"}}, "## Process\n{{ params.label }}\n"))
		self.ctx.engines["fake"] = self.engine
		self.engine.outputs = iter(["Worked"])
		PlanRelease(self.ctx, self.plan, "Recover application")
		self.assertEqual(list(PlanContinue(self.ctx, self.plan))[-1]["data"]["stop"], "ended")
		self.assertEqual(receipt.read_bytes(), original)
		self.assertEqual(len(self.engine.calls), 3)
		self.assertEqual(sum(row.get("elaborationRun") == run["id"] for row in journal(self.plan)), 1)
