"""Artifact aliases agree across selectors, owners and retained definitions."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from auto.features.elaboration.operations import ElaborationApply, ElaborationValidate
from auto.features.plan.operations import PlanRevise
from auto.features.task.operations import TaskGet
from auto.runtime.model import Content
import test_006_06_integration_methodology as fixtures


class ArtifactAliasTests(unittest.TestCase):
	setUp = fixtures.MethodologyTests.setUp
	artifact = fixtures.MethodologyTests.artifact
	task = fixtures.MethodologyTests.task
	proposal = fixtures.MethodologyTests.proposal

	def testEquivalentNumericLineSelectorsSatisfyTheSameArtifactRequirement(self):
		file = self.root / "artifact.md"
		file.write_text("Artifact instructions\n")
		value = self.proposal([self.task("work", phase="spec", artifact=str(file) + "#L1-1")],
			[{"id": "spec", "artifacts": [str(file) + "#L01-001"]}])
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		ElaborationApply(self.ctx, self.plan, value, "numeric-selector-alias")
		file.unlink()
		value.tasks = []
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])

	def testNumericSelectorAliasesDoNotRequireTwoDeferredProducers(self):
		file = self.root / "artifact.md"
		file.write_text("Artifact instructions\n")
		selected = str(file) + "#L1-1"
		value = self.proposal([self.task("work", phase="spec", artifact="{{ params.selected }}",
			params={"selected": "!printf selected"})],
			[{"id": "spec", "artifacts": [selected, str(file) + "#L01-01"]}])
		with patch("auto.integration.process.capture", return_value=(selected, "", 0)) as shell:
			self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
			shell.assert_not_called()
			ElaborationApply(self.ctx, self.plan, value, "deduplicate-selector-aliases")
		self.assertEqual(TaskGet(self.ctx, "work").meta["artifact"], selected)

	def testDistinctLineRangesWithEqualBytesAreNotArtifactAliases(self):
		file = self.root / "artifact.md"
		file.write_text("Same instructions\nSame instructions\n")
		value = self.proposal([self.task("work", phase="spec", artifact=str(file) + "#L1-1")],
			[{"id": "spec", "artifacts": [str(file) + "#L2-2"]}])
		self.assertIn("Missing phase artifact", str(ElaborationValidate(self.ctx, self.plan, value)))

	def testOwnerAliasesUseTheCanonicalResourceWithoutDependingOnDigestSyntax(self):
		calls = []
		def resolve(context, ref):
			calls.append(ref)
			return Content("owner:canonical", b"Artifact instructions", "owner-revision-7", self.root / "owned.md")
		context = self.ctx.derive(resolvers={"owner:": resolve})
		value = self.proposal([self.task("work", phase="spec", artifact="owner:alias")],
			[{"id": "spec", "artifacts": ["owner:canonical"]}])
		self.assertEqual(ElaborationValidate(context, self.plan, value), [])
		ElaborationApply(context, self.plan, value, "canonical-owner-alias")
		# Captured owner material remains usable without consulting the owner again.
		value.tasks = []
		unavailable = context.derive(resolvers={"owner:": lambda *args: self.fail("Captured owner must not be called")})
		self.assertEqual(ElaborationValidate(unavailable, self.plan, value), [])
		self.assertTrue(calls)

	def testOwnerNumericSelectorAliasesRemainEquivalentAfterOwnerDisappears(self):
		def resolve(context, ref):
			return Content("owner:canonical", b"Artifact instructions\n", "owner-revision-7", self.root / "owned.md")
		context = self.ctx.derive(resolvers={"owner:": resolve})
		value = self.proposal([self.task("work", phase="spec", artifact="owner:alias#L1-1")],
			[{"id": "spec", "artifacts": ["owner:canonical#L01-001"]}])
		self.assertEqual(ElaborationValidate(context, self.plan, value), [])
		ElaborationApply(context, self.plan, value, "owner-numeric-selector")
		value.tasks = []
		unavailable = context.derive(resolvers={"owner:": lambda *args: self.fail("Captured selector must not call owner")})
		self.assertEqual(ElaborationValidate(unavailable, self.plan, value), [])

	def testArchivedDefinitionAliasesCanSupplyAnArtifactType(self):
		PlanRevise(self.ctx, self.plan, [{"op": "add", "task": {"id": "type", "title": "Type",
			"sections": {"Process": "Artifact instructions"}}}], "Define artifact source")
		PlanRevise(self.ctx, self.plan, [{"op": "remove", "task": "type"}], "Archive artifact source")
		value = self.proposal([self.task("work", phase="spec", artifact="task:type/definition#Process")],
			[{"id": "spec", "artifacts": ["auto:demo/task/type/definition#Process"]}])
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		ElaborationApply(self.ctx, self.plan, value, "archived-artifact-type")
		value.tasks = []
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])


# EOF
