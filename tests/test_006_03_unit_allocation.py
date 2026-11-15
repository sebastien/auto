"""Module: test_allocation — Profile inheritance retains definition origins."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from auto.features.elaboration.materialize import materialize
from auto.features.task.operations import TaskValidate, effectiveTask
from auto.runtime.model import Context
from auto.utils.data import documentText
from support import rootFile


class AllocationTests(unittest.TestCase):
	def setUp(self):
		temp = tempfile.TemporaryDirectory()
		self.addCleanup(temp.cleanup)
		self.root = Path(temp.name)
		self.plan = self.root / ".auto/demo"
		self.plan.mkdir(parents=True)
		profile = self.plan / "profiles/default.md"
		profile.parent.mkdir()
		profile.write_text(documentText({"model": "plan-model", "memory": "minimal", "budget": {"tokens": 25}}, "# Default"))
		rootFile(self.plan).write_text(documentText({"id": "demo", "profile": "./profiles/default.md"}, "# Demo"))
		self.context = Context(project=self.root, plan=self.plan)

	def testPlanProfileIsDefaultWithItsOwnOrigin(self):
		task = materialize(self.context, {"id": "work", "title": "Work", "sections": {"Process": "Work"}}, "root")
		self.assertEqual(task.meta["profile"], str(self.plan / "profiles/default.md"))
		resolved = effectiveTask(self.context, task)
		self.assertEqual(resolved.meta["model"], "plan-model")
		self.assertEqual(resolved.meta["memory"], "minimal")
		self.assertEqual(resolved.meta["budget"], {"tokens": 25})
		(self.plan / "profiles/default.md").unlink()
		self.assertEqual(effectiveTask(self.context, task).meta["model"], "plan-model")

	def testTemplateAndExplicitProfilesOverridePlanDefault(self):
		profile = self.root / "task-profile.md"
		profile.write_text(documentText({"model": "task-model", "memory": "full"}, "# Task profile"))
		template = self.root / "task-template.md"
		template.write_text(documentText({"profile": "./task-profile.md"}, "## Process\nWork"))
		value = {"id": "work", "title": "Work", "from": str(template)}
		task = materialize(self.context, value, "root")
		self.assertEqual(effectiveTask(self.context, task).meta["model"], "task-model")
		task = materialize(self.context, {**value, "profile": str(self.plan / "profiles/default.md"), "model": "explicit", "memory": "standard"}, "root")
		resolved = effectiveTask(self.context, task)
		self.assertEqual(resolved.meta["model"], "explicit")
		self.assertEqual(resolved.meta["memory"], "standard")

	def testInvalidProfileAndArtifactReportValidationViolations(self):
		for key in ("profile", "artifact"):
			with self.subTest(key=key):
				task = materialize(self.context, {"id": "work", "sections": {"Process": "Work"}, key: ["bad"]}, "root")
				self.assertIn("reference string", TaskValidate(self.context, task, graph=False)[0]["message"])
