"""Cancellation prevents new process work and ignores late engine success."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from auto.features.script.operations import ScriptStart
from auto.integration.process import capture
from auto.runtime.model import Context, Limits
from auto.utils.data import AutoError
import test_002_01_unit_script as fixtures


class ScriptCancellationTests(unittest.TestCase):
	setUp = fixtures.ScriptTests.setUp
	script = fixtures.ScriptTests.script

	def testAlreadyCancelledScriptSkipsLoopValuesAndInputResolution(self):
		for meta in ({"inputs": {"data": "!printf data"}},
			{"each": {"item": "!printf first"}, "inputs": {"data": "!printf data"}}):
			with self.subTest(meta=meta), patch("auto.integration.process.subprocess.Popen",
				side_effect=AssertionError("Cancelled work must not spawn")) as spawn:
				result = list(ScriptStart(self.ctx.derive(cancelled=lambda: True), self.script("Work", meta)))
				self.assertEqual(result[-1]["status"], "Cancelled", result)
				spawn.assert_not_called()

	def testLateSuccessFromAnUncooperativeEngineCannotOverrideCancellation(self):
		cancelled, closed = False, []
		class Engine:
			def start(inner, context, request):
				nonlocal cancelled
				try:
					context.limits.usage["tokens"] = 3
					cancelled = True
					yield {"type": "ScriptEnded", "status": "Succeeded", "output": "Too late"}
				finally:
					closed.append(True)
		context = self.ctx.derive(engines={"fake": Engine()}, config={"engine": "fake"}, cancelled=lambda: cancelled)
		events = list(ScriptStart(context, self.script("Work")))
		self.assertEqual(events[-1]["status"], "Cancelled")
		self.assertEqual(events[-1]["failure"]["kind"], "cancelled")
		self.assertEqual(closed, [True])

	def testCancellationWhileAnsweringDoesNotResumeEngine(self):
		cancelled, resumed, closed = False, [], []
		class Engine:
			def start(inner, context, request):
				try:
					answer = yield {"type": "ScriptQuestion", "content": "Continue?"}
					resumed.append(answer)
					yield {"type": "ScriptEnded", "status": "Succeeded", "output": "Too late"}
				finally:
					closed.append(True)
		def answer(event):
			nonlocal cancelled
			cancelled = True
			return "yes"
		context = self.ctx.derive(engines={"fake": Engine()}, config={"engine": "fake"},
			cancelled=lambda: cancelled, question=answer)
		events = list(ScriptStart(context, self.script("Work")))
		self.assertEqual(events[-1]["status"], "Cancelled")
		self.assertEqual(resumed, [])
		self.assertEqual(closed, [True])

	def testCancellationAtLifecycleActionDoesNotLaunchTheCommand(self):
		class Engine:
			def start(inner, context, request):
				yield {"type": "ScriptEnded", "status": "Succeeded", "output": "Done"}
		for phase in ("pre", "post", "transform", "apply"):
			with self.subTest(phase=phase), patch("auto.integration.process.subprocess.Popen",
				side_effect=AssertionError("Cancelled work must not spawn")) as spawn:
				cancelled = False
				context = self.ctx.derive(engines={"fake": Engine()}, config={"engine": "fake"}, cancelled=lambda: cancelled)
				events = []
				for event in ScriptStart(context, self.script("Work", {phase: "printf changed"})):
					events.append(event)
					if event["type"] == "ScriptAction":
						cancelled = True
				self.assertEqual(events[-1]["status"], "Cancelled")
				spawn.assert_not_called()


class ProcessCancellationTests(unittest.TestCase):
	def testPreCancelledProcessDoesNotSpawn(self):
		with patch("auto.integration.process.subprocess.Popen", side_effect=AssertionError("Cancelled work must not spawn")) as spawn:
			with self.assertRaises(AutoError) as failure:
				capture(Context(cancelled=lambda: True).process, ["should-not-run"], {})
			self.assertEqual(failure.exception.kind, "cancelled")
			spawn.assert_not_called()

	def testExpiredDeadlineDoesNotSpawnOrLeakAnUnownedProcess(self):
		context = Context(limits=Limits({"time": 0}, started=0))
		with patch("auto.integration.process.subprocess.Popen", side_effect=AssertionError("Expired work must not spawn")) as spawn:
			with self.assertRaises(AutoError) as failure:
				capture(context.process, ["should-not-run"], {})
			self.assertEqual(failure.exception.kind, "timeout")
			spawn.assert_not_called()


# EOF
