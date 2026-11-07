"""Module: test_memo — Exercise real Memo writes when its package is installed."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from auto.features.plan.gaps import GapRaise, GapDecide, GapList, PlanRepair
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

# EOF
