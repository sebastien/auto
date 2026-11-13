"""Definition data stays inert; definition transclusions compose as scripts."""

import unittest
from unittest.mock import patch

from auto.features.elaboration.model import createElaborationFromDict
from auto.features.elaboration.operations import ElaborationApply, ElaborationValidate
from auto.features.elaboration.sources import TemplateSources
from auto.features.plan.operations import PlanRevise, dependencyGraph, tasksAt
from auto.features.script.operations import ScriptInputs
from auto.features.task.operations import TaskDependencies, TaskGet, TaskStart
from auto.runtime.model import Content
from auto.runtime.operations import Shell
from auto.utils.data import AutoError, digest, documentText
import test_004_01_integration_planning as fixtures


class DefinitionSourceTests(unittest.TestCase):
	setUp = fixtures.PlanningTests.setUp
	add = fixtures.PlanningTests.add

	def testDefinitionTransclusionDiscoversInputsAndOutputDependencies(self):
		self.engine.outputs = iter(["Produced", "Reviewed"])
		self.add("data")
		self.add("library", process="{{ payload }} {{ inputs.extra }}", inputs={"payload": "task:data"})
		self.add("reader", process="@task:library/definition#Process")
		self.assertEqual(TaskDependencies(self.ctx, "reader"), ["data"])
		self.assertEqual(ScriptInputs(self.ctx, TaskGet(self.ctx, "reader").script())["inputs"], ["extra", "payload"])
		self.assertEqual(TaskStart(self.ctx, "reader")["status"], "Blocked")
		TaskStart(self.ctx, "data")
		self.assertEqual(TaskStart(self.ctx, "reader", {"extra": "Current"})["status"], "Succeeded")
		self.assertIn("Produced Current", self.engine.calls[-1])

	def testDefinitionsStagedTogetherContributeTheirNestedDependencies(self):
		PlanRevise(self.ctx, self.plan, [
			{"op": "add", "task": {"id": "data", "title": "Data", "sections": {"Process": "Produce"}}},
			{"op": "add", "task": {"id": "library", "title": "Library", "inputs": {"payload": "task:data"}, "sections": {"Process": "{{ payload }}"}}},
			{"op": "add", "task": {"id": "reader", "title": "Reader", "sections": {"Process": "@auto:demo/task/library/definition#Process"}}}], "Stage definition sources")
		self.assertEqual(TaskDependencies(self.ctx, "reader"), ["data"])

	def testRecursiveStagedDefinitionTransclusionsFailBeforeWrites(self):
		with self.assertRaisesRegex(AutoError, "Recursive"):
			PlanRevise(self.ctx, self.plan, [
				{"op": "add", "task": {"id": "first", "title": "First", "sections": {"Process": "@task:second/definition#Process"}}},
				{"op": "add", "task": {"id": "second", "title": "Second", "sections": {"Process": "@task:first/definition#Process"}}}], "Recursive sources")
		self.assertFalse((self.plan / "tasks/first.md").exists())

	def testArchivedDefinitionTransclusionsContributeNestedDependencies(self):
		self.add("data")
		self.add("library", process="{{ payload }}", inputs={"payload": "task:data"})
		PlanRevise(self.ctx, self.plan, [{"op": "remove", "task": "library"}], "Archive source")
		self.add("reader", process="@task:library/definition#Process")
		self.assertEqual(TaskDependencies(self.ctx, "reader"), ["data"])

	def testInlineDefinitionsRemainVerbatimData(self):
		self.add("data")
		self.add("library", process="{{ payload }}", inputs={"payload": "task:data"})
		self.add("reader", process="Read {{@task:library/definition#Process}}")
		self.assertEqual(TaskDependencies(self.ctx, "reader"), [])
		self.assertEqual(TaskStart(self.ctx, "reader")["status"], "Succeeded")
		self.assertIn("{{ payload }}", self.engine.calls[-1])

	def testCallerOverridesSuppressUnusedDefinitionInputDependencies(self):
		self.add("data")
		self.add("library", process="{{ payload }}", inputs={"payload": "task:data"})
		self.add("reader", process="@task:library/definition#Process", inputs={"payload": "Override"})
		self.assertEqual(TaskDependencies(self.ctx, "reader"), [])

	def testInvalidSelectorsOnStagedDefinitionsFailBeforeWrites(self):
		with self.assertRaisesRegex(AutoError, "Missing"):
			PlanRevise(self.ctx, self.plan, [
				{"op": "add", "task": {"id": "library", "title": "Library", "sections": {"Process": "Instructions"}}},
				{"op": "add", "task": {"id": "reader", "title": "Reader", "sections": {"Process": "@task:library/definition#Missing"}}}], "Invalid source selector")
		self.assertFalse((self.plan / "tasks/reader.md").exists())

	def testUnchangedDefinitionLineSelectorsUseExactStoredBytes(self):
		self.add("data")
		raw = "---\nid: library\ntitle: Library\n---\n# Source\n\n## Process\n\n@task:data\n"
		(self.plan / "tasks/library.md").write_text(raw)
		line = next(index for index, text in enumerate(raw.splitlines(), 1) if text == "@task:data")
		with self.assertRaisesRegex(AutoError, "Dependency cycle"):
			PlanRevise(self.ctx, self.plan, [
				{"op": "edit", "task": {"id": "data", "title": "Data", "inputs": {"reader": "task:reader"}, "sections": {"Process": "Data"}}},
				{"op": "add", "task": {"id": "reader", "title": "Reader", "sections": {"Process": f"@task:library/definition#L{line}-{line}"}}}], "Exact line selector")
		self.assertFalse((self.plan / "tasks/reader.md").exists())

	def testShorthandCompanionSourceAliasesDiscoverTheSameInputs(self):
		file = self.root / "template.md"
		file.write_text(documentText({"title": "Owner"}, "## Process\n@./child.md\n"))
		(self.root / "child.md").write_text("{{ inputs.extra }}")
		ElaborationApply(self.ctx, self.plan, createElaborationFromDict({"task": "root", "tasks": [{"id": "owner", "from": str(file)}]}), "capture-source-alias")
		task = TaskGet(self.ctx, "owner")
		ref = next(ref for ref in task.meta["_template_sources"] if ref.startswith("auto:demo/task/owner/source/"))
		self.add("reader", process="@" + ref.replace("auto:demo/task/", "task:"))
		self.assertEqual(ScriptInputs(self.ctx, TaskGet(self.ctx, "reader").script())["inputs"], ["extra"])

	def testDefinitionDiscoveryReportsShellBackedOwnersWithoutExecutingThem(self):
		calls = []
		def resolve(context, ref):
			calls.append(ref)
			data = Shell(context, "printf definition")[0].encode()
			return Content(ref, data, digest(data), self.root / "owned.md")
		context = self.ctx.derive(resolvers={"task:": resolve})
		with patch("auto.integration.process.capture") as shell:
			result = ScriptInputs(context, {"content": "@task:owner/definition", "meta": {}, "path": str(self.root / "script.md")})
			self.assertEqual(calls, ["task:owner/definition"])
			self.assertEqual(result["unresolved"], ["task:owner/definition"])
			self.assertEqual(shell.call_count, 0)

	def testElaborationValidatesDefinitionsCreatedInTheSameProposal(self):
		value = createElaborationFromDict({"task": "root", "tasks": [
			{"id": "data", "title": "Data", "sections": {"Process": "Produce"}},
			{"id": "library", "title": "Library", "inputs": {"payload": "task:data"}, "sections": {"Process": "{{ payload }}"}},
			{"id": "reader", "title": "Reader", "sections": {"Process": "@task:library/definition#Process"}}]})
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		ElaborationApply(self.ctx, self.plan, value, "proposal-definition-source")
		self.assertEqual(TaskDependencies(self.ctx, "reader"), ["data"])

	def testNestedDefinitionOutputCyclesAreRejectedBeforeWrites(self):
		with self.assertRaisesRegex(AutoError, "Dependency cycle"):
			PlanRevise(self.ctx, self.plan, [
				{"op": "add", "task": {"id": "library", "title": "Library", "inputs": {"payload": "task:reader"}, "sections": {"Process": "{{ payload }}"}}},
				{"op": "add", "task": {"id": "reader", "title": "Reader", "sections": {"Process": "@task:library/definition#Process"}}}], "Nested output cycle")
		self.assertFalse((self.plan / "tasks/reader.md").exists())

	def testUnchangedDefinitionsRespectTheirExplicitOwner(self):
		self.add("other")
		self.add("library", process="Stored")
		self.add("reader", process="@task:library/definition#Process")
		text = documentText({"inputs": {"actual": "task:other"}}, "## Process\n{{ actual }}\n")
		context = self.ctx.derive(resolvers={"task:library/definition": lambda context, ref:
			Content(ref, text.encode(), digest(text.encode()), self.root / "owned.md")})
		self.assertEqual(dependencyGraph(context, tasksAt(self.plan))["reader"], {"other"})

	def testCapturedDefinitionsTakePrecedenceOverStagedUpdates(self):
		self.add("old")
		self.add("new")
		self.add("library", process="{{ payload }}", inputs={"payload": "task:old"})
		sources = TemplateSources(self.ctx, task="reader")
		sources.capture("task:library/definition", self.plan / "tasks", {}, framework=True)
		PlanRevise(self.ctx, self.plan, [
			{"op": "edit", "task": {"id": "library", "title": "Library", "inputs": {"payload": "task:new"}, "sections": {"Process": "{{ payload }}"}}},
			{"op": "add", "task": {"id": "reader", "title": "Reader", "_template_sources": sources.records,
				"sections": {"Process": "@auto:demo/task/library/definition"}}}], "Keep captured definition")
		self.assertEqual(TaskDependencies(self.ctx, "reader"), ["old"])
		self.assertEqual(dependencyGraph(self.ctx, tasksAt(self.plan))["reader"], {"old"})

	def testDifferentSectionsOfOneDefinitionCanFormAFiniteChain(self):
		self.add("library", sections={"Process": "@task:library/definition#Post", "Post": "Nested {{ inputs.extra }}"})
		self.add("reader", process="@auto:demo/task/library/definition#Process")
		self.assertEqual(ScriptInputs(self.ctx, TaskGet(self.ctx, "reader").script())["inputs"], ["extra"])
		self.assertEqual(TaskStart(self.ctx, "reader", {"extra": "Current"})["status"], "Succeeded")
		self.assertIn("Nested Current", self.engine.calls[-1])
