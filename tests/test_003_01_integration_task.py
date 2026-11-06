"""Module: test_task — Task model, dependencies, validation and expansion."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from auto.features.task.model import Task, parseTask, taskText
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
		(self.plan / "tasks" / (id + ".md")).write_text(taskText(Task.fromDict(value, self.plan / "tasks" / (id + ".md"))))

	def testParseAndRoundTrip(self):
		task = parseTask(self.text, self.plan / "tasks/build.md")
		self.assertEqual(task.id, "build")
		self.assertEqual(task.meta["title"], "Build the artefact")
		self.assertIn("Process", task.sections)
		self.assertIn("Evaluation", task.sections)
		again = parseTask(taskText(task), self.plan / "tasks/build.md")
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

# EOF
