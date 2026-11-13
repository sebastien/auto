"""Module: test_engines — Engine wire contracts and process cancellation."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from auto.features.script.model import Script
from auto.features.script.operations import ScriptStart
from auto.integration.opencode.engine import OpenCode
from auto.integration.process import capture
from auto.runtime.model import Context, Limits
from auto.utils.data import AutoError


class EngineTests(unittest.TestCase):
	def testLargeStdinAndTimeout(self):
		ctx = Context(maxBytes=1024 * 1024)
		text = "x" * 100000
		out, _, code = capture(ctx.process, ["cat"], {}, text)
		self.assertEqual(out, text)
		self.assertEqual(code, 0)
		with self.assertRaises(AutoError) as failure:
			capture(ctx.derive(limits=Limits({"time": .05})).process, ["sleep", "5"], {})
		self.assertEqual(failure.exception.kind, "timeout")

	def testPiWireAndBudget(self):
		with tempfile.TemporaryDirectory() as directory:
			root = Path(directory)
			pi = root / "pi"
			events = [
				{"type": "message_start", "message": {"role": "assistant"}},
				{"type": "message_update", "usage": {"totalTokens": 4, "cost": {"total": .01}}, "assistantMessageEvent": {"type": "text_delta", "delta": "Hello"}},
				{"type": "message_end", "message": {"role": "assistant", "content": [{"type": "text", "text": "Hello"}], "usage": {"totalTokens": 4, "cost": {"total": .01}}, "stopReason": "stop"}},
				{"type": "agent_settled", "aborted": False},
			]
			pi.write_text("#!/usr/bin/env python3\nimport sys\nfrom pathlib import Path\nPath('args').write_text('\\n'.join(sys.argv[1:]))\nsys.stdin.read()\nprint(" + repr("\n".join(json.dumps(event) for event in events)) + ")\n")
			pi.chmod(0o755)
			ctx = Context(project=root, config={"engine": "pi"}, limits=Limits({"tokens": 10}), environ={**os.environ, "PATH": str(root) + os.pathsep + os.environ["PATH"]})
			result = list(ScriptStart(ctx, Script("Hello", root / "hello.md")))[-1]
			self.assertEqual(result["status"], "Succeeded")
			self.assertNotIn("--no-context-files", (root / "args").read_text())
			result = list(ScriptStart(ctx.derive(limits=Limits({})), Script("Hello", root / "hello.md", {"isolated": True})))[-1]
			self.assertEqual(result["status"], "Succeeded")
			self.assertIn("--no-context-files", (root / "args").read_text())
			self.assertIn("--no-extensions", (root / "args").read_text())
			self.assertNotIn("--no-mcp", (root / "args").read_text())
			self.assertNotIn("--no-approve", (root / "args").read_text())
			self.assertEqual(ctx.limits.usage["tokens"], 4)
			for temperature in (0, .1):
				with self.subTest(temperature=temperature):
					result = list(ScriptStart(ctx.derive(limits=Limits({})), Script("Hello", root / "hello.md", {"temperature": temperature})))
					self.assertFalse(any(event.get("name") == "warning" and "temperature" in event.get("value", "") for event in result))
					self.assertEqual(result[-1]["status"], "Succeeded")
					self.assertEqual(result[-1]["output"], "Hello")
			result = list(ScriptStart(ctx.derive(limits=Limits({"tokens": 2})), Script("Hello", root / "hello.md")))[-1]
			self.assertEqual(result["failure"]["kind"], "budget")
			for meta, expected, absent in (
				({"tools": []}, ["--no-tools"], "--tools"),
				({"tools": ["read", "grep"]}, ["--tools", "read,grep"], "--no-tools"),
				({"provider": "openai", "model": "gpt-6-luna"}, ["--model", "gpt-6-luna", "--provider", "openai"], "--no-tools"),
			):
				with self.subTest(meta=meta):
					result = list(ScriptStart(ctx.derive(limits=Limits({})), Script("Hello", root / "hello.md", meta)))[-1]
					self.assertEqual(result["status"], "Succeeded")
					args = (root / "args").read_text().splitlines()
					self.assertEqual(args[args.index(expected[0]):args.index(expected[0]) + len(expected)], expected)
					self.assertNotIn(absent, args)

	def testPiCurrentJsonAgentEndAndAbortedMessages(self):
		from unittest.mock import patch
		from auto.integration.pi.engine import Pi
		for stop, terminal in (("stop", "agent_end"), ("stop", "agent_settled"), ("aborted", "agent_end"), ("error", "agent_end")):
			with self.subTest(stop=stop, terminal=terminal):
				events = [
					{"type": "message_start", "message": {"role": "assistant"}},
					{"type": "message_end", "message": {"role": "assistant", "content": [{"type": "text", "text": "Done"}],
						"usage": {"totalTokens": 4, "cost": {"total": .01}}, "stopReason": stop}},
					{"type": terminal},
				]
				data = ("\n".join(json.dumps(event) for event in events) + "\n").encode()
				context = Context(config={"engine": "pi"}, limits=Limits({"tokens": 10}))
				def stream():
					yield 0, data
					yield 2, 0
				with patch("auto.integration.pi.engine.streamProcess", return_value=stream()):
					if stop == "stop":
						result = list(Pi().start(context, {"config": {}, "prompt": "Work"}))[-1]
						self.assertEqual(result["status"], "Succeeded")
						self.assertEqual(result["output"], "Done")
					else:
						with self.assertRaises(AutoError):
							list(Pi().start(context, {"config": {}, "prompt": "Work"}))
				self.assertEqual(context.limits.usage["tokens"], 4)

	def testOpenCodeFormsAndCancellation(self):
		class Stub(OpenCode):
			def __init__(self):
				self.calls, self.answered = [], False
			def api(self, context, method, path, data=None, config=None):
				self.calls.append((method, path, data))
				if path.endswith("/reply"):
					self.answered = True
				if path.endswith("/form"):
					return {"data": [] if self.answered else [{"id": "form1", "title": "Choose", "fields": [{"id": "answer", "type": "text"}]}]}
				if "/message?" in path:
					return {"data": [{"id": "answer", "type": "assistant", "content": [{"type": "text", "text": "Done"}]}, {"id": "idle", "type": "idle", "outcome": "succeeded"}], "cursor": {}}
				return {}
		engine = Stub()
		ctx = Context(engines={"opencode": engine}, config={"engine": "opencode"}, question=lambda event: {"answer": "yes"})
		result = list(ScriptStart(ctx, Script("Work", Path("work.md"))))[-1]
		self.assertEqual(result["output"], "Done")
		prompt = next(data for method, path, data in engine.calls if path.endswith("/prompt"))
		self.assertRegex(prompt["id"], r"^msg_")
		self.assertTrue(prompt["text"].startswith("Work"))
		self.assertTrue(engine.answered)
		for temperature in (0, .1):
			with self.subTest(temperature=temperature):
				result = list(ScriptStart(ctx, Script("Work", Path("work.md"), {"temperature": temperature})))
				self.assertFalse(any(event.get("name") == "warning" and "temperature" in event.get("value", "") for event in result))
				self.assertEqual(result[-1]["status"], "Succeeded")
				self.assertEqual(result[-1]["output"], "Done")
		stream = ScriptStart(ctx, Script("Work", Path("work.md")))
		next(stream)
		stream.close()
		self.assertTrue(engine.calls[-1][1].endswith("/interrupt"))

	def testOpenCodeWaitsForSettledOutput(self):
		class Stub(OpenCode):
			def __init__(self):
				self.polls = 0
			def api(self, context, method, path, data=None, config=None):
				if path.endswith("/form"):
					return {"data": []}
				if "/message?" in path:
					self.polls += 1
					text = "Done" if self.polls > 2 else ""
					return {"data": [{"id": "answer", "type": "assistant", "content": [{"type": "text", "text": text}]},
						{"id": "idle", "type": "idle", "outcome": "succeeded"}], "cursor": {}}
				return {}
		engine = Stub()
		ctx = Context(engines={"opencode": engine}, config={"engine": "opencode"})
		result = list(ScriptStart(ctx, Script("Work", Path("work.md"))))[-1]
		self.assertEqual(result["status"], "Succeeded")
		self.assertEqual(result["output"], "Done")
		self.assertGreater(engine.polls, 2)

	def testOpenCodeKeepsOutputSeenBeforeIdle(self):
		class Stub(OpenCode):
			def __init__(self):
				self.polls = 0
			def api(self, context, method, path, data=None, config=None):
				if path.endswith("/form"):
					return {"data": []}
				if "/message?" in path:
					self.polls += 1
					if self.polls == 1:
						return {"data": [{"id": "answer", "type": "assistant", "content": [{"type": "text", "text": "Done"}]}], "cursor": {}}
					return {"data": [{"id": "answer", "type": "assistant", "content": [{"type": "text", "text": ""}]},
						{"id": "idle", "type": "idle", "outcome": "succeeded"}], "cursor": {}}
				return {}
		engine = Stub()
		ctx = Context(engines={"opencode": engine}, config={"engine": "opencode"})
		result = list(ScriptStart(ctx, Script("Work", Path("work.md"))))[-1]
		self.assertEqual(result["status"], "Succeeded")
		self.assertEqual(result["output"], "Done")

	def testOpenCodePolicyPermissionAndResume(self):
		class Stub(OpenCode):
			def __init__(self):
				self.calls = []
				self.replied = False
			def api(self, context, method, path, data=None, config=None):
				self.calls.append((method, path, data))
				if "/permission/" in path and path.endswith("/reply"):
					self.replied = True
				if path.endswith("/permission"):
					return {"data": [] if self.replied else [{"id": "per1", "action": "edit", "resources": ["a.txt"]}]}
				if path.endswith("/form"):
					return {"data": []}
				if "/message?" in path:
					return {"data": [{"id": "idle", "type": "idle", "outcome": "succeeded"}], "cursor": {}}
				return {}
		engine = Stub()
		context = Context(variables={"_detach": True})
		request = {"config": {}, "prompt": "Work", "policy": {}}
		stream = engine.start(context, request)
		self.assertEqual(next(stream)["type"], "ScriptMeta")
		self.assertEqual(next(stream), {"type": "PolicyRequest", "effect": "repo.write", "action": "edit: a.txt"})
		stream.close()
		create = next(data for method, path, data in engine.calls if path == "/api/session")
		self.assertEqual(create["permissions"][0]["effect"], "ask")
		self.assertEqual(create["permissions"][-1]["action"], "subagent")
		request["answer"] = True
		self.assertEqual(list(engine.start(context, request))[-1]["status"], "Succeeded")
		self.assertTrue(any(path.endswith("/permission/per1/reply") and data == {"decision": "once"} for _, path, data in engine.calls))

	def testUnsupportedExecutionSettingsStillFail(self):
		for config, reason in (
			({"engine": "pi", "agent": "custom", "temperature": .1}, "named agents"),
			({"engine": "pi", "provider": "openai"}, "needs a model"),
			({"engine": "llm", "tools": ["read"]}, "without tools"),
			({"engine": "opencode", "tools": []}, "cannot restrict tools"),
			({"engine": "fx", "tools": []}, "cannot restrict tools"),
			({"engine": "pi", "tools": "read"}, "tools must be a list"),
			({"engine": "pi", "tools": ["read; rm"]}, "tools must be a list"),
			({"engine": "opencode", "isolated": True, "temperature": .1}, "isolation"),
			({"engine": "opencode", "isolated": False, "prompt": "Custom", "temperature": .1}, "system prompt"),
		):
			with self.subTest(config=config):
				result = list(ScriptStart(Context(config=config), Script("Work", Path("work.md"))))
				self.assertEqual(len(result), 1)
				self.assertEqual(result[0]["status"], "Failed")
				self.assertEqual(result[0]["failure"]["kind"], "input")
				self.assertIn(reason, result[0]["failure"]["reason"])

# EOF
