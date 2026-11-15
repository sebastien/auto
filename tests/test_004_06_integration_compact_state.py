"""Module: test_004_06 — Plans store and send each fact once, in compact forms."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auto.features.elaboration.operations import ElaborationStart, PlanCreate
from auto.features.plan.events import eventRows
from auto.features.plan.operations import PlanRevise
from auto.runtime.model import Context
from auto.runtime.operations import isReference
from auto.utils.data import document, promptText, readJson, yamlLoad
from auto.utils.layout import planFile, runsPath
from test_004_01_integration_planning import Fake, MemoryKnowledge

GOAL = "Build a small site\n\n- Pages: `index.html` and `contact.html`.\n- No JavaScript."


class CompactStateTests(unittest.TestCase):
	def setUp(self):
		temp = tempfile.TemporaryDirectory()
		self.addCleanup(temp.cleanup)
		self.root = Path(temp.name)
		self.engine = Fake(["not an elaboration"] * 8)
		self.ctx = Context(project=self.root, engines={"fake": self.engine}, config={"engine": "fake"}, knowledge=MemoryKnowledge())
		with patch("auto.features.elaboration.operations.PlanRefine", side_effect=lambda context, plan, reason: plan):
			self.plan = PlanCreate(self.ctx, GOAL, budget={"cost": 3}, effects=["repo.write"])
		self.ctx = self.ctx.derive(plan=self.plan)

	def inputs(self, run):
		return readJson(runsPath(self.plan) / run["id"] / "state.json")["inputs"]

	def testRootTaskStoresTheGoalOnceWithoutEmptyFields(self):
		self.assertTrue(self.plan.name.startswith("pln-000-"))
		meta, body = document(planFile(self.plan).read_text())
		self.assertEqual(meta, {"budget": {"cost": 3}, "requested_effects": ["repo.write"]})
		self.assertEqual(body, GOAL + "\n")
		PlanRevise(self.ctx, self.plan, [{"op": "agents", "agents": []}], "No agents")
		self.assertEqual(planFile(self.plan).read_text().count("Build a small site"), 1)
		self.assertNotIn("goal:", planFile(self.plan).read_text())

	def testFirstElaborationReceivesTheGoalOnceAndNoState(self):
		run = ElaborationStart(self.ctx, self.plan, "root")
		inputs = self.inputs(run)
		self.assertEqual(inputs["goal"], GOAL + "\n")
		self.assertEqual(inputs["state"], "")
		self.assertEqual(self.engine.calls[0].count("Build a small site"), 1)
		self.assertEqual(yamlLoad(inputs["policy"]), {"default": "confirm", "grants": {"plan": ["repo.write"]},
			"rules": {"repo.read": "allow", "shell.run": "allow"}})

	def testRefinementStateOmitsTheTargetGoalPathsAndDerivableFields(self):
		PlanRevise(self.ctx, self.plan, [{"op": "add", "task": {"id": "pages", "title": "Pages", "sections": {"Process": "Write pages"}}},
			{"op": "add", "task": {"id": "check", "title": "Check", "inputs": {"site": "task:pages"}, "sections": {"Process": "Check"}}}], "Tasks")
		run = ElaborationStart(self.ctx, self.plan, "root", "Add a page")
		inputs = self.inputs(run)
		self.assertEqual(self.engine.calls[0].count("Build a small site"), 1)
		state = yamlLoad(inputs["state"])
		self.assertEqual(state["reason"], "Add a page")
		self.assertEqual(state["tasks"]["root"], {"budget": {"cost": 3}, "state": "Blocked"})
		self.assertEqual(state["tasks"]["tsk-check"]["dependencies"], ["tsk-pages"])
		self.assertEqual(state["tasks"]["tsk-pages"], {"sections": {"Process": "Write pages"}, "state": "Ready", "title": "Pages"})
		self.assertNotIn(str(self.plan), inputs["state"])

	def testMaterializedTemplatesDropCatalogueMetadataAndRoundBudgets(self):
		from auto.features.elaboration.operations import ElaborationApply
		template = self.root / "check.md"
		template.write_text("---\nkind: task\nidentity: auto:catalogue/task/check\ntitle: Check\nparams: {}\n---\n## Process\n\nCheck\n")
		ElaborationApply(self.ctx, self.plan, {"task": "root", "tasks": [{"id": "check", "from": str(template)},
			{"id": "other", "title": "Other", "sections": {"Process": "Work"}}]}, "run-test")
		meta, _ = document((self.plan / "tsk/check.md").read_text())
		self.assertEqual(sorted(meta), ["budget", "from", "id", "parent", "title"])
		self.assertEqual(meta["budget"], {"cost": 1.5})
		# The Elaborator reads a templated task as its template, not its text.
		state = yamlLoad(self.inputs(ElaborationStart(self.ctx, self.plan, "root", "Refine"))["state"])
		self.assertEqual(state["tasks"]["tsk-check"], {"budget": {"cost": 1.5}, "from": str(template), "parent": "root",
			"state": "Ready", "title": "Check"})
		self.assertIn("sections", state["tasks"]["tsk-other"])

	def testTaskIdsArePrefixedAndBareNamesStillResolve(self):
		from auto.features.elaboration.operations import ElaborationApply
		from auto.features.task.operations import TaskDependencies, TaskGet
		from auto.runtime.operations import Resolve
		text = "---\ntask: root\n---\n## task: pages\n```yaml\ntitle: Pages\n```\n### Process\nWrite\n" \
			"## task: tsk-check\n```yaml\ntitle: Check\ninputs: {site: 'task:pages'}\n```\n### Process\nCheck {{ site }}\n"
		ElaborationApply(self.ctx, self.plan, text, "run-test")
		self.assertEqual(sorted(path.name for path in (self.plan / "tsk").glob("*.md")), ["check.md", "pages.md", "root.md"])
		self.assertEqual(document((self.plan / "tsk/pages.md").read_text())[0]["id"], "tsk-pages")
		self.assertEqual(TaskGet(self.ctx, "pages").id, TaskGet(self.ctx, "tsk-pages").id)
		self.assertEqual(TaskDependencies(self.ctx, "check"), ["tsk-pages"])
		self.assertEqual(Resolve(self.ctx, "task:pages/definition").ref, f"auto:{self.plan.name}/task/tsk-pages/definition")

	def testPromptTextKeepsMultilineValuesReadableAndLiteral(self):
		text = promptText({"goal": "First\nSecond: x", "kind": "plain"})
		self.assertEqual(text, "goal: |-\n  First\n  Second: x\nkind: plain\n")
		self.assertFalse(isReference(text))
		self.assertFalse(isReference("cost: 3\n"))
		self.assertTrue(isReference("task:pages"))

	def testRevisionEventsSummariseTheirOperations(self):
		PlanRevise(self.ctx, self.plan, [{"op": "add", "task": {"id": "pages", "title": "Pages", "sections": {"Process": "Write"}}}], "Tasks")
		revised = [row for row in eventRows(self.plan) if row["kind"] == "PlanRevised"]
		self.assertEqual([row["data"]["ops"] for row in revised], [["grant"], ["add"]])
		self.assertTrue(all(row["data"]["revision"].startswith("rev-") for row in revised))
		self.assertNotIn("operations", json.dumps(revised))

# EOF
