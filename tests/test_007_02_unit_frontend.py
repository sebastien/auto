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

from auto.app.cli.frontend import EventText, Frontend, clip, costText, countText, safeText, spendingText, width
from auto.features.script.operations import executePrepared
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
			frontend.event({"type": "RunFinished", "run": {"status": "Waiting"}})
			frontend.event({"type": "RunStarted", "task": "build", "budget": {"cost": 1}, "resuming": True})
			frontend.event({"type": "ScriptMeta", "name": "usage", "value": {"cost": .01}})
			frontend.event({"type": "RunFinished", "run": {"status": "Succeeded", "usage": {"cost": .01}}})
		self.assertIn("Process", err.getvalue())
		self.assertIn("? Waiting", err.getvalue())
		self.assertIn("Resuming task: build", err.getvalue())
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

	def testPlanProgressIncludesPriorRunsAndLabelsTheRunLimitSeparately(self):
		out, err = Terminal(), Terminal()
		with patch.dict(os.environ, {"TERM": "xterm", "NO_COLOR": "1"}):
			frontend = Frontend("text", stdout=out, stderr=err)
			frontend.event({"type": "RunStarted", "task": "elaborator", "budget": {"tokens": 5, "time": 30},
				"usage": {"tokens": 2, "time": 4}, "planUsage": {"tokens": 6, "time": 35, "cost": .1},
				"planBudget": {"tokens": 10, "time": 60, "cost": 1}})
			frontend.event({"type": "ScriptMeta", "name": "usage", "value": {"tokens": 3, "cost": .01}})
		self.assertIn("Run · cost — · 2 / 5 tokens · 00:04 / 00:30", err.getvalue())
		self.assertEqual(frontend.metrics(5), "Plan · cost $0.1100 / $1.0000 · 9 / 10 tokens · 00:40 / 01:00")

	def testLifecycleProgressAndRuntimeLine(self):
		out, err = Terminal(), Terminal()
		with patch.dict(os.environ, {"TERM": "xterm", "NO_COLOR": "1"}):
			frontend = Frontend("text", stdout=out, stderr=err)
			frontend.event({"type": "ScriptItemStarted", "name": "rev", "value": "abc"})
			frontend.event({"type": "ScriptAction", "name": "pre", "content": "test -z abc", "lifecycle": True})
			self.assertEqual(frontend.operation, "pre: test -z abc")
			frontend.event({"type": "ExecutionStarted", "config": {"engine": "opencode", "provider": "example-provider", "model": "example", "agent": "build"}})
			self.assertIn("\r  › engine: opencode · provider: example-provider · model: example · agent: build\n", err.getvalue())
			frontend.event({"type": "ScriptAction", "name": "pre", "content": "tool event"})
			self.assertEqual(frontend.operation, "Use pre")

	def testRuntimeLineDefaults(self):
		out, err = Terminal(), Terminal()
		with patch.dict(os.environ, {"TERM": "xterm", "NO_COLOR": "1"}):
			frontend = Frontend("text", stdout=out, stderr=err)
			frontend.event({"type": "ExecutionStarted", "config": {}})
		self.assertIn("  › engine: pi · provider: engine default · model: engine default · agent: engine default\n", err.getvalue())
		self.assertEqual(out.getvalue(), "")

	def testRuntimeLineNamesTask(self):
		for event, label in (({"task": "tsk-index", "kind": "task"}, "task: tsk-index · "),
				({"task": "root", "kind": "triage"}, "task: root (triage) · "),
				({"task": "tsk-index", "kind": "assessment", "check": "review"}, "task: tsk-index (assessment: review) · ")):
			out, err = Terminal(), Terminal()
			with patch.dict(os.environ, {"TERM": "xterm", "NO_COLOR": "1"}):
				Frontend("text", stdout=out, stderr=err).event({"type": "ExecutionStarted", "config": {}, **event})
			self.assertIn("  › " + label + "engine: pi", err.getvalue())

	def testLogLinesNameWhatEachEventBelongsTo(self):
		text = EventText(times=True, level="debug")
		started = text.line({"seq": 3, "kind": "RunStarted", "date": "2026-11-18T06:40:24", "data": {"run": "run-1", "task": "elaborator"}})
		self.assertEqual(started, "11-18 06:40  ● planning  #3 run-1")
		violations = json.dumps([{"element": "elaboration", "message": "Invalid gap: none"}])
		lines = text.line({"run": "run-1", "task": "elaborator", "index": 4, "event": {"type": "TaskChecked", "check": "post",
			"verdict": {"pass": False, "reason": violations}}}).split("\n")
		self.assertEqual([line.strip() for line in lines], ["planning ✗ check post", "planning   - elaboration: Invalid gap: none".strip()])
		self.assertTrue(all(line.startswith(" " * 17) for line in lines))
		ended = text.line({"run": "run-1", "task": "elaborator", "index": 5, "event": {"type": "ScriptEnded", "status": "Succeeded", "output": "a\nb\nc"}})
		self.assertIn("✓ script succeeded  output: a (+2 lines)", ended)
		trace = EventText(level="trace").line({"run": "run-1", "task": "work", "index": 6, "event": {"type": "ScriptMessage", "content": "one\ntwo"}})
		self.assertEqual(trace.split("\n"), ["    work │ one", "    work │ two"])
		self.assertEqual(EventText(level="info").line({"seq": 3, "kind": "RunStarted", "data": {"run": "run-1", "task": "work"}}), "● work")
		def tool(**event):
			return EventText().line({"run": "run-1", "task": "work", "index": 7, "event": {"type": "ScriptAction", **event}}).strip()
		self.assertEqual(tool(name="edit", content="completed", access="write", files=["a.md", "b.md"]), "work ✎ edit a.md, b.md")
		self.assertEqual(tool(name="grep", content="completed", access="read", files=["src"], detail="TODO"), "work · grep src")
		self.assertEqual(tool(name="bash", content="completed", access="use", detail="make test"), "work · bash make test")
		self.assertEqual(tool(name="write", content="error", access="write", files=["c.md"], error="Denied\nmore"), "work ✗ write c.md: Denied")
		ended = {"seq": 9, "kind": "RunEnded", "data": {"run": "run-1", "status": "Succeeded", "files": {"read": ["x"], "written": list("abcdef")}}}
		self.assertIn("wrote a, b, c, d (+2 more)", EventText(level="debug").line(ended))
		self.assertIn("wrote 6 files", EventText(level="info").line(ended))

	def testLogWatchIsFollow(self):
		from auto.app.cli.main import parser
		args = parser().parse_args(["log", "--watch", "--level", "trace"])
		self.assertEqual((args.follow, args.level), (True, "trace"))

	def testTokenCounts(self):
		for count, text in ((850, "850"), (12_345, "12.3k"), (12_000, "12k"), (1_234_567, "1.2M"), (456_789, "457k"), (1_250_000_000, "1,250M")):
			self.assertEqual(countText(count), text)
		frontend = Frontend("text", stdout=Terminal(), stderr=Terminal())
		frontend.tokens, frontend.budget = 12_345, {"tokens": 200_000}
		self.assertIn("12.3k / 200k tokens", frontend.metrics(0))

	def testPreparedExecutionsReportRuntime(self):
		events = []
		request = {"config": {"provider": "example-provider", "model": "example", "agent": "build"}, "output": {"format": "json"}}
		def engine():
			yield from ()
		with tempfile.TemporaryDirectory() as directory:
			context = Context(project=Path(directory), observe=events.append)
			stream = executePrepared(context, request, transform=True)
			with patch("auto.features.script.operations._executePrepared", return_value=engine()):
				list(stream)
		self.assertEqual(events, [{"type": "ExecutionStarted", "config": {"engine": "pi", **request["config"]},
			"output": {"format": "json"}, "transform": True}])

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

	def testSpendingByStage(self):
		stages = [{"stage": "planning", "tokens": 140_074, "turns": 10, "cost": None, "cost_estimate": .021},
			{"stage": "tasks", "tokens": 850, "turns": 1, "cost": .5, "cost_estimate": .5}, {"stage": "checks", "tokens": None, "turns": None, "cost": None, "cost_estimate": None}]
		self.assertEqual(spendingText(stages), "planning 140k tokens 10 turns ≈$0.02 · tasks 850 tokens 1 turn $0.50 · checks")
		self.assertEqual(spendingText([]), "")

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
