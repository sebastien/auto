"""Opt-in provider smoke checks; never spend inference during ordinary tests."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from auto.features.script.model import Script
from auto.features.script.operations import ScriptStart
from auto.runtime.model import Context, Limits
from auto.integration.opencode.engine import OpenCode
from support import rootFile


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

	def modelControls(self, actions):
		from auto.features.plan.operations import PlanRevise
		from auto.features.task.operations import RunGet, RunOutput, TaskStart
		from auto.integration.memo.store import MemoKnowledge
		for action in actions:
			with self.subTest(action=action), tempfile.TemporaryDirectory() as directory:
				root = Path(directory)
				plan = root / ".auto/live"
				plan.mkdir(parents=True)
				rootFile(plan).write_text("---\nid: live\n---\n# Live control check\n")
				config = {"engine": "opencode"}
				if os.environ.get("AUTO_LIVE_OPENCODE_MODEL"):
					config["model"] = os.environ["AUTO_LIVE_OPENCODE_MODEL"]
				context = Context(project=root, plan=plan, config=config, knowledge=MemoKnowledge(root),
					defaults={"policy": {"repo.read": "confirm"}}, limits=Limits({"time": 90}))
				prompt = "Use the question tool now to ask me to choose between AUTO_RESUME_OK and NO. " \
					"Wait for my answer. Then reply with exactly the chosen value, without explanation. " \
					"Do not call any other tools or read/modify files."
				if action in {"approve", "reject"}:
					(root / "fixture.txt").write_text("AUTO_READ_OK")
					prompt = "Use the read tool exactly once to read " + str(root / "fixture.txt") + ". " \
						"If permission is granted, reply with exactly the file content. If permission is denied, " \
						"reply with exactly DENIED and do not retry. Do not call any other tools or modify files."
				PlanRevise(context, plan, [{"op": "add", "task": {"id": "question", "title": "Live question",
					"pre": "printf x >> " + str(root / "pre-count"), "output": {"format": "text"},
					"sections": {"Process": prompt}}}], "Add live control fixture")
				run = TaskStart(context, "question")
				session = run.get("runtime", {}).get("session")
				try:
					self.assertEqual(run["status"], "Waiting", run.get("failure", run))
					self.assertTrue(session)
					if action in {"answer", "cancel"}:
						self.assertEqual(run["question"]["type"], "ScriptQuestion")
						field = run["question"]["fields"][0]
						key = field.get("key", field.get("id"))
						option = next(item for item in field["options"]
							if (item.get("label") if isinstance(item, dict) else item) == "AUTO_RESUME_OK")
						answer = {key: option.get("value", option.get("label")) if isinstance(option, dict) else option}
					else:
						self.assertEqual(run["question"]["type"], "PolicyRequest")
						self.assertEqual(run["question"]["effect"], "repo.read")
						answer = action == "approve"
					code = '''import json, sys
from pathlib import Path
from auto.features.task.operations import RunAnswer, RunCancel, RunGet
from auto.integration.memo.store import MemoKnowledge
from auto.runtime.model import Context
root = Path(sys.argv[1])
context = Context(project=root, plan=root / ".auto/live", config={"engine": "opencode"}, knowledge=MemoKnowledge(root),
    defaults={"policy": {"repo.read": "confirm"}})
if sys.argv[3] != "cancel":
    RunAnswer(context, sys.argv[2], json.loads(sys.argv[4]), "Live fixture answer")
else:
    RunCancel(context, sys.argv[2])
print(json.dumps(RunGet(context, sys.argv[2])))
'''
					result = subprocess.run([sys.executable, "-c", code, directory, run["id"], action, json.dumps(answer)],
						text=True, capture_output=True, timeout=100)
					self.assertEqual(result.returncode, 0, result.stderr)
					completed = RunGet(context, run)
					# A denial carries feedback, so the model continues its turn rather than being interrupted.
					expectedStatus = "Cancelled" if action == "cancel" else "Succeeded"
					if completed["status"] != expectedStatus:
						probe = context.derive(limits=Limits({"time": 10}))
						messages = OpenCode().api(probe, "get", "/api/session/" + session + "/message?order=asc&limit=100")["data"]
						self.fail(json.dumps({"failure": completed.get("failure"), "messages": [
							{key: message[key] for key in ("type", "error", "outcome") if key in message} for message in messages]}))
					self.assertEqual(completed["runtime"]["session"], session)
					self.assertEqual((root / "pre-count").read_text(), "x")
					if action in {"answer", "approve", "reject"}:
						expected = {"answer": "AUTO_RESUME_OK", "approve": "AUTO_READ_OK", "reject": "DENIED"}[action]
						self.assertEqual(RunOutput(context, run).strip(), expected)
					else:
						self.assertEqual(completed["failure"]["kind"], "cancelled")
					probe = context.derive(limits=Limits({"time": 10}))
					self.assertEqual(OpenCode().api(probe, "get", "/api/session/" + session + "/form")["data"], [])
					self.assertEqual(OpenCode().api(probe, "get", "/api/session/" + session + "/permission")["data"], [])
				finally:
					if session:
						subprocess.run(["opencode", "api", "delete", "/api/session/" + session],
							text=True, capture_output=True, timeout=15, check=True)

	def testOpenCodeModelQuestionsResumeOrCancelAcrossProcesses(self):
		self.modelControls(("answer", "cancel"))

	def testOpenCodeModelPermissionsApproveOrRejectAcrossProcesses(self):
		self.modelControls(("approve", "reject"))


# EOF
