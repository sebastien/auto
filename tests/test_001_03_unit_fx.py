"""Module: test_fx — fx wire results, settings and bounded execution."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from auto.features.script.model import Script
from auto.features.script.operations import ScriptStart
from auto.runtime.model import Context, Limits


class FxTests(unittest.TestCase):
	def setUp(self):
		self.directory = tempfile.TemporaryDirectory()
		self.addCleanup(self.directory.cleanup)
		self.root = Path(self.directory.name)
		self.binary = self.root / "fx"
		self.binary.write_text("""#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
Path('invocation.json').write_text(json.dumps({
    'args': sys.argv[1:], 'prompt': sys.stdin.read(),
    'model': os.environ.get('FX_MODEL'), 'provider': os.environ.get('FX_PROVIDER'),
    'custom': os.environ.get('CUSTOM'), 'cwd': os.getcwd()}))
time.sleep(float(os.environ.get('DELAY', '0')))
sys.stderr.write(os.environ.get('ERROR', ''))
sys.stdout.write(Path('result.json').read_text())
sys.exit(int(os.environ.get('EXIT', '0')))
""")
		self.binary.chmod(0o755)
		self.result = {"exit_code": 0, "output": "Intermediate\nDone", "final_output": "Done",
			"model": "vendor/model", "resolved_provider": "bedrock",
			"usage": {"input_tokens": 3, "output_tokens": 2},
			"tool_calls": [{"name": "read_file", "status": "success"}]}
		self.context = Context(project=self.root, config={"engine": "fx"}, limits=Limits(),
			environ={**os.environ, "PATH": str(self.root) + os.pathsep + os.environ["PATH"]})

	def runFx(self, meta=None, context=None, raw=None):
		(self.root / "result.json").write_text(json.dumps(self.result) if raw is None else raw)
		return list(ScriptStart(context or self.context, Script("Work", self.root / "work.md", meta or {})))

	def testWireAndSettings(self):
		events = self.runFx({"model": "chosen", "provider": "gateway", "prompt": "System",
			"cwd": str(self.root), "env": {"CUSTOM": "yes", "FX_MODEL": "ambient"}})
		self.assertEqual(events[-1]["status"], "Succeeded")
		self.assertEqual(events[-1]["output"], "Done")
		invocation = json.loads((self.root / "invocation.json").read_text())
		self.assertEqual(invocation["args"], ["ask", "--json", "--no-save", "--system", "System"])
		self.assertTrue(invocation["prompt"].startswith("Work"))
		self.assertEqual((invocation["model"], invocation["provider"], invocation["custom"]), ("chosen", "gateway", "yes"))
		self.assertEqual(invocation["cwd"], str(self.root))
		self.assertEqual(self.context.limits.usage["tokens"], 5)
		self.assertEqual(self.context.limits.usage["cost"], 0)
		self.assertTrue(any(e.get("name") == "read_file" and e["type"] == "ScriptAction" for e in events))
		self.assertTrue(any(e.get("name") == "runtime" and e["value"]["resolved_provider"] == "bedrock" for e in events))
		self.assertEqual([e["content"] for e in events if e["type"] == "ScriptMessage"], ["Done"])

	def testUnsupportedSettingsAndBudgetsNeverLaunch(self):
		for meta in ({"agent": "reviewer"}, {"isolated": True}, {"temperature": 0}):
			with self.subTest(meta=meta):
				event = self.runFx(meta)[-1]
				self.assertEqual(event["failure"]["kind"], "input")
		for dimension in ("tokens", "cost"):
			with self.subTest(dimension=dimension):
				event = self.runFx(context=self.context.derive(limits=Limits({dimension: 10})))[-1]
				self.assertEqual(event["failure"]["kind"], "input")
		self.assertFalse((self.root / "invocation.json").exists())

	def testInvalidResults(self):
		for raw in ("bad json", "[]", "{}", json.dumps({**self.result, "exit_code": False}),
			json.dumps({**self.result, "final_output": None}),
			json.dumps({**self.result, "usage": {"input_tokens": -1}}),
			json.dumps({**self.result, "tool_calls": [None]})):
			with self.subTest(raw=raw):
				event = self.runFx(raw=raw)[-1]
				self.assertEqual(event["status"], "Failed")
				self.assertEqual(event["failure"]["kind"], "system")

	def testFailedRunsAndPartialUsage(self):
		self.result.update(exit_code=1, error="NoCredits")
		event = self.runFx({"env": {"EXIT": "1"}})[-1]
		self.assertIn("NoCredits", event["failure"]["reason"])
		self.assertEqual(self.context.limits.usage["tokens"], 5)
		event = self.runFx({"env": {"EXIT": "1", "ERROR": "bad credential"}}, raw="")[-1]
		self.assertIn("bad credential", event["failure"]["reason"])
		self.result.update(exit_code=0, usage={"input_tokens": None, "output_tokens": 0})
		self.assertEqual(self.runFx()[-1]["status"], "Succeeded")
		event = self.runFx({"env": {"EXIT": "1"}})[-1]
		self.assertEqual(event["status"], "Failed")
		event = self.runFx({"env": {"EXIT": "130"}})[-1]
		self.assertEqual(event["status"], "Cancelled")

	def testTimeoutCancellationAndOutputLimit(self):
		event = self.runFx({"env": {"DELAY": "5"}}, context=self.context.derive(limits=Limits({"time": .1})))[-1]
		self.assertEqual(event["status"], "TimedOut")
		event = self.runFx(context=self.context.derive(cancelled=lambda: True))[-1]
		self.assertEqual(event["status"], "Cancelled")
		event = self.runFx(context=self.context.derive(maxBytes=10), raw="x" * 100)[-1]
		self.assertIn("byte limit", event["failure"]["reason"])

# EOF
