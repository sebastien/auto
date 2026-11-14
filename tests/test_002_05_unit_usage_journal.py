"""Module: test_usage_journal — One usage journal line per engine invocation."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from auto.features.script.model import Script
from auto.features.script.operations import ScriptStart
from auto.runtime.model import Context


class Reporting:
	"""Fake engine that reports usage and the model it actually ran."""

	def start(self, context, request):
		context.limits.usage["tokens"] += 120
		context.limits.usage["cost"] += .25
		yield {"type": "ScriptMeta", "name": "usage", "value": dict(context.limits.usage)}
		yield {"type": "ScriptMeta", "name": "runtime", "value": {"provider": "anthropic", "model": "claude-opus-5-5"}}
		yield {"type": "ScriptEnded", "status": "Succeeded", "output": "done"}


class Silent:
	"""Fake engine that reports no usage and fails."""

	def start(self, context, request):
		yield {"type": "ScriptEnded", "status": "Failed", "output": None, "failure": {"kind": "system", "reason": "boom"}}


class UsageJournalTests(unittest.TestCase):
	def setUp(self):
		temp = tempfile.TemporaryDirectory()
		self.addCleanup(temp.cleanup)
		self.root = Path(temp.name)
		(self.root / ".auto").mkdir()
		self.journal = self.root / ".auto/usage.jsonl"

	def run_script(self, engine, **defaults):
		ctx = Context(project=self.root, defaults=defaults, engines={"fake": engine}, config={"engine": "fake", "model": "configured"})
		return list(ScriptStart(ctx, Script("Hello", self.root / "script.md", {})))

	def entries(self):
		return [json.loads(line) for line in self.journal.read_text().splitlines()]

	def testReportedUsageAndRuntimeModel(self):
		self.run_script(Reporting())
		self.run_script(Reporting())
		first, second = self.entries()
		self.assertEqual((first["engine"], first["provider"], first["model"]), ("fake", "anthropic", "claude-opus-5-5"))
		self.assertEqual((first["status"], first["kind"], first["tokens"], first["cost"]), ("Succeeded", "script", 120, .25))
		self.assertEqual(second["tokens"], 120, "usage is a per-invocation delta")
		self.assertFalse(first["usage_complete"], "only known engines report complete usage")
		self.assertGreaterEqual(first["duration_s"], 0)

	def testMissingUsageIsNullAndFailureRecorded(self):
		self.run_script(Silent())
		(entry,) = self.entries()
		self.assertIsNone(entry["tokens"])
		self.assertIsNone(entry["cost"])
		self.assertEqual((entry["status"], entry["failure"]["reason"], entry["model"]), ("Failed", "boom", "configured"))

	def testDisabledOrOutsideProject(self):
		self.run_script(Reporting(), journal=False)
		self.assertFalse(self.journal.exists())
		(self.root / ".auto").rmdir()
		self.run_script(Reporting())
		self.assertFalse((self.root / ".auto").exists())


if __name__ == "__main__":
	unittest.main()
