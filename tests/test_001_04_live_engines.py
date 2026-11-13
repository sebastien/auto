"""Opt-in provider smoke checks; never spend inference during ordinary tests."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from auto.features.script.model import Script
from auto.features.script.operations import ScriptStart
from auto.runtime.model import Context, Limits
from auto.integration.opencode.engine import OpenCode


@unittest.skipUnless(os.environ.get("AUTO_LIVE_ENGINES") == "1", "Set AUTO_LIVE_ENGINES=1 to call real providers")
class LiveEngineTests(unittest.TestCase):
	def exercise(self, engine):
		self.assertIsNotNone(shutil.which(engine), f"Install {engine} before requesting live verification")
		with tempfile.TemporaryDirectory() as directory:
			root = Path(directory)
			config = {"engine": engine, "isolated": engine == "pi"}
			model = os.environ.get("AUTO_LIVE_" + engine.upper() + "_MODEL")
			if model:
				config["model"] = model
			context = Context(project=root, config=config, limits=Limits({"time": 45}))
			script = Script("Reply with exactly AUTO_SMOKE_OK, without quotes, markdown or explanation. "
				"Do not use tools, read files, access networks or modify anything.", root / "smoke.md", {"output": {"format": "text"}})
			events = list(ScriptStart(context, script))
			self.assertTrue(events)
			self.assertEqual(events[-1]["status"], "Succeeded", events[-1].get("failure"))
			self.assertEqual(events[-1]["output"].strip(), "AUTO_SMOKE_OK")

	def testPi(self):
		self.exercise("pi")

	def testOpenCode(self):
		self.exercise("opencode")

	def testLlm(self):
		self.exercise("llm")

	def testOpenCodeNativeFormAndPermissionCheckpointsWithoutProviderInference(self):
		def api(method, path, body=None):
			args = ["opencode", "api", method, path]
			if body is not None:
				args += ["--data", json.dumps(body)]
			result = subprocess.run(args, text=True, capture_output=True, timeout=15)
			self.assertEqual(result.returncode, 0, result.stderr)
		with tempfile.TemporaryDirectory() as directory:
			context = Context(project=Path(directory), limits=Limits({"time": 45}), variables={"_detach": True})
			engine = OpenCode()
			session = "ses" + uuid4().hex
			base = "/api/session/" + session
			engine.api(context, "post", "/api/session", {"id": session, "location": {"directory": directory},
				"permissions": [{"action": "*", "resource": "*", "effect": "ask"}]})
			try:
				for answer in (True, False):
					with self.subTest(permission=answer):
						api("post", base + "/permission", {"action": "edit", "resources": ["fixture.txt"]})
						request = {"session": session, "config": {}, "policy": {}, "prompt": "Unused", "output": {"format": "text"}}
						stream = engine.start(context, request)
						try:
							self.assertEqual(next(stream), {"type": "PolicyRequest", "effect": "repo.write", "action": "edit: fixture.txt"})
						finally:
							stream.close()
						self.assertEqual(len(engine.api(context, "get", base + "/permission")["data"]), 1)
						request = json.loads(json.dumps(request))
						request["answer"] = answer
						stream = OpenCode().start(context, request)
						try:
							self.assertEqual(next(stream)["type"], "ScriptMeta")
						finally:
							stream.close()
						self.assertEqual(engine.api(context, "get", base + "/permission")["data"], [])
				api("post", base + "/form", {"title": "Native form transport",
					"fields": [{"key": "answer", "type": "string", "required": True}]})
				request = {"session": session, "config": {}, "prompt": "Unused", "output": {"format": "text"}}
				stream = engine.start(context, request)
				try:
					question = next(stream)
					self.assertEqual(question["type"], "ScriptQuestion")
					self.assertEqual(question["fields"][0]["key"], "answer")
				finally:
					stream.close()
				self.assertEqual(len(engine.api(context, "get", base + "/form")["data"]), 1)
				# A fresh adapter resumes only the JSON checkpoint, not the old stream.
				request = json.loads(json.dumps(request))
				request["answer"] = {"answer": "yes"}
				stream = OpenCode().start(context, request)
				try:
					self.assertEqual(next(stream)["type"], "ScriptMeta")
				finally:
					stream.close()
				self.assertEqual(engine.api(context, "get", base + "/form")["data"], [])
			finally:
				api("delete", base)


# EOF
