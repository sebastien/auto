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


@unittest.skipUnless(importlib.util.find_spec("memo"), "Install sebastien/memo to exercise the real integration")
class MemoTests(unittest.TestCase):
	def testOptimisticWritesAndRepair(self):
		with tempfile.TemporaryDirectory() as directory:
			root = Path(directory)
			plan = root / ".auto/plans/demo"
			plan.mkdir(parents=True)
			(plan / "plan.md").write_text(documentText({"id": "demo", "goal": "Demo"}, "# Demo"))
			ctx = Context(project=root, plan=plan)
			gap = GapRaise(ctx, plan, {"kind": "decision", "question": "Which?", "needed_by": ["root"]})
			decision = GapDecide(ctx, gap, "first", "Chosen")
			self.assertEqual(GapList(ctx)[0]["decision"], decision["id"])
			PlanRepair(ctx)
			self.assertEqual(len(journal(plan)), 2)
			store = MemoKnowledge(root)
			value = store.get(f"auto:demo/gap/{gap}")
			store.write("gap", value)

	def testStandaloneDecisionAndCliRoundTrip(self):
		with tempfile.TemporaryDirectory() as directory:
			root = Path(directory)
			(root / ".git").mkdir()
			plan = root / ".auto/plans/demo"
			plan.mkdir(parents=True)
			(plan / "plan.md").write_text(documentText({"id": "demo", "goal": "Demo"}, "# Demo"))
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
