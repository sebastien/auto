"""Archived definition selectors agree across discovery, rendering and capture."""

from __future__ import annotations

import unittest

from auto.features.elaboration.model import createElaborationFromDict
from auto.features.elaboration.operations import ElaborationApply, ElaborationValidate
from auto.features.plan.operations import PlanRevise
from auto.features.script.operations import ScriptExpand, ScriptInputs
from auto.features.task.operations import TaskDependencies, TaskGet, TaskStart
from auto.runtime.operations import Resolve
from auto.utils.data import AutoError, documentText
import test_004_01_integration_planning as fixtures


class HistoricalSelectorTests(unittest.TestCase):
	setUp = fixtures.PlanningTests.setUp
	add = fixtures.PlanningTests.add

	def archive(self, task):
		PlanRevise(self.ctx, self.plan, [{"op": "remove", "task": task}], "Archive definition source")

	def testArchivedNestedSectionChainKeepsAliasesAndRelativeInputOrigins(self):
		(self.plan / "tasks").mkdir()
		(self.plan / "tasks/data.txt").write_text("Archived data")
		self.add("library", inputs={"data": "./data.txt"}, sections={
			"Process": "@auto:demo/task/library/definition#Post",
			"Post": "@task:library/definition#Details",
			"Evaluation": "### Details\n{{ data }} {{ inputs.extra }}\n"})
		self.archive("library")
		for ref in ("task:library/definition#Process", "auto:demo/task/library/definition#Process"):
			with self.subTest(ref=ref):
				self.assertEqual(ScriptInputs(self.ctx, ref)["inputs"], ["data", "extra"])
				self.assertIn("Archived data Current", ScriptExpand(self.ctx.derive(inputs={"extra": "Current"}), ref))
		self.add("reader", process="@task:library/definition#Process")
		self.assertEqual(TaskDependencies(self.ctx, "reader"), [])
		self.assertEqual(TaskStart(self.ctx, "reader", {"extra": "Current"})["status"], "Succeeded")
		self.assertIn("Archived data Current", self.engine.calls[-1])

	def testArchivedLineSelectorRetainsExactBytesAndImplicitDependencies(self):
		self.add("data")
		self.add("library", sections={"Process": "Preamble\n{{ inputs.extra }}\n@task:data"})
		file = self.plan / "tasks/library.md"
		raw = file.read_text()
		line = next(index for index, text in enumerate(raw.splitlines(), 1) if text == "@task:data")
		self.archive("library")
		for prefix in ("task:library/definition", "auto:demo/task/library/definition"):
			ref = prefix + f"#L{line-1}-{line}"
			content = Resolve(self.ctx, ref)
			self.assertEqual(content.data, "".join(raw.splitlines(keepends=True)[line-2:line]).encode())
			self.assertEqual(content.path, file)
			self.assertEqual(ScriptInputs(self.ctx, ref)["inputs"], ["extra"])
		self.add("reader", process=f"@task:library/definition#L{line-1}-{line}")
		self.assertEqual(TaskDependencies(self.ctx, "reader"), ["data"])

	def testArchivedAliasCycleFailsWithoutCreatingReaderOrExecutingChecks(self):
		self.add("library")
		self.archive("library")
		# An older/imported archive can contain a cycle rejected by current writes.
		(self.plan / ".history/library.md").write_text(documentText({"id": "library"},
			"## Process\nInstructions\n## Post\n@auto:demo/task/library/definition#Post\n"))
		ref = "task:library/definition#Post"
		for operation in (ScriptInputs, ScriptExpand):
			with self.subTest(operation=operation.__name__), self.assertRaisesRegex(AutoError, "Recursive"):
				operation(self.ctx, ref)
		with self.assertRaisesRegex(AutoError, "Recursive"):
			self.add("reader", process="@" + ref)
		self.assertFalse((self.plan / "tasks/reader.md").exists())
		self.assertEqual(self.engine.calls, [])

	def testMissingAndAmbiguousArchivedSelectorsFailInBothDiscoveryAndRendering(self):
		self.add("library", sections={"Process": "### Same\nFirst\n### Same\nSecond"})
		self.archive("library")
		for selector in ("Missing", "Same", "L0-1", "L999-1000"):
			for operation in (ScriptInputs, ScriptExpand):
				with self.subTest(selector=selector, operation=operation.__name__), self.assertRaises(AutoError):
					operation(self.ctx, "auto:demo/task/library/definition#" + selector)

	def testArchivedCompiledSourceSelectorsRemainDefinitionAssets(self):
		template = self.root / "template.md"
		template.write_text(documentText({"title": "Captured"}, "## Process\n@./part.md\n"))
		(self.root / "part.md").write_text("### Details\nSelected {{ inputs.extra }}\n### Other\nUnused\n")
		value = createElaborationFromDict({"task": "root", "tasks": [{"id": "library", "from": str(template)}]})
		ElaborationApply(self.ctx, self.plan, value, "capture-selected-source")
		task = TaskGet(self.ctx, "library")
		ref = next(ref for ref in task.meta["_template_sources"] if ref.startswith("auto:demo/task/library/source/"))
		self.archive("library")
		template.unlink()
		(self.root / "part.md").unlink()
		for prefix in (ref, ref.replace("auto:demo/task/", "task:")):
			with self.subTest(prefix=prefix):
				selected = prefix + "#Details"
				self.assertEqual(ScriptInputs(self.ctx, selected)["inputs"], ["extra"])
				self.assertIn("Selected Current", ScriptExpand(self.ctx.derive(inputs={"extra": "Current"}), selected))
		self.add("reader", process="@" + ref + "#Details")
		self.assertEqual(TaskDependencies(self.ctx, "reader"), [])
		self.assertEqual(TaskStart(self.ctx, "reader", {"extra": "Current"})["status"], "Succeeded")

	def testMaterializedTemplateCanCaptureAnArchivedDefinitionWithNestedSelectors(self):
		self.add("library", sections={"Process": "@task:library/definition#Details", "Post": "### Details\nArchived {{ inputs.extra }}"})
		self.archive("library")
		template = self.root / "template.md"
		template.write_text(documentText({"title": "Archive consumer"}, "## Process\n@auto:demo/task/library/definition#Process\n"))
		value = createElaborationFromDict({"task": "root", "tasks": [{"id": "reader", "from": str(template)}]})
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		ElaborationApply(self.ctx, self.plan, value, "capture-archived-selector")
		template.unlink()
		self.assertEqual(TaskDependencies(self.ctx, "reader"), [])
		self.assertEqual(TaskStart(self.ctx, "reader", {"extra": "Current"})["status"], "Succeeded")
		self.assertIn("Archived Current", self.engine.calls[-1])


# EOF
