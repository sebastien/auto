"""Module: test_memo — Exercise real Memo writes when its package is installed."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from auto.features.plan.gaps import DecisionRecord, GapRaise, GapDecide, GapList, PlanRepair
from auto.features.plan.store import journal
from auto.integration.memo.store import MemoKnowledge
from auto.runtime.model import Context
from auto.utils.data import documentText
from support import rootFile


@unittest.skipUnless(importlib.util.find_spec("memo"), "Install sebastien/memo to exercise the real integration")
class MemoTests(unittest.TestCase):
	def testOptimisticWritesAndRepair(self):
		with tempfile.TemporaryDirectory() as directory:
			root = Path(directory)
			plan = root / ".auto/demo"
			plan.mkdir(parents=True)
			rootFile(plan).write_text(documentText({"id": "demo", "goal": "Demo"}, "# Demo"))
			ctx = Context(project=root, plan=plan)
			gap = GapRaise(ctx, plan, {"kind": "decision", "question": "Which?", "needed_by": ["root"]})
			decision = GapDecide(ctx, gap, "first", "Chosen")
			self.assertEqual(GapList(ctx)[0]["decision"], decision["id"])
			PlanRepair(ctx)
			self.assertEqual(len(journal(plan)), 2)
			store = MemoKnowledge(root)
			value = store.get(f"auto:demo/gap/{gap}")
			store.write("gap", value)
			# Records are Memo documents of the plan's namespace, in its directory.
			self.assertTrue(gap.startswith("gap-") and decision["id"].startswith("dec-"))
			self.assertEqual(sorted(path.relative_to(plan).as_posix() for path in plan.glob("*/*.md")),
				sorted(["tsk/root.md", f"gap/{gap.lower()}.md", f"dec/{decision['id'].lower()}.md"]))
			self.assertFalse((root / ".memo").exists())
			from memo.integrations.filesystem.backend import LocalBackend
			self.assertEqual({entry.id for entry in LocalBackend(str(root / ".auto")).listEntries("demo", "gap")},
				{f"demo/gap:{gap.lower()}"})

	def testRecordTextIsTheBodyAndRoundTrips(self):
		from auto.integration.memo.store import bodyText, textBody
		for value, decision in (({"question": "Which?\n\n- a\n- b"}, False), ({"question": "Which?", "rationale": "Because\n\nreasons"}, False),
			({"rationale": ""}, True), ({"rationale": "## Rationale\n\nquoted"}, True), ({"question": "Q", "rationale": ""}, True)):
			with self.subTest(value=value):
				self.assertEqual(bodyText(textBody(value), decision), value)
		with tempfile.TemporaryDirectory() as directory:
			root = Path(directory)
			plan = root / ".auto/demo"
			rootFile(plan).write_text("Demo\n")
			ctx = Context(project=root, plan=plan)
			gap = GapRaise(ctx, plan, {"kind": "decision", "question": "Which one?\nPick carefully.", "needed_by": ["root"]})
			GapDecide(ctx, gap, "first", "")
			text = next(plan.glob("gap/*.md")).read_text()
			self.assertEqual(text.count("Which one?"), 1)
			self.assertNotIn("question:", text)
			self.assertEqual(GapList(ctx)[0]["question"], "Which one?\nPick carefully.")
			# The plan and id are read from the identity; empty fields are not written.
			decisionText = next(plan.glob("dec/*.md")).read_text()
			for text in (text, decisionText):
				self.assertNotRegex(text, r"(?m)^(plan|id|concerns):")
			self.assertEqual((GapList(ctx)[0]["plan"], GapList(ctx)[0]["id"]), ("auto:demo", gap))
			decision = MemoKnowledge(root).get(f"auto:demo/decision/{GapList(ctx)[0]['decision']}")
			self.assertEqual((decision["answer"], decision["rationale"], decision.get("concerns", [])), ("first", "", []))

	def testStandaloneDecisionAndCliRoundTrip(self):
		with tempfile.TemporaryDirectory() as directory:
			root = Path(directory)
			(root / ".git").mkdir()
			plan = root / ".auto/demo"
			plan.mkdir(parents=True)
			rootFile(plan).write_text(documentText({"id": "demo", "goal": "Demo"}, "# Demo"))
			ctx = Context(project=root, plan=plan)
			first = DecisionRecord(ctx, plan, "Which database?", "sqlite", "Simple deployment", source="chat:1")
			store = MemoKnowledge(root)
			self.assertNotIn("gap", store.get(first["identity"]))
			result = subprocess.run([sys.executable, "-m", "auto.app.cli.main", "decision", "record",
				"Which format?", "{format: markdown}", "Readable", "--plan", str(plan),
				"--concerns", "[documentation]", "--source", "chat:2", "--format", "json", "--no-input"],
				cwd=root, capture_output=True, text=True, check=False)
			self.assertEqual(result.returncode, 0, result.stderr)
			decision = json.loads(result.stdout)
			self.assertNotIn("gap", decision)
			self.assertEqual(decision["answer"], {"format": "markdown"})
			self.assertEqual(decision["concerns"], ["documentation"])
			self.assertEqual(store.get(decision["identity"])["source"], "chat:2")
			PlanRepair(ctx)
			self.assertEqual([row["operations"][0]["op"] for row in journal(plan)], ["record", "record"])

# EOF
