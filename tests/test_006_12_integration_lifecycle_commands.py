"""Owned lifecycle command shapes survive compilation and artifact composition."""

import unittest
from unittest.mock import patch

from auto.features.elaboration.materialize import materialize
from auto.features.elaboration.operations import ElaborationValidate
from auto.features.script.templates import Renderer
from auto.features.task.model import createTaskFromDict
from auto.features.task.operations import effectiveTask
from auto.utils.data import AutoError, documentText
import test_006_04_integration_materialization as fixtures


class LifecycleCommandTests(unittest.TestCase):
	setUp = fixtures.MaterializationTests.setUp
	template = fixtures.MaterializationTests.template
	elaboration = fixtures.MaterializationTests.elaboration

	def testMalformedTemplateCommandsFailBeforeParameterEffects(self):
		for key in ("pre", "post", "apply", "eval", "transform"):
			for value in ({"true": "ignored"}, False, 0, None, ["true", 1]):
				with self.subTest(key=key, value=value):
					file = self.template({"title": "Invalid commands", key: value,
						"params": {"effect": "!printf effect"}}, "## Process\nWork\n")
					with patch("auto.integration.process.capture") as shell:
						self.assertIn(key + " must be a command", str(ElaborationValidate(self.ctx, self.plan, self.elaboration(file))))
						with self.assertRaisesRegex(AutoError, key + " must be a command"):
							materialize(self.ctx, {"id": "work", "from": str(file)}, "root")
						self.assertEqual(shell.call_count, 0)

	def testMalformedArtifactCommandsCannotBeCompiledIntoValidLists(self):
		file = self.template({"title": "Artifact checks", "artifact": "./artifact.md"}, "## Process\nWork\n")
		for key in ("post", "eval"):
			for value in ({"true": "ignored"}, False, 0, None, ["true", 1]):
				with self.subTest(key=key, value=value):
					(file.parent / "artifact.md").write_text(documentText({key: value}, "Artifact"))
					self.assertIn(key + " must be a command", str(ElaborationValidate(self.ctx, self.plan, self.elaboration(file))))
					with self.assertRaisesRegex(AutoError, key + " must be a command"):
						materialize(self.ctx, {"id": "work", "from": str(file)}, "root")

	def testOrdinaryArtifactCompositionRejectsMalformedCommandsOnEitherSide(self):
		artifact = self.root / "artifact.md"
		for owner in ("artifact", "task"):
			for key in ("post", "eval"):
				for value in ({"true": "ignored"}, False, 0, None, ["true", 1]):
					with self.subTest(owner=owner, key=key, value=value):
						artifact.write_text(documentText({key: value if owner == "artifact" else "true"}, "Artifact"))
						task = createTaskFromDict({"id": "work", "title": "Work", "artifact": str(artifact),
							key: value if owner == "task" else "true", "sections": {"Process": "Work"}}, self.root / "task.md")
						with self.assertRaisesRegex(AutoError, key + " must be a command"):
							effectiveTask(self.ctx, task)

	def testArtifactCommandListsKeepOrderAndIgnoreUnconsumedLifecycleKeys(self):
		file = self.template({"title": "Ordered checks", "artifact": "./artifact.md",
			"params": {"label": "compiled"}, "post": ["task-first", "task-second"], "eval": "task-eval"}, "## Process\nWork\n")
		(file.parent / "artifact.md").write_text(documentText({"post": ["artifact-{{ params.label }}", "artifact-second"],
			"eval": "artifact-eval", "pre": {"ignored": True}, "apply": False, "transform": None}, "Artifact"))
		task = effectiveTask(self.ctx, materialize(self.ctx, {"id": "work", "from": str(file)}, "root"))
		renderer = Renderer(self.ctx, task.script())
		self.assertEqual([renderer.shellArgument(command, renderer.bindings()) for command in task.meta["post"]],
			["artifact-compiled", "artifact-second", "task-first", "task-second"])
		self.assertEqual(task.meta["eval"], ["artifact-eval", "task-eval"])
