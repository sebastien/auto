"""Cancellation is checked by the lifecycle, not only by engine adapters."""

from __future__ import annotations

import unittest

from auto.features.task.operations import TaskStart
import test_009_02_integration_continuation as fixtures


class LifecycleCancellationTests(unittest.TestCase):
	setUp = fixtures.ContinuationTests.setUp
	add = fixtures.ContinuationTests.add

	def testAlreadyCancelledRunDoesNotResolveEffectfulInputsOrStartEngine(self):
		marker = self.root / "input-effect"
		self.add("work", inputs={"data": "!printf x > " + str(marker)})
		run = TaskStart(self.context.derive(cancelled=lambda: True), "work")
		self.assertEqual(run["status"], "Cancelled", run)
		self.assertFalse(marker.exists())
		self.assertEqual(self.context.engines["fake"].calls, [])

	def testCancellationAtModelCompletionSkipsPostApplyAndEvaluation(self):
		marker = self.root / "apply-effect"
		self.add("work", post="printf post > " + str(marker), apply="printf apply >> " + str(marker),
			sections={"Process": "Work", "Evaluation": "Evaluate"})
		cancelled, calls = False, []
		class Engine:
			def start(inner, context, request):
				nonlocal cancelled
				calls.append(request)
				context.limits.usage["tokens"] = 3
				cancelled = True
				yield {"type": "ScriptEnded", "status": "Succeeded", "output": "Done"}
		context = self.context.derive(engines={"fake": Engine()}, cancelled=lambda: cancelled)
		run = TaskStart(context, "work")
		self.assertEqual(run["status"], "Cancelled", run)
		self.assertEqual(run["usage"]["tokens"], 3)
		self.assertEqual(len(calls), 1)
		self.assertFalse(marker.exists())


# EOF
