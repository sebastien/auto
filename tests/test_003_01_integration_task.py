"""Module: test_task — Task model, dependencies, validation and expansion."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from auto.features.task.model import Task, createTaskFromDict, createTaskFromText, taskText
from auto.features.task.operations import TaskDependencies, TaskExpand, TaskGet, TaskValidate
from auto.runtime.model import Context
from auto.utils.data import documentText


DATA = Path(__file__).resolve().parent / "data" / "task-basic.md"


class TaskTests(unittest.TestCase):
	def setUp(self):
		self.temp = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp.cleanup)
		self.root = Path(self.temp.name)
		self.plan = self.root / ".auto/plans/demo"
		(self.plan / "tasks").mkdir(parents=True)
		(self.plan / "plan.md").write_text(documentText({"id": "demo", "goal": "Demo"}, "# Demo"))
		self.text = DATA.read_text()
		(self.plan / "tasks" / "build.md").write_text(self.text)

	def context(self) -> Context:
		return Context(project=self.root, plan=self.plan)

	def add(self, id: str, **meta) -> None:
		value = {"id": id, "title": id, "sections": {"Process": "Work"}, **meta}
		(self.plan / "tasks" / (id + ".md")).write_text(taskText(createTaskFromDict(value, self.plan / "tasks" / (id + ".md"))))

	def testParseAndRoundTrip(self):
		task = createTaskFromText(self.text, self.plan / "tasks/build.md")
		self.assertEqual(task.id, "build")
		self.assertEqual(task.meta["title"], "Build the artefact")
		self.assertIn("Process", task.sections)
		self.assertIn("Evaluation", task.sections)
		again = createTaskFromText(taskText(task), self.plan / "tasks/build.md")
		self.assertEqual(again.id, task.id)
		self.assertEqual(again.meta, task.meta)
		self.assertEqual(again.sections, task.sections)

	def testDependenciesValidationAndExpansion(self):
		self.add("assemble", inputs={"part": "task:build"})
		context = self.context()
		self.assertEqual(TaskDependencies(context, "assemble"), ["build"])
		self.assertEqual(TaskValidate(context, "assemble"), [])
		self.assertEqual(TaskGet(context, "build").id, "build")
		self.assertIn("Generate sample", TaskExpand(context, "build"))
		self.assertIn("Output contract", TaskExpand(context, "build"))

	def testInvalidTaskReportsViolation(self):
		context = self.context()
		invalid = Task("empty", {"id": "empty"}, {}, self.plan / "tasks/empty.md")
		violations = TaskValidate(context, invalid)
		self.assertEqual(len(violations), 1)
		self.assertIn("Process", violations[0]["message"])

	def testValidationRejectsUnresolvableContracts(self):
		context = self.context()
		base = {"id": "invalid", "title": "Invalid", "sections": {"Process": "Work"}}
		for fields in ({"inputs": {"x": "plan:missing"}}, {"inputs": {"x": "run:missing"}},
			{"params": {"x": "./missing"}}, {"output": {"format": "bogus"}}, {"engine": "missing"}):
			with self.subTest(fields=fields):
				self.assertTrue(TaskValidate(context, {**base, **fields}))
		cycle = self.root / "cycle.md"
		cycle.write_text("@./cycle.md")
		self.assertTrue(TaskValidate(context, {**base, "sections": {"Process": "@" + str(cycle)}}))
		data = self.root / "data.md"
		data.write_text("---\ninputs: [not, script, metadata]\n---\nData")
		self.assertEqual(TaskValidate(context, {**base, "inputs": {"x": str(data)}}), [])

# EOF
