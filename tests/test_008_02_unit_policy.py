"""Module: test_policy — Layered policy resolution and scoped decision grants."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auto.features.plan.policy import PolicyGrant, PolicyResolve, ruleFor
from auto.features.plan.store import commit, locked
from auto.runtime.model import Context
from auto.utils.data import AutoError


class Store:
	"""Minimal knowledge stub returning one human decision."""

	def get(self, ref):
		return {"id": "decision-1", "revision": "revision-1", "identity": ref, "plan": "auto:example",
			"author": "human", "answer": {"grant": "deploy.production", "scope": "auto:example/task/ship"}}


class PolicyTest(unittest.TestCase):
	def makePlan(self, root: Path) -> tuple[Path, Context]:
		path = root / ".auto/plans/example"
		path.mkdir(parents=True)
		(path / "plan.md").write_text("---\nid: example\npolicy:\n  deploy.production: allow\n---\n# Example\n")
		(path / "tasks").mkdir()
		(path / "tasks/ship.md").write_text("---\nid: ship\neffects: [deploy.production]\n---\n# Ship\n")
		layers = [("user", {"deploy": "deny", "repo": "allow"}), ("project", {"deploy.production": "confirm"})]
		return path, Context(project=root, plan=path, policyLayers=layers)

	def testLayersAndGrants(self):
		with tempfile.TemporaryDirectory() as directory:
			path, context = self.makePlan(Path(directory))
			resolved = PolicyResolve(context, path, "ship")
			self.assertEqual(resolved["rules"]["deploy.production"], {"rule": "deny", "source": "user"})
			self.assertEqual(resolved["rules"]["repo.write"]["rule"], "allow")
			self.assertEqual(ruleFor("unknown.effect", context.policyLayers)["rule"], "confirm")
			self.assertIn("deploy.production", resolved["unenforceable"])
			context.knowledge = Store()
			with locked(path):
				commit(context, path, [{"op": "resolve", "decision": "decision-1"}], revision="revision-1")
			PolicyGrant(context, path, "deploy.production", "auto:example/task/ship", "decision-1")
			self.assertEqual(len(PolicyResolve(context, path, "ship")["grants"]), 1)
			self.assertFalse(PolicyResolve(context, path, "root")["grants"])
			with self.assertRaises(AutoError):
				PolicyGrant(context, path, "vcs.push", "auto:example", "decision-1")

	def testHumanGoalEffectsArePinned(self):
		from auto.features.elaboration.operations import PlanCreate
		with tempfile.TemporaryDirectory() as directory:
			root = Path(directory)
			context = Context(project=root)
			with patch("auto.features.elaboration.operations.PlanRefine", side_effect=lambda ctx, plan, reason: plan):
				path = PlanCreate(context, "Write notes and commit them", effects=["vcs.commit"])
				with self.assertRaises(AutoError):
					PlanCreate(context.derive(author="agent"), "Commit", effects=["vcs.commit"])
			resolved = PolicyResolve(context, path)
			self.assertEqual(resolved["grants"][0]["source"], "auto:write-notes-and-commit-them/task/root/definition")
			(path / "plan.md").write_text((path / "plan.md").read_text() + "\nChanged goal\n")
			self.assertEqual(PolicyResolve(context, path)["grants"], [])

# EOF
