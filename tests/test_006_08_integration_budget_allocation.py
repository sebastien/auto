"""Allocate resolved task/profile budgets within plan and parent limits."""

import json
import unittest
from contextlib import contextmanager
from unittest.mock import patch

import auto.features.elaboration.operations as operations
from auto.features.elaboration.operations import ElaborationApply, ElaborationStart, ElaborationValidate, proposed
from auto.features.plan.operations import PlanRevise
from auto.features.plan.store import journal
from auto.features.task.operations import TaskGet, TaskStart, effectiveTask
from auto.utils.data import AutoError, documentText
import test_006_04_integration_materialization as fixtures


class BudgetAllocationTests(unittest.TestCase):
	setUp = fixtures.MaterializationTests.setUp
	template = fixtures.MaterializationTests.template
	elaboration = fixtures.MaterializationTests.elaboration

	def planBudget(self, budget, **meta):
		(self.plan / "plan.md").write_text(documentText({"id": "demo", "goal": "Demo", "budget": budget, **meta}, "# Demo"))

	def profile(self, name, budget):
		path = self.root / "profiles" / (name + ".md")
		path.parent.mkdir(exist_ok=True)
		path.write_text(documentText({"budget": budget}, "Profile"))
		return str(path)

	def task(self, id, **meta):
		return {"id": id, "title": id, "sections": {"Process": "Work"}, **meta}

	def completedUsage(self, tokens):
		run = self.plan / "runs/historical"
		run.mkdir(parents=True)
		(run / "receipt.json").write_text(json.dumps({"id": "historical", "task": "removed", "status": "Succeeded",
			"started": "2026-11-14T00:00:00Z", "budget": {"tokens": tokens}, "usage": {"tokens": tokens, "cost": 0, "time": 0}}))

	def testProfileDefaultIsAllocatedBeforeUnspecifiedTaskShares(self):
		self.planBudget({"tokens": 100})
		profile = self.profile("small", {"tokens": 5})
		value = {"task": "root", "tasks": [self.task("small", profile=profile), self.task("other")]}
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		ElaborationApply(self.ctx, self.plan, value, "profile-before-shares")
		self.assertEqual(TaskGet(self.ctx, "small").meta["budget"], {"tokens": 5})
		self.assertEqual(TaskGet(self.ctx, "other").meta["budget"], {"tokens": 95})
		(self.root / "profiles/small.md").unlink()
		self.assertEqual(effectiveTask(self.ctx, TaskGet(self.ctx, "small")).meta["budget"], {"tokens": 5})
		self.assertEqual(TaskStart(self.ctx, "small")["budget"]["tokens"], 5)

	def testSucceededTasksReturnTheirUnspentAllocationToRefinements(self):
		# Seen live: a refinement of a completed plan found its whole budget still reserved.
		self.planBudget({"tokens": 100})
		ElaborationApply(self.ctx, self.plan, {"task": "root", "tasks": [self.task("done")]}, "first")
		self.assertEqual(TaskGet(self.ctx, "done").meta["budget"], {"tokens": 100})
		refinement = {"task": "root", "tasks": [self.task("more", budget={"tokens": 50})]}
		self.assertNotEqual(ElaborationValidate(self.ctx, self.plan, refinement), [])
		self.assertEqual(TaskStart(self.ctx, "done")["status"], "Succeeded")
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, refinement), [])

	def testExplicitAndProfileDimensionsPrecedeDerivedDimensions(self):
		self.planBudget({"tokens": 100, "cost": 2, "time": "10s"})
		profile = self.profile("mixed", {"tokens": 5, "cost": .25, "time": "2s"})
		value = {"task": "root", "tasks": [self.task("selected", profile=profile, budget={"tokens": 0, "cost": .5}), self.task("other")]}
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		ElaborationApply(self.ctx, self.plan, value, "per-dimension-allocation")
		self.assertEqual(TaskGet(self.ctx, "selected").meta["budget"], {"tokens": 0, "cost": .5, "time": 2})
		self.assertEqual(TaskGet(self.ctx, "other").meta["budget"], {"tokens": 100, "cost": 1.5, "time": 8})

	def testNullAndEmptyBudgetsMeanUnspecifiedDimensions(self):
		self.planBudget({"tokens": 100})
		profile = self.profile("small", {"tokens": 5})
		value = {"task": "root", "tasks": [self.task("null", profile=profile, budget=None),
			self.task("empty", profile=profile, budget={}), self.task("plain", budget=None)]}
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		ElaborationApply(self.ctx, self.plan, value, "nullable-budgets")
		self.assertEqual(TaskGet(self.ctx, "null").meta["budget"], {"tokens": 5})
		self.assertEqual(TaskGet(self.ctx, "empty").meta["budget"], {"tokens": 5})
		self.assertEqual(TaskGet(self.ctx, "plain").meta["budget"], {"tokens": 90})

	def testShellResolvedNullBudgetFallsBackToProfile(self):
		self.planBudget({"tokens": 100})
		profile = self.profile("small", {"tokens": 5})
		file = self.template({"title": "Nullable mapping", "profile": profile, "budget": "{{ params.selection | default: nil }}",
			"params": {"selection": "!printf ''"}}, "## Process\nWork\n")
		value = self.elaboration(file)
		with patch("auto.integration.process.capture", return_value=("", "", 0)) as shell:
			self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
			self.assertEqual(shell.call_count, 0)
			ElaborationApply(self.ctx, self.plan, value, "resolved-null-budget")
			self.assertEqual(shell.call_count, 1)
		self.assertEqual(TaskGet(self.ctx, "work").meta["budget"], {"tokens": 5})

	def testRootDefaultProfileDoesNotCreateAPlanWideBudget(self):
		profile = self.profile("small", {"tokens": 5})
		self.planBudget(None, profile=profile)
		value = {"task": "root", "tasks": [self.task("first"), self.task("second")]}
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		ElaborationApply(self.ctx, self.plan, value, "unbounded-plan-profile")
		self.assertEqual(TaskGet(self.ctx, "first").meta["budget"], {"tokens": 5})
		self.assertEqual(TaskGet(self.ctx, "second").meta["budget"], {"tokens": 5})

	def testProfileBudgetExceedingThePlanFailsBeforeParameterEffects(self):
		self.planBudget({"tokens": 100})
		profile = self.profile("large", {"tokens": 101})
		file = self.template({"title": "Too large", "profile": profile, "params": {"note": "!printf note"}}, "## Process\nWork\n")
		value = self.elaboration(file)
		with patch("auto.integration.process.capture") as shell:
			self.assertIn("budgets exceed remaining budget", str(ElaborationValidate(self.ctx, self.plan, value)))
			with self.assertRaisesRegex(AutoError, "budgets exceed remaining budget"):
				ElaborationApply(self.ctx, self.plan, value, "profile-over-plan")
			self.assertEqual(shell.call_count, 0)
		self.assertEqual(journal(self.plan), [])

	def testResolvedTemplateBudgetOverridesProfileAndMustFitKnownSibling(self):
		self.planBudget({"tokens": 20})
		profile = self.profile("small", {"tokens": 5})
		file = self.template({"title": "Explicit template budget", "profile": profile, "budget": {"tokens": "{{ params.tokens | plus: 0 }}"},
			"params": {"tokens": "!printf 12"}}, "## Process\nWork\n")
		for sibling, succeeds in ((9, False), (8, True)):
			with self.subTest(sibling=sibling):
				value = self.elaboration(file)
				value.tasks.append(self.task("sibling", budget={"tokens": sibling}))
				with patch("auto.integration.process.capture", return_value=("12", "", 0)) as shell:
					self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
					if succeeds:
						ElaborationApply(self.ctx, self.plan, value, "explicit-template-fits")
						self.assertEqual(TaskGet(self.ctx, "work").meta["budget"]["tokens"], 12)
					else:
						with self.assertRaisesRegex(AutoError, "budgets exceed remaining budget") as failure:
							ElaborationApply(self.ctx, self.plan, value, "explicit-template-exceeds")
						self.assertEqual(failure.exception.kind, "output")
						self.assertEqual(journal(self.plan), [])
					self.assertEqual(shell.call_count, 1)

	def testRetainedProfileAllocationAndMeasuredUsageAreCountedOnce(self):
		self.planBudget({"tokens": 20})
		retained = self.profile("retained", {"tokens": 5})
		PlanRevise(self.ctx, self.plan, [{"op": "add", "task": self.task("retained", profile=retained)}], "Retain task")
		class Used:
			def start(self, context, request):
				context.limits.usage["tokens"] = 3
				yield {"type": "ScriptEnded", "status": "Succeeded", "output": "Done"}
		self.ctx.engines["fake"] = Used()
		self.assertEqual(TaskStart(self.ctx, "retained")["usage"]["tokens"], 3)
		profile = self.profile("new", {"tokens": 4})
		value = {"task": "root", "tasks": [self.task("selected", profile=profile), self.task("other")]}
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		ElaborationApply(self.ctx, self.plan, value, "retained-profile-capacity")
		self.assertEqual(TaskGet(self.ctx, "selected").meta["budget"]["tokens"], 4)
		# The succeeded retained task counts by its usage (3), not its unspent allocation too
		self.assertEqual(TaskGet(self.ctx, "other").meta["budget"]["tokens"], 13)

	def testParentUsesItsResolvedProfileAndExplicitDimensions(self):
		self.planBudget({"tokens": 100, "cost": 2, "time": "10s"})
		parent = self.profile("parent", {"tokens": 12, "time": "6s"})
		PlanRevise(self.ctx, self.plan, [{"op": "add", "task": self.task("parent", profile=parent, budget={"tokens": 8},
			sections={}, hints={"elaborate": "later"})}], "Parent")
		child = self.profile("child", {"tokens": 5, "cost": .25})
		value = {"task": "parent", "tasks": [self.task("selected", profile=child), self.task("other")]}
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		ElaborationApply(self.ctx, self.plan, value, "parent-profile-limits")
		self.assertEqual(TaskGet(self.ctx, "selected").meta["budget"], {"tokens": 5, "cost": .25, "time": 3})
		self.assertEqual(TaskGet(self.ctx, "other").meta["budget"], {"tokens": 3, "cost": 1.75, "time": 3})

	def testParentProfileCapCannotBeBypassedByAnUnsetBudget(self):
		parent = self.profile("parent", {"tokens": 5})
		PlanRevise(self.ctx, self.plan, [{"op": "add", "task": self.task("parent", profile=parent, budget=None,
			sections={}, hints={"elaborate": "later"})}], "Parent")
		value = {"task": "parent", "tasks": [self.task("child", budget={"tokens": 6})]}
		before = len(journal(self.plan))
		self.assertIn("budgets exceed remaining budget", str(ElaborationValidate(self.ctx, self.plan, value)))
		with self.assertRaisesRegex(AutoError, "budgets exceed remaining budget"):
			ElaborationApply(self.ctx, self.plan, value, "parent-profile-exceeded")
		self.assertEqual(len(journal(self.plan)), before)

	def testElaboratorReceivesResolvedParentBudgetLimits(self):
		self.planBudget({"tokens": 100, "time": "10s"})
		parent = self.profile("parent", {"tokens": 12, "time": "6s"})
		custom = self.root / "elaborate.md"
		custom.write_text(documentText({"title": "Elaborate"}, "## Process\n{{ budget }}\n"))
		PlanRevise(self.ctx, self.plan, [{"op": "add", "task": self.task("parent", profile=parent, budget={"tokens": 8},
			sections={}, hints={"elaborate": "later"})},
			{"op": "update", "fields": {"elaborator": {"task": str(custom)}}}], "Parent and Elaborator")
		run = ElaborationStart(self.ctx, self.plan, "parent")
		self.assertEqual(run["budget"], {"tokens": 8, "time": 6})
		state = json.loads((self.plan / "runs" / run["id"] / "state.json").read_text())
		self.assertEqual(json.loads(state["inputs"]["budget"]), {"tokens": 8, "time": 6})

	def testPreparedValidationRechecksNewlyMeasuredUsage(self):
		self.planBudget({"tokens": 10})
		profile = self.profile("small", {"tokens": 5})
		value = self.elaboration(self.template({"title": "Work", "profile": profile}, "## Process\nWork\n"))
		prepared = proposed(self.ctx, value)
		self.completedUsage(6)
		self.assertIn("budgets exceed remaining budget", str(ElaborationValidate(self.ctx, self.plan, value, prepared=prepared)))
		self.assertFalse((self.plan / "tasks/work.md").exists())

	def testCommitRechecksUsageAfterAcquiringTheWriterLock(self):
		self.planBudget({"tokens": 10})
		profile = self.profile("small", {"tokens": 5})
		value = {"task": "root", "tasks": [self.task("work", profile=profile)]}
		realLocked, acquisitions = operations.locked, []
		@contextmanager
		def locked(path):
			if path == self.plan:
				acquisitions.append(path)
				if len(acquisitions) == 2:
					self.completedUsage(6)
			with realLocked(path):
				yield
		with patch("auto.features.elaboration.operations.locked", side_effect=locked):
			with self.assertRaisesRegex(AutoError, "budgets exceed remaining budget"):
				ElaborationApply(self.ctx, self.plan, value, "commit-budget-race")
		self.assertEqual(len(acquisitions), 2)
		self.assertFalse((self.plan / "tasks/work.md").exists())
		self.assertEqual(journal(self.plan), [])

	def testNullDimensionRemainsAnInvalidNumericBudget(self):
		self.planBudget({"tokens": 100})
		profile = self.profile("small", {"tokens": 5})
		value = {"task": "root", "tasks": [self.task("work", profile=profile, budget={"tokens": None})]}
		self.assertIn("Invalid tokens budget", str(ElaborationValidate(self.ctx, self.plan, value)))
		with self.assertRaisesRegex(AutoError, "Invalid tokens budget"):
			ElaborationApply(self.ctx, self.plan, value, "null-dimension")
