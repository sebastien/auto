"""Module: test_usage_journal — One usage journal line per engine invocation."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from auto.features.script.model import Script
from auto.features.script.operations import ScriptStart
from auto.features.usage.operations import UsageEntries, UsageStages, UsageSummary, stageOf
from auto.runtime.model import Context
from auto.utils.data import AutoError


class Reporting:
	"""Fake engine that reports usage and the model it actually ran."""

	def start(self, context, request):
		context.limits.usage["tokens"] += 120
		context.limits.usage["cost"] += .25
		yield {"type": "ScriptMeta", "name": "usage", "value": dict(context.limits.usage)}
		yield {"type": "ScriptMeta", "name": "runtime", "value": {"provider": "anthropic", "model": "claude-opus-5-5"}}
		yield {"type": "ScriptEnded", "status": "Succeeded", "output": "done"}


class Subscribed:
	"""Fake engine on a subscription: it reports tokens by kind, and no cost."""

	def start(self, context, request):
		context.limits.usage["tokens"] += 3_000_000
		yield {"type": "ScriptMeta", "name": "usage", "value": dict(context.limits.usage)}
		yield {"type": "ScriptMeta", "name": "runtime", "value": {"provider": "openai", "model": "gpt-6-luna"}}
		yield {"type": "ScriptMeta", "name": "tokens", "value": {"turns": 1, "input": 1_000_000, "output": 0, "cache_read": 0}}
		yield {"type": "ScriptMeta", "name": "tokens", "value": {"turns": 2, "input": 1_000_000, "output": 1_000_000, "cache_read": 1_000_000}}
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

	def testTokensByKindAndEstimatedCost(self):
		self.run_script(Subscribed())
		(entry,) = self.entries()
		self.assertEqual({key: entry[key] for key in ("turns", "input", "output", "cache_read")},
			{"turns": 2, "input": 1_000_000, "output": 1_000_000, "cache_read": 1_000_000}, "the last report is the run's breakdown")
		self.assertEqual(entry["cost_estimate"], .61, "built-in luna prices: $0.10 input, $0.50 output, $0.01 cached")
		self.journal.unlink()
		self.run_script(Subscribed(), prices={"openai/gpt-6-luna": {"input": 1, "output": 2}})
		(entry,) = self.entries()
		self.assertEqual(entry["cost_estimate"], 4, "configured prices win; unpriced cache reads cost as input")
		self.journal.unlink()
		self.run_script(Reporting())
		self.assertNotIn("cost_estimate", self.entries()[0], "a reported cost is not estimated")

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


class UsageSummaryTests(unittest.TestCase):
	def setUp(self):
		temp = tempfile.TemporaryDirectory()
		self.addCleanup(temp.cleanup)
		self.root = Path(temp.name)
		(self.root / ".auto").mkdir()
		rows = [
			{"ts": "2026-11-13T09:00:00+00:00", "kind": "task", "engine": "pi", "model": "a", "status": "Succeeded", "duration_s": 10, "tokens": 100, "cost": .5, "usage_complete": True},
			{"ts": "2026-11-14T09:00:00+00:00", "kind": "task", "engine": "pi", "model": "a", "status": "Failed", "duration_s": 5, "tokens": 50, "cost": .25, "usage_complete": True},
			{"ts": "2026-11-14T10:00:00+00:00", "kind": "script", "engine": "llm", "model": "b", "status": "Succeeded", "duration_s": 2, "tokens": None, "cost": None, "usage_complete": False},
		]
		(self.root / ".auto/usage.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows) + "not json\n")
		self.ctx = Context(project=self.root)

	def testSummaryGroupsAndKeepsUnknownUsageNull(self):
		entries, skipped = UsageEntries(self.ctx)
		self.assertEqual(skipped, 1)
		pi, llm = UsageSummary(entries, ("engine",))
		self.assertEqual((pi["runs"], pi["failed"], pi["tokens"], pi["cost"], pi["partial"]), (2, 1, 150, .75, 0))
		self.assertEqual((llm["tokens"], llm["cost"], llm["partial"]), (None, None, 1))
		self.assertEqual([row["day"] for row in UsageSummary(entries, ("day",))], ["2026-11-13", "2026-11-14"])

	def testStagesNameWhatEachInvocationWasFor(self):
		cases = [({"kind": "triage"}, "planning"), ({"kind": "elaboration", "phase": "Process", "attempt": 0}, "planning"), ({"kind": "elaboration", "phase": "Process", "attempt": 1}, "repairs"),
			({"kind": "elaboration", "phase": "Post"}, "checks"), ({"kind": "task", "phase": "Process"}, "tasks"), ({"kind": "assessment", "phase": "Evaluation"}, "checks"),
			({"kind": "memory"}, "memory"), ({"kind": "task"}, "tasks")]
		for entry, stage in cases:
			self.assertEqual(stageOf(entry), stage, entry)
		rows = [{"ts": "2026-11-14T09:00:00+00:00", "plan": "pln-000-x", "kind": "task", "phase": "Process", "attempt": 0, "tokens": 10, "turns": 1, "cost": .5},
			{"ts": "2026-11-14T09:00:00+00:00", "plan": "pln-000-x", "kind": "elaboration", "phase": "Process", "attempt": 0, "tokens": 30, "cost_estimate": .1},
			{"ts": "2026-11-14T09:00:00+00:00", "plan": "pln-001-y", "kind": "task", "phase": "Process", "tokens": 99}]
		(self.root / ".auto/usage.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
		stages = UsageStages(self.ctx, "pln-000-x")
		self.assertEqual([(stage["stage"], stage["tokens"], stage["cost_estimate"]) for stage in stages], [("planning", 30, .1), ("tasks", 10, .5)])

	def testFilters(self):
		entries, _ = UsageEntries(self.ctx, since="2026-11-14", where={"kind": "task"})
		self.assertEqual([entry["status"] for entry in entries], ["Failed"])
		with self.assertRaises(AutoError):
			UsageEntries(self.ctx, where={"nope": "x"})
		with self.assertRaises(AutoError):
			UsageSummary(entries, ("nope",))
		with self.assertRaises(AutoError):
			UsageEntries(self.ctx, since="soon")

	def testCommand(self):
		def auto(*args):
			return subprocess.run([sys.executable, "-m", "auto.app.cli.main", "x", "usage", *args], cwd=self.root, text=True, capture_output=True)
		result = auto("--format", "tsv", "--by", "engine")
		self.assertEqual(result.returncode, 0, result.stderr)
		self.assertEqual(result.stdout.splitlines()[0].split("\t"), ["engine", "runs", "failed", "duration_s", "turns", "input", "output", "cache_read",
			"tokens", "cost", "cost_estimate", "partial"])
		self.assertIn("llm\t1\t0\t2.0" + "\t" * 8 + "1", result.stdout)
		# Breakdown columns nobody reported are left out of the table.
		self.assertNotIn("CACHED", auto("--by", "engine").stdout)
		result = auto("--by", "engine")
		self.assertIn("total", result.stdout.splitlines()[-1])
		self.assertIn("lower bounds", result.stderr)
		self.assertEqual(len(json.loads(auto("--entries", "--format", "json").stdout)), 3)
		self.assertEqual(auto("--by", "nope").returncode, 2)


if __name__ == "__main__":
	unittest.main()
