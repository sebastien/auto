"""Module: test_frontend — Human output and progress stay separate from results."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auto.app.cli.frontend import Frontend, clip, costText, safeText, width
from auto.integration.process import capture
from auto.runtime.model import Context


class Terminal(io.StringIO):
	def isatty(self):
		return True


class FrontendTests(unittest.TestCase):
	def testStreamingWithoutDuplicateOrChunkNewlines(self):
		out, err = io.StringIO(), io.StringIO()
		frontend = Frontend("text", stdout=out, stderr=err)
		frontend.event({"type": "ExecutionStarted", "output": {}})
		frontend.scriptEvent({"type": "ScriptMessage", "content": "Hel"})
		frontend.scriptEvent({"type": "ScriptMessage", "content": "lo\nworld"})
		frontend.scriptEvent({"type": "ScriptEnded", "status": "Succeeded", "output": "Hello\nworld"})
		self.assertEqual(out.getvalue(), "Hello\nworld")
		self.assertEqual(err.getvalue(), "")

	def testStructuredOutputIsOnlyPrintedOnce(self):
		out, err = io.StringIO(), io.StringIO()
		frontend = Frontend("text", stdout=out, stderr=err)
		frontend.event({"type": "ExecutionStarted", "output": {"format": "json"}})
		frontend.scriptEvent({"type": "ScriptMessage", "content": '{"a":'})
		frontend.scriptEvent({"type": "ScriptEnded", "status": "Succeeded", "output": {"a": 1}})
		self.assertEqual(out.getvalue(), '{\n  "a": 1\n}\n')

	def testTerminalProgressAndBudget(self):
		out, err = Terminal(), Terminal()
		with patch.dict(os.environ, {"TERM": "xterm", "NO_COLOR": "1"}):
			frontend = Frontend("text", stdout=out, stderr=err)
			frontend.event({"type": "RunStarted", "task": "build", "budget": {"cost": 1}})
			frontend.event({"type": "RunPhase", "phase": "Process"})
			frontend.event({"type": "ScriptMeta", "name": "usage", "value": {"cost": .01}})
			frontend.event({"type": "RunFinished", "run": {"status": "Succeeded", "usage": {"cost": .01}}})
		self.assertIn("Process", err.getvalue())
		self.assertIn("$0.0100 / $1.0000", err.getvalue())
		self.assertIn("✓ Succeeded", err.getvalue())
		self.assertNotIn("\x1b", err.getvalue())
		self.assertEqual(frontend.lastWidth, 0)

	def testTerminalChunksAndFailure(self):
		out, err = Terminal(), Terminal()
		with patch.dict(os.environ, {"TERM": "xterm", "NO_COLOR": "1"}):
			frontend = Frontend("text", stdout=out, stderr=err)
			frontend.event({"type": "ExecutionStarted", "output": {}})
			frontend.scriptEvent({"type": "ScriptMessage", "content": "hello"})
			frontend.scriptEvent({"type": "ScriptMessage", "content": " world\nnext"})
			frontend.scriptEvent({"type": "ScriptEnded", "status": "Failed", "failure": {"reason": "token=secret"}})
		self.assertEqual(out.getvalue(), "  │ hello world\n  │ next\n")
		self.assertIn("! Failed", err.getvalue())
		self.assertNotIn("secret", err.getvalue())

	def testLifecycleProgressAndModelLine(self):
		out, err = Terminal(), Terminal()
		with patch.dict(os.environ, {"TERM": "xterm", "NO_COLOR": "1"}):
			frontend = Frontend("text", stdout=out, stderr=err)
			frontend.event({"type": "ScriptItemStarted", "name": "rev", "value": "abc"})
			frontend.event({"type": "ScriptAction", "name": "pre", "content": "test -z abc", "lifecycle": True})
			self.assertEqual(frontend.operation, "pre: test -z abc")
			frontend.event({"type": "ExecutionStarted", "config": {"model": "example"}})
			self.assertIn("\r  › model: example\n", err.getvalue())
			frontend.event({"type": "ScriptAction", "name": "pre", "content": "tool event"})
			self.assertEqual(frontend.operation, "Use pre")

	def testDiagnosticColorsOnlyOnColorTerminals(self):
		for noColor in (False, True):
			out, err = Terminal(), Terminal()
			environ = {"TERM": "xterm", **({"NO_COLOR": "1"} if noColor else {})}
			with patch.dict(os.environ, environ, clear=True):
				frontend = Frontend("text", stdout=out, stderr=err)
				frontend.event({"type": "ScriptMeta", "name": "warning", "value": "warning text"})
				frontend.error("error text")
			self.assertEqual("\033[33m!\033[0m" in err.getvalue(), not noColor)
			self.assertEqual("\033[31mauto: error:\033[0m" in err.getvalue(), not noColor)

	def testFormattingAndProcessTicks(self):
		self.assertEqual(costText(None), "—")
		self.assertEqual(costText(.00001), "<$0.0001")
		self.assertNotIn("secret", safeText("token=secret"))
		for columns in range(10):
			self.assertLessEqual(width(clip("界long", columns)), columns)
		ticks = []
		capture(Context(tick=lambda: ticks.append(True)).process, ["sleep", ".12"], {})
		self.assertGreaterEqual(len(ticks), 2)

	def testCliScriptTextAndJsonContracts(self):
		with tempfile.TemporaryDirectory() as directory:
			root = Path(directory)
			engine = root / "llm"
			engine.write_text("#!/bin/sh\nprintf 'hello world'\n")
			engine.chmod(0o755)
			script = root / "script.md"
			script.write_text("Say hello")
			environ = {key: value for key, value in os.environ.items() if not key.startswith("AUTO_")}
			environ.update({"HOME": str(root), "XDG_CONFIG_HOME": str(root), "PATH": str(root) + os.pathsep + os.environ["PATH"]})
			command = [sys.executable, "-m", "auto.app.cli.main", "run", str(script), "--engine", "llm"]
			text = subprocess.run(command, capture_output=True, text=True, env=environ, cwd=root, check=False)
			self.assertEqual(text.returncode, 0, text.stderr)
			self.assertEqual(text.stdout, "hello world")
			structured = subprocess.run([*command, "--format", "json"], capture_output=True, text=True, env=environ, cwd=root, check=False)
			self.assertEqual(structured.returncode, 0, structured.stderr)
			events = [json.loads(line) for line in structured.stdout.splitlines()]
			self.assertEqual([event["type"] for event in events], ["ScriptMessage", "ScriptEnded"])
			self.assertEqual(events[-1]["output"], "hello world")


# EOF
