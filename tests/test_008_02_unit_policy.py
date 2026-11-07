"""Checks for layered policy resolution and scoped decision grants."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auto.features.plan.policy import PolicyGrant, PolicyResolve, ruleFor
from auto.runtime.model import Context
from auto.utils.data import AutoError


def test_policy_layers_and_grants(tmp_path):
	path = tmp_path / ".auto/plans/example"
	path.mkdir(parents=True)
	(path / "plan.md").write_text('---\nid: example\npolicy:\n  deploy.production: allow\n---\n# Example\n')
	(path / "tasks").mkdir()
	(path / "tasks/ship.md").write_text('---\nid: ship\neffects: [deploy.production]\n---\n# Ship\n')
	layers = [("user", {"deploy": "deny", "repo": "allow"}), ("project", {"deploy.production": "confirm"})]
	context = Context(project=tmp_path, plan=path, defaults={"_policyLayers": layers})
	resolved = PolicyResolve(context, path, "ship")
	assert resolved["rules"]["deploy.production"] == {"rule": "deny", "source": "user"}
	assert resolved["rules"]["repo.write"]["rule"] == "allow"
	assert ruleFor("unknown.effect", layers)["rule"] == "confirm"
	assert "deploy.production" in resolved["unenforceable"]

	class Store:
		def get(self, ref):
			return {"id": "decision-1", "revision": "revision-1", "identity": ref, "plan": "auto:example", "author": "human", "answer": {"grant": "deploy.production", "scope": "auto:example/task/ship"}}

	context.knowledge = Store()
	from auto.features.plan.store import commit, locked
	with locked(path):
		commit(context, path, [{"op": "resolve", "decision": "decision-1"}], revision="revision-1")
	PolicyGrant(context, path, "deploy.production", "auto:example/task/ship", "decision-1")
	assert len(PolicyResolve(context, path, "ship")["grants"]) == 1
	assert not PolicyResolve(context, path, "root")["grants"]
	try:
		PolicyGrant(context, path, "vcs.push", "auto:example", "decision-1")
	except AutoError:
		pass
	else:
		assert False, "Unrelated decisions must not grant effects"


class PolicyTest(unittest.TestCase):
	def test_layers_and_grants(self):
		with tempfile.TemporaryDirectory() as directory:
			test_policy_layers_and_grants(Path(directory))

	def test_human_goal_effects_are_pinned(self):
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
