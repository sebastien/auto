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
			self.assertEqual(ctx.limits.usage["tokens"], 4)
			for temperature in (0, .1):
				with self.subTest(temperature=temperature):
					result = list(ScriptStart(ctx.derive(limits=Limits({})), Script("Hello", root / "hello.md", {"temperature": temperature})))
					self.assertEqual(result[0]["type"], "ScriptMeta")
					self.assertEqual(result[0]["name"], "warning")
					self.assertIn("temperature", result[0]["value"])
					self.assertEqual(result[-1]["status"], "Succeeded")
					self.assertEqual(result[-1]["output"], "Hello")
			result = list(ScriptStart(ctx.derive(limits=Limits({"tokens": 2})), Script("Hello", root / "hello.md")))[-1]
			self.assertEqual(result["failure"]["kind"], "budget")

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
				self.assertEqual(result[0]["type"], "ScriptMeta")
				self.assertEqual(result[0]["name"], "warning")
				self.assertIn("temperature", result[0]["value"])
				self.assertEqual(result[-1]["status"], "Succeeded")
				self.assertEqual(result[-1]["output"], "Done")
		stream = ScriptStart(ctx, Script("Work", Path("work.md")))
		next(stream)
		stream.close()
		self.assertTrue(engine.calls[-1][1].endswith("/interrupt"))

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
