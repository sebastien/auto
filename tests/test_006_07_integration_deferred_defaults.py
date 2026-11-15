"""Deferred mappings preserve override precedence during dry validation."""

import unittest
from unittest.mock import patch

from auto.features.elaboration.materialize import materialize
from auto.features.elaboration.operations import ElaborationApply, ElaborationValidate
from auto.features.plan.store import journal
from auto.features.task.operations import TaskExpand, TaskGet, TaskStart, TaskValidate, effectiveTask
from auto.utils.data import AutoError, digest, documentText
import test_006_04_integration_materialization as fixtures
from support import rootFile


class DeferredDefaultsTests(unittest.TestCase):
	setUp = fixtures.MaterializationTests.setUp
	template = fixtures.MaterializationTests.template
	elaboration = fixtures.MaterializationTests.elaboration

	def profile(self, meta):
		path = self.root / "templates/profile.md"
		path.parent.mkdir(exist_ok=True)
		path.write_text(documentText(meta, "Profile description"))
		return path

	def artifact(self, meta, body=""):
		path = self.root / "templates/artifact.md"
		path.parent.mkdir(exist_ok=True)
		path.write_text(documentText(meta, body))
		return path

	def testWholeDeferredOutputCanOverrideUnusedArtifactDefaults(self):
		checked = self.root / "checked.txt"
		self.artifact({"output": {"format": "unsupported", "template": "./missing.md"}}, "## Post\n!?printf checked >> " + str(checked) + "\n")
		file = self.template({"title": "Deferred output", "artifact": "./artifact.md", "output": "{{ params.selection | default: params.contract }}",
			"params": {"selection": "!printf ''", "contract": {"format": "text", "template": "Chosen outline"}}}, "## Process\nWork\n")
		value = self.elaboration(file)
		with patch("auto.integration.process.capture", return_value=("", "", 0)) as shell:
			self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
			self.assertEqual(shell.call_count, 0)
			ElaborationApply(self.ctx, self.plan, value, "deferred-output-defaults")
			self.assertEqual(shell.call_count, 1)
		self.assertFalse(checked.exists())
		self.assertEqual(effectiveTask(self.ctx, TaskGet(self.ctx, "work")).meta["output"], {"format": "text", "template": "\\Chosen outline"})
		self.assertEqual(TaskStart(self.ctx, "work")["status"], "Succeeded")
		self.assertEqual(checked.read_text(), "checked")

	def testDefaultsActuallyUsedAfterResolutionStillHaveToResolve(self):
		self.artifact({"output": {"format": "text", "template": "./missing.md"}})
		file = self.template({"title": "Needed default", "artifact": "./artifact.md", "output": "{{ params.selection | default: params.contract }}",
			"params": {"selection": "!printf ''", "contract": {"format": "text"}}}, "## Process\nWork\n")
		value = self.elaboration(file)
		with patch("auto.integration.process.capture", return_value=("", "", 0)) as shell:
			self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
			with self.assertRaisesRegex(AutoError, "Cannot resolve"):
				ElaborationApply(self.ctx, self.plan, value, "needed-output-default")
			self.assertEqual(shell.call_count, 1)
		self.assertFalse((self.plan / "tsk/work.md").exists())
		self.assertEqual(journal(self.plan), [])

	def testIndependentChecksRemainGroundedDespiteUnknownOutputMapping(self):
		self.artifact({"output": {"template": "./also-missing.md"}}, "## Post\n@./missing-check.md\n")
		file = self.template({"title": "Known check", "artifact": "./artifact.md", "output": "{{ params.selection | default: params.contract }}",
			"params": {"selection": "!printf ''", "contract": {"format": "text", "template": "Outline"}}}, "## Process\nWork\n")
		value = self.elaboration(file)
		with patch("auto.integration.process.capture") as shell:
			self.assertIn("missing-check.md", str(ElaborationValidate(self.ctx, self.plan, value)))
			with self.assertRaises(AutoError):
				ElaborationApply(self.ctx, self.plan, value, "independent-check")
			self.assertEqual(shell.call_count, 0)

	def testKnownExplicitOutputOverrideCannotHideBehindDeferredMapping(self):
		self.artifact({"output": {"format": "text"}})
		file = self.template({"title": "Known override", "artifact": "./artifact.md", "output": "{{ params.selection | default: params.contract }}",
			"params": {"selection": "!printf ''", "contract": {"format": "text"}}}, "## Process\nWork\n")
		value = self.elaboration(file, output={"format": "unsupported"})
		with patch("auto.integration.process.capture") as shell:
			self.assertIn("Unsupported output format", str(ElaborationValidate(self.ctx, self.plan, value)))
			with self.assertRaises(AutoError):
				ElaborationApply(self.ctx, self.plan, value, "known-output-override")
			self.assertEqual(shell.call_count, 0)

	def testMalformedArtifactContractIsNotDeferredWithTaskOverrides(self):
		self.artifact({"output": "invalid"})
		file = self.template({"title": "Invalid artifact", "artifact": "./artifact.md", "output": "{{ params.selection | default: params.contract }}",
			"params": {"selection": "!printf ''", "contract": {"format": "text"}}}, "## Process\nWork\n")
		value = self.elaboration(file)
		with patch("auto.integration.process.capture") as shell:
			self.assertIn("Artifact output must be a mapping", str(ElaborationValidate(self.ctx, self.plan, value)))
			with self.assertRaisesRegex(AutoError, "Artifact output must be a mapping"):
				ElaborationApply(self.ctx, self.plan, value, "malformed-artifact-contract")
			self.assertEqual(shell.call_count, 0)

	def testWholeDeferredBudgetCanReplaceInvalidProfileDimensions(self):
		self.profile({"budget": {"tokens": "invalid", "cost": 2}, "model": "Profile model"})
		file = self.template({"title": "Deferred budget", "profile": "./profile.md", "budget": "{{ params.selection | default: params.allocation }}",
			"params": {"selection": "!printf ''", "allocation": {"tokens": 12}}}, "## Process\nWork\n")
		value = self.elaboration(file)
		with patch("auto.integration.process.capture", return_value=("", "", 0)) as shell:
			self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
			self.assertEqual(shell.call_count, 0)
			ElaborationApply(self.ctx, self.plan, value, "deferred-budget-defaults")
			self.assertEqual(shell.call_count, 1)
		task = TaskGet(self.ctx, "work")
		self.assertEqual(effectiveTask(self.ctx, task).meta["budget"], {"tokens": 12, "cost": 2})
		self.assertEqual(effectiveTask(self.ctx, task).meta["model"], "Profile model")

	def testKnownSiblingBudgetDimensionsStillFailBeforeEffects(self):
		self.profile({"budget": {"tokens": "invalid", "cost": -1}})
		file = self.template({"title": "Known sibling", "profile": "./profile.md", "budget": {"tokens": "{{ params.tokens | plus: 0 }}"},
			"params": {"tokens": "!printf 12"}}, "## Process\nWork\n")
		value = self.elaboration(file)
		with patch("auto.integration.process.capture") as shell:
			self.assertIn("Invalid cost budget", str(ElaborationValidate(self.ctx, self.plan, value)))
			with self.assertRaises(AutoError):
				ElaborationApply(self.ctx, self.plan, value, "known-budget-sibling")
			self.assertEqual(shell.call_count, 0)

	def testUnreplacedProfileBudgetDefaultsAreCheckedAfterResolution(self):
		self.profile({"budget": {"tokens": 4, "cost": -1}})
		file = self.template({"title": "Used budget default", "profile": "./profile.md", "budget": "{{ params.selection | default: params.allocation }}",
			"params": {"selection": "!printf ''", "allocation": {"tokens": 12}}}, "## Process\nWork\n")
		value = self.elaboration(file)
		with patch("auto.integration.process.capture", return_value=("", "", 0)) as shell:
			self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
			with self.assertRaisesRegex(AutoError, "Invalid cost budget") as failure:
				ElaborationApply(self.ctx, self.plan, value, "used-budget-default")
			self.assertEqual(failure.exception.kind, "output")
			self.assertEqual(shell.call_count, 1)
		self.assertFalse((self.plan / "tsk/work.md").exists())
		self.assertEqual(journal(self.plan), [])

	def testDeferredRuntimeFieldsCanReplaceProfileDefaultsOfADifferentType(self):
		self.profile({"model": {}, "temperature": "invalid"})
		file = self.template({"title": "Deferred runtime", "profile": "./profile.md", "model": "{{ params.selection | default: params.model }}",
			"temperature": "{{ params.selection | default: params.temperature }}",
			"params": {"selection": "!printf ''", "model": "Chosen model", "temperature": .2}}, "## Process\nWork\n")
		value = self.elaboration(file)
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		ElaborationApply(self.ctx, self.plan, value, "runtime-defaults")
		task = effectiveTask(self.ctx, TaskGet(self.ctx, "work"))
		self.assertEqual(task.meta["model"], "Chosen model")
		self.assertEqual(task.meta["temperature"], .2)

	def testIndependentProfileFieldsRemainKnown(self):
		self.profile({"model": {}, "temperature": "invalid"})
		file = self.template({"title": "Independent setting", "profile": "./profile.md", "model": "{{ params.selection | default: params.model }}",
			"params": {"selection": "!printf ''", "model": "Chosen model"}}, "## Process\nWork\n")
		value = self.elaboration(file)
		with patch("auto.integration.process.capture") as shell:
			self.assertIn("temperature must be a finite number", str(ElaborationValidate(self.ctx, self.plan, value)))
			with self.assertRaises(AutoError):
				ElaborationApply(self.ctx, self.plan, value, "independent-profile-setting")
			self.assertEqual(shell.call_count, 0)

	def testDryProjectionKeepsCapturedBytesAndNeverPersistsItsHook(self):
		profile = self.profile({"budget": {"tokens": "invalid"}, "model": "Profile model"})
		original = profile.read_bytes()
		file = self.template({"title": "Projection state", "profile": "./profile.md", "budget": {"tokens": "{{ params.tokens | plus: 0 }}"},
			"params": {"tokens": "!printf 12"}}, "## Process\nWork\n")
		value = self.elaboration(file)
		dry = materialize(self.ctx, value.tasks[0], "root", dry=True)
		self.assertEqual(TaskValidate(self.ctx, dry), [])
		record = dry.meta["_template_sources"][str(profile)]
		self.assertEqual(record["text"].encode(), original)
		self.assertEqual(record["content_digest"], digest(original))
		self.assertNotIn("projectDefaults", dry.asDict())
		ElaborationApply(self.ctx, self.plan, value, "projection-state")
		task = TaskGet(self.ctx, "work")
		self.assertIsNone(task.projectDefaults)
		self.assertNotIn("projectDefaults", task.asDict())

	def testMetadataCannotImpersonateAnInternalDefaultsProjection(self):
		self.profile({"budget": {"tokens": "invalid"}})
		file = self.template({"title": "Untrusted marker", "profile": "./profile.md", "params": {"note": "!printf note"}}, "## Process\nWork\n")
		value = self.elaboration(file, projectDefaults={"budget": {}})
		with patch("auto.integration.process.capture") as shell:
			self.assertIn("Invalid tokens budget", str(ElaborationValidate(self.ctx, self.plan, value)))
			with self.assertRaises(AutoError):
				ElaborationApply(self.ctx, self.plan, value, "untrusted-defaults-marker")
			self.assertEqual(shell.call_count, 0)

	def testExplicitOutputReferencesSkipUnusedTemplateDefaultsAndKeepTheirOrigin(self):
		outline = self.root / "outline.txt"
		outline.write_text("Project outline")
		self.artifact({"output": {"template": "./also-missing.md"}})
		file = self.template({"title": "Reference override", "artifact": "./artifact.md", "output": {"template": "./missing.md"},
			"params": {"note": "!printf note"}}, "## Process\nWork\n")
		value = self.elaboration(file, output={"template": "./outline.txt"})
		with patch("auto.integration.process.capture", return_value=("note", "", 0)) as shell:
			self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
			ElaborationApply(self.ctx, self.plan, value, "project-output-reference")
			self.assertEqual(shell.call_count, 1)
		outline.unlink()
		(self.root / "templates/artifact.md").unlink()
		self.assertIn("Project outline", TaskExpand(self.ctx, "work"))

	def testResolvedWholeBudgetMustFitBesideKnownSiblingAllocations(self):
		rootFile(self.plan).write_text(documentText({"id": "demo", "goal": "Demo", "budget": {"tokens": 20}}, "# Demo"))
		self.profile({"budget": {"tokens": "invalid", "cost": 2}})
		file = self.template({"title": "Whole allocation", "profile": "./profile.md", "budget": "{{ params.selection | default: params.allocation }}",
			"params": {"selection": "!printf ''", "allocation": {"tokens": 12}}}, "## Process\nWork\n")
		for sibling, succeeds in ((9, False), (8, True)):
			with self.subTest(sibling=sibling):
				value = self.elaboration(file)
				value.tasks.append({"id": "sibling", "title": "Sibling", "budget": {"tokens": sibling}, "sections": {"Process": "Other work"}})
				with patch("auto.integration.process.capture", return_value=("", "", 0)) as shell:
					self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
					if succeeds:
						ElaborationApply(self.ctx, self.plan, value, "whole-budget-fits")
						self.assertEqual(effectiveTask(self.ctx, TaskGet(self.ctx, "work")).meta["budget"], {"tokens": 12, "cost": 2})
					else:
						with self.assertRaisesRegex(AutoError, "budgets exceed remaining budget"):
							ElaborationApply(self.ctx, self.plan, value, "whole-budget-exceeds")
						self.assertFalse((self.plan / "tsk/work.md").exists())
						self.assertEqual(journal(self.plan), [])
					self.assertEqual(shell.call_count, 1)
