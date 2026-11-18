"""Project actions: listed in the catalogue, and instantiated by plan tasks with `from`."""

from __future__ import annotations

import unittest

from auto.features.elaboration.catalogue import CatalogueGet, CatalogueList
from auto.features.elaboration.materialize import materialize
from auto.features.elaboration.model import createElaborationFromDict
from auto.features.elaboration.operations import ElaborationApply, ElaborationValidate
from auto.features.script.operations import ScriptExpand
from auto.features.task.operations import TaskExpand, TaskGet
from auto.app.operations import render
from auto.utils.data import AutoError
from auto.wiring import validateContext
import test_004_01_integration_planning as fixtures

ACTION = """---
from: task/audit
title: Audit the docs
inputs:
  facet: documentation
---
Keep `docs/` focussed.

## Scope

- docs/

## Criteria

- DOC-01: Only specifications
"""


class ActionTests(unittest.TestCase):
	setUp = fixtures.PlanningTests.setUp

	def actions(self, patterns=("actions/*.md",)):
		self.ctx.defaults = {**self.ctx.defaults, "catalogue": {"actions": list(patterns)}}
		folder = self.root / "actions"
		folder.mkdir(exist_ok=True)
		(folder / "docs-audit.md").write_text(ACTION)
		(folder / "greet.md").write_text("---\ntitle: Greet\ninputs: {who: ~}\nparams: {tone: warm}\n---\n## Method\n\nGreet {{ who }}, {{ params.tone }}.\n")
		(folder / "not.an-action.md").write_text("Ignored: not an identifier")

	def testActionsAreListedWithHowToUseThem(self):
		self.actions()
		entries = {entry["identity"]: entry for entry in CatalogueList(self.ctx, "task")}
		audit = entries["auto:catalogue/task/docs-audit"]
		self.assertEqual(audit["layer"], "actions")
		self.assertEqual(audit["title"], "Audit the docs")
		self.assertEqual(audit["description"], "Keep `docs/` focussed.")
		self.assertEqual(audit["specialises"], "auto:catalogue/task/audit")
		# What the action gives itself is not left to its callers.
		self.assertNotIn("scope", audit["inputs"])
		self.assertNotIn("facet", audit["inputs"])
		self.assertIn("baseline", audit["inputs"])
		self.assertEqual((audit["access"], audit["artifact"]), ("read", "auto:catalogue/artifact/audit"))
		greet = entries["auto:catalogue/task/greet"]
		self.assertEqual((greet["inputs"], greet["required"], greet["params"]), (["who"], ["who"], {"tone": "warm"}))
		self.assertNotIn("auto:catalogue/task/not.an-action", entries)
		resolve = entries["auto:catalogue/task/audit-resolve"]
		self.assertEqual(resolve["required"], ["audit"])
		text = render(CatalogueList(self.ctx, "task"), "markdown")
		self.assertIn("- `auto:catalogue/task/greet` (task, actions): Greet.\n  inputs: `who` (required); params: `tone` (default \"warm\")\n", text)

	def testTheProjectCatalogueTakesPrecedenceOverActions(self):
		self.actions()
		folder = self.root / ".auto/catalogue/task"
		folder.mkdir(parents=True)
		(folder / "greet.md").write_text("---\ntitle: Project greet\n---\n## Process\n\nHi\n")
		self.assertEqual(CatalogueGet(self.ctx, "auto:catalogue/task/greet")["layer"], "project")
		self.assertEqual(CatalogueGet(self.ctx, "auto:catalogue/task/docs-audit")["layer"], "actions")
		self.assertEqual([entry["title"] for entry in CatalogueList(self.ctx, "task") if entry["identity"].endswith("/greet")], ["Project greet"])

	def testActionPatternsAreValidated(self):
		for value in ("action-*.md", ["/abs/*.md"], [1]):
			with self.subTest(value=value), self.assertRaisesRegex(AutoError, "catalogue.actions"):
				validateContext(self.ctx.derive(defaults={"catalogue": {"actions": value}}))

	def testPlanTasksInstantiateActionsAsAutoRunComposesThem(self):
		self.actions()
		task = materialize(self.ctx, {"id": "check-docs", "from": "auto:catalogue/task/docs-audit", "inputs": {"facet": "naming"}}, "root")
		self.assertEqual(set(task.sections), {"Process"})
		self.assertEqual(task.meta["access"], "read")
		self.assertEqual(task.meta["artifact"], "auto:catalogue/artifact/audit")
		self.assertEqual(task.meta["inputs"]["facet"], "naming")
		self.assertEqual(task.meta["inputs"]["scope"], ["docs/"])
		self.assertIn("### Scope", task.sections["Process"])
		text = ScriptExpand(self.ctx, task.script())
		self.assertIn("DOC-01: Only specifications", text)
		self.assertIn("perspective of\n*naming*", text)
		# The template's companion instructions resolve from the template's folder.
		self.assertIn("### Method", text)

	def testScriptActionsWithoutProcessBecomeTheirProcess(self):
		self.actions()
		value = createElaborationFromDict({"task": "root", "tasks": [{"id": "hello", "from": "auto:catalogue/task/greet", "inputs": {"who": "Ada"}}]})
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		ElaborationApply(self.ctx, self.plan, value, {"id": "elaboration-one"})
		task = TaskGet(self.ctx, "hello")
		self.assertIn("### Method", task.sections["Process"])
		self.assertIn("Greet Ada, warm.", TaskExpand(self.ctx, "hello"))

	def testAuditResolveSelectsFindings(self):
		ctx = self.ctx.derive(inputs={"audit": "Report"})
		text = ScriptExpand(ctx, "auto:catalogue/task/audit-resolve")
		self.assertIn("Resolve the findings of severity critical, major, and only these.", text)
		self.assertIn("with `mode: fix`", text)
		text = ScriptExpand(ctx.derive(params={"findings": ["F2"], "mode": "triage"}), "auto:catalogue/task/audit-resolve")
		self.assertIn("Resolve these findings, and only these: F2.", text)
		self.assertIn("without changing the project", text)
		with self.assertRaisesRegex(AutoError, "Missing required input: audit"):
			ScriptExpand(self.ctx, "auto:catalogue/task/audit-resolve")


if __name__ == "__main__":
	unittest.main()
