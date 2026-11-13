"""Selected source identities survive capture, discovery and execution."""

import unittest
from unittest.mock import patch

from auto.features.elaboration.operations import ElaborationApply, ElaborationValidate
from auto.features.script.templates import Renderer
from auto.features.task.operations import TaskExpand, TaskGet, effectiveTask
from auto.utils.data import AutoError, digest, documentText
import test_006_04_integration_materialization as fixtures


class SelectorSourceTests(unittest.TestCase):
	setUp = fixtures.MaterializationTests.setUp
	template = fixtures.MaterializationTests.template
	elaboration = fixtures.MaterializationTests.elaboration

	def testCrLfTemplatesAndSelectedCompanionsRetainDefaultsAndChecksums(self):
		file = self.template({"title": "Windows sources", "params": {"label": "Windows"},
			"inputs": {"name": "./name.txt"}, "artifact": "./artifact.md"}, "## Process\n@./part.md#Selected\n")
		file.write_bytes(file.read_bytes().replace(b"\n", b"\r\n"))
		original = file.read_bytes()
		part = file.parent / "part.md"
		part.write_bytes(documentText({"inputs": {"detail": "./detail.txt"}},
			"## Selected\n{{ params.label }} {{ name }} {{ detail }}\n## Unused\n{{ missing }}\n").replace("\n", "\r\n").encode())
		partDigest = digest(part.read_bytes())
		artifact = file.parent / "artifact.md"
		artifact.write_bytes(documentText({"output": {"format": "text"}},
			"## Post\nCheck {{ params.label }}\n").replace("\n", "\r\n").encode())
		artifactText = artifact.read_bytes().decode()
		(file.parent / "name.txt").write_text("Name")
		(file.parent / "detail.txt").write_text("Detail")
		value = self.elaboration(file)
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		ElaborationApply(self.ctx, self.plan, value, "windows-sources")
		task = TaskGet(self.ctx, "work")
		self.assertEqual(task.meta["from"]["digest"], digest(original))
		selected = task.meta["_template_sources"][str(part) + "#Selected"]
		self.assertEqual(selected["digest"], partDigest)
		self.assertEqual(digest(selected["text"].encode()), selected["content_digest"])
		self.assertIn("\r\n", selected["text"])
		self.assertEqual(task.meta["_template_sources"][str(artifact)]["text"], artifactText)
		for path in (file, part, artifact):
			path.unlink()
		self.assertIn("Windows Name Detail", TaskExpand(self.ctx, "work"))
		self.assertEqual(effectiveTask(self.ctx, task).meta["output"]["format"], "text")
		self.assertIn("Check Windows", Renderer(self.ctx, effectiveTask(self.ctx, task).script("Post")).render())

	def testNestedSelectedCompanionsSurviveDeletionAtTheirSemanticDepth(self):
		file = self.template({"title": "Selected sources"}, "## Process\n@./parts.md#First\n")
		(file.parent / "parts.md").write_text(documentText({"params": {"text": "./value.txt"}},
			"## First\n@./parts.md#Second\n## Second\n{{ params.text }}\n"))
		(file.parent / "value.txt").write_text("Captured source")
		context = self.ctx.derive(maxDepth=3)
		self.assertEqual(ElaborationValidate(context, self.plan, self.elaboration(file)), [])
		ElaborationApply(context, self.plan, self.elaboration(file), "nested-selected-sources")
		for path in (file, file.parent / "parts.md", file.parent / "value.txt"):
			path.unlink()
		self.assertIn("Captured source", TaskExpand(context, "work"))

	def testParameterFileValuesCanReadTheirOwnSourceAsData(self):
		file = self.template({"title": "Source data"}, "## Process\n@./part.md\n")
		marker = self.root / "must-not-exist"
		(file.parent / "part.md").write_text(documentText({"params": {"copy": "./part.md"}, "pre": "touch " + str(marker)}, "Data: {{ params.copy }}\n"))
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, self.elaboration(file)), [])
		ElaborationApply(self.ctx, self.plan, self.elaboration(file), "source-is-data")
		text = TaskExpand(self.ctx, "work")
		self.assertIn("{{ params.copy }}", text)
		self.assertFalse(marker.exists())

	def testRepeatedSelectorsThroughAliasesFailBeforeParameterEffects(self):
		file = self.template({"title": "Selector cycle", "params": {"note": "!printf note"}}, "## Process\n@./link.md#First\n")
		(file.parent / "parts.md").write_text("## First\n@./parts.md#Second\n## Second\n@./parts.md#First\n")
		(file.parent / "link.md").symlink_to("parts.md")
		with patch("auto.integration.process.capture") as shell:
			self.assertIn("Recursive", str(ElaborationValidate(self.ctx, self.plan, self.elaboration(file))))
			with self.assertRaisesRegex(AutoError, "Recursive"):
				ElaborationApply(self.ctx, self.plan, self.elaboration(file), "selector-cycle")
			self.assertEqual(shell.call_count, 0)
		self.assertFalse((self.plan / "tasks/work.md").exists())

	def testUnusedLifecycleDefaultsDoNotExpandMissingParameters(self):
		file = self.template({"title": "Ignored commands"}, "## Process\n@./part.md\n")
		(file.parent / "part.md").write_text(documentText({"pre": "{{ params.missing }}", "post": {"unused": "shape"},
			"apply": False, "eval": 42, "transform": "{{ missing }}"}, "Leaf"))
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, self.elaboration(file)), [])
		ElaborationApply(self.ctx, self.plan, self.elaboration(file), "ignored-child-commands")
		self.assertIn("Leaf", TaskExpand(self.ctx, "work"))
