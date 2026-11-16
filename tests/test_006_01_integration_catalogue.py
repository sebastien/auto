"""Module: test_catalogue — Shipped assets exercise the ordinary task lifecycle."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from auto.features.elaboration.catalogue import CatalogueList, CatalogueGet
from auto.features.elaboration.operations import PlanCreate
from auto.features.plan.operations import PlanStatus
from auto.runtime.model import Context


class CatalogueTests(unittest.TestCase):
	def testShippedElaboratorEndToEnd(self):
		elaboration = """---
task: root
summary: One task
---
## Rationale
The goal is small.
## task: work
```yaml
title: Do the work
```
### Process
Produce the result.
"""
		class Fake:
			def __init__(self):
				self.calls = []
				# Post is a deterministic shell check: the model elaborates, then evaluates
				self.outputs = iter([elaboration, '{"score":0.9,"feedback":"Good"}'])
			def start(self, context, request):
				self.calls.append(request["prompt"])
				yield {"type": "ScriptEnded", "status": "Succeeded", "output": next(self.outputs)}
		with tempfile.TemporaryDirectory() as directory:
			root, fake = Path(directory), Fake()
			ctx = Context(project=root, engines={"fake": fake}, config={"engine": "fake"})
			ctx.environ["PATH"] = str(Path(__file__).resolve().parents[1] / ".venv/bin") + os.pathsep + ctx.environ["PATH"]
			result = PlanCreate(ctx, "A small goal")
			self.assertTrue(result["applied"], result)
			self.assertEqual(len(fake.calls), 2)
			self.assertIn("coarse", fake.calls[0].lower())
			self.assertIn("set `task: root`", fake.calls[0])
			self.assertEqual(PlanStatus(ctx, result["plan"])["tasks"]["tsk-work"], "Ready")

	def testProjectLayerAndCompanions(self):
		with tempfile.TemporaryDirectory() as directory:
			ctx = Context(project=Path(directory))
			path = ctx.project / ".auto/catalogue/profile/default.md"
			path.parent.mkdir(parents=True)
			path.write_text("---\nkind: profile\ntitle: Local\n---\nLocal")
			(path.parent / "default.example.md").write_text("companion")
			entries = {entry["identity"]: entry for entry in CatalogueList(ctx, "profile")}
			self.assertEqual(set(entries), {"auto:catalogue/profile/default", "auto:catalogue/profile/decision"}, "companions are not entries")
			self.assertEqual(entries["auto:catalogue/profile/default"]["layer"], "project")
			self.assertEqual(entries["auto:catalogue/profile/decision"]["layer"], "builtin")
			self.assertEqual(CatalogueGet(ctx, "auto:catalogue/profile/default")["title"], "Local")

# EOF
