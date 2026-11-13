"""Workspace exclusion follows dotted repository-write effects and live grants."""

import unittest

from auto.features.orchestration.operations import PlanContinue
from auto.features.plan.gaps import GapDecide, GapRaise
from auto.features.plan.events import PlanEvents
from auto.features.plan.operations import PlanRevise
from auto.utils.data import AutoError, documentText
import test_009_02_integration_continuation as fixtures


class WorkspaceEffectTests(unittest.TestCase):
	setUp = fixtures.ContinuationTests.setUp
	add = fixtures.ContinuationTests.add

	def otherPlan(self):
		path = self.plan.with_name("other")
		path.mkdir()
		(path / "plan.md").write_text(documentText({"id": "other", "goal": "Other", "effects": ["repo.write"]}, "# Other"))
		return path

	def grant(self, effect):
		gap = GapRaise(self.context, self.plan, {"kind": "decision", "question": "Allow writing?",
			"effects": [effect], "needed_by": ["work"]})
		GapDecide(self.context, gap, {"grant": effect, "scope": "auto:demo/task/work"}, "Human request")

	def testDeclaredWriteSubeffectsExcludeAnotherWritingContinuation(self):
		self.add("work", effects=["repo.write.files"])
		other = self.otherPlan()
		first, second = PlanContinue(self.context, self.plan), PlanContinue(self.context, other)
		try:
			self.assertEqual(next(first)["kind"], "ContinuationStarted")
			with self.assertRaisesRegex(AutoError, "working tree"):
				next(second)
		finally:
			first.close()
			second.close()
		self.assertEqual(list(PlanEvents(self.context, other)), [])

	def testGrantedWriteSubeffectsExcludeAnotherWritingContinuation(self):
		self.add("work")
		self.grant("repo.write.files")
		other = self.otherPlan()
		first, second = PlanContinue(self.context, self.plan), PlanContinue(self.context, other)
		try:
			self.assertEqual(next(first)["kind"], "ContinuationStarted")
			with self.assertRaisesRegex(AutoError, "working tree"):
				next(second)
		finally:
			first.close()
			second.close()
		self.assertEqual(list(PlanEvents(self.context, other)), [])

	def testSimilarlyNamedEffectsDoNotClaimTheWriteWorkspace(self):
		self.add("work", effects=["repo.writer", "repo.write_extra"])
		self.grant("repo.writer")
		other = self.otherPlan()
		first, second = PlanContinue(self.context, self.plan), PlanContinue(self.context, other)
		try:
			self.assertEqual(next(first)["kind"], "ContinuationStarted")
			self.assertEqual(next(second)["kind"], "ContinuationStarted")
		finally:
			first.close()
			second.close()

	def testAWriteGrantAddedAfterStartIsCheckedBeforeExecution(self):
		self.add("work")
		other = self.otherPlan()
		first, second = PlanContinue(self.context, self.plan), PlanContinue(self.context, other)
		try:
			self.assertEqual(next(first)["kind"], "ContinuationStarted")
			self.assertEqual(next(second)["kind"], "ContinuationStarted")
			self.grant("repo.write.files")
			events = list(first)
			self.assertEqual(events[-1]["kind"], "ContinuationStopped")
			self.assertEqual(events[-1]["data"]["stop"], "failed")
			self.assertIn("working tree", events[-1]["data"]["error"])
			self.assertEqual(self.context.engines["fake"].calls, [])
		finally:
			first.close()
			second.close()

	def testGrantsForRemovedTasksDoNotClaimTheWriteWorkspace(self):
		self.add("work")
		self.grant("repo.write")
		PlanRevise(self.context, self.plan, [{"op": "remove", "task": "work"}], "Retire granted work")
		other = self.otherPlan()
		first, second = PlanContinue(self.context, self.plan), PlanContinue(self.context, other)
		try:
			self.assertEqual(next(first)["kind"], "ContinuationStarted")
			self.assertEqual(next(second)["kind"], "ContinuationStarted")
		finally:
			first.close()
			second.close()
