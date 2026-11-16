"""Module: test_pi_worker — Warm Pi workers reset history and die on interruption."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from auto.integration.pi.worker import WORKERS, closeWorkers, streamWorker
from auto.runtime.model import Context, Limits
from auto.utils.data import AutoError

FAKE = """#!/usr/bin/env python3
import json, os, sys, time
with open("starts", "a") as f:
 f.write(str(os.getpid()) + "\\n")
history = []
for line in sys.stdin:
 command = json.loads(line)
 if command["type"] == "new_session":
  history = []
  print(json.dumps({"id":command["id"],"type":"response","success":True}), flush=True)
 else:
  history.append(command["message"])
  print(json.dumps({"id":command["id"],"type":"response","success":True}), flush=True)
  if command["message"] == "malformed":
   print("not json", flush=True)
  elif command["message"] == "exit":
   sys.exit(0)
  elif command["message"] == "hang":
   time.sleep(30)
  else:
   print(json.dumps({"type":"message_end","history":history}), flush=True)
   print(json.dumps({"type":"agent_end"}), flush=True)
   print(json.dumps({"type":"agent_settled"}), flush=True)
"""


class PiWorkerTests(unittest.TestCase):
	def setUp(self):
		closeWorkers()
		self.addCleanup(closeWorkers)
		temp = tempfile.TemporaryDirectory()
		self.addCleanup(temp.cleanup)
		self.root = Path(temp.name)
		self.binary = self.root / "pi"
		self.binary.write_text(FAKE)
		self.binary.chmod(0o755)
		self.context = Context(project=self.root)

	def runPrompt(self, prompt, context=None, config=None):
		return list(streamWorker((context or self.context).process, [str(self.binary)], config or {}, prompt))

	def testReusesProcessButNotConversation(self):
		for prompt in ("first", "second"):
			result = self.runPrompt(prompt)
			events = [json.loads(chunk) for number, chunk in result if number == 0]
			self.assertEqual(events[0]["history"], [prompt], "new_session cleared the last task's messages")
			self.assertEqual([event["type"] for event in events], ["message_end", "agent_end", "agent_settled"])
			self.assertEqual(result[-1], (2, 0), "the worker is ready only after agent_settled")
		self.assertEqual(len((self.root / "starts").read_text().splitlines()), 1)

	def testChangesToEnvironmentGetSeparateWorkersAndPoolIsBounded(self):
		for value in ("a", "b", "c"):
			self.runPrompt(value, config={"env": {"AUTO_WORKER_TEST": value}})
		self.assertEqual(len(WORKERS), 2)
		self.assertEqual(len((self.root / "starts").read_text().splitlines()), 3)

	def testBadOutputAndUnexpectedExitEvictWorkers(self):
		for prompt in ("malformed", "exit"):
			with self.assertRaises(AutoError):
				self.runPrompt(prompt)
			self.assertFalse(WORKERS)
		self.runPrompt("works")
		self.assertEqual(len((self.root / "starts").read_text().splitlines()), 3)

	def testClosingStreamKillsChildRatherThanReusingPartialConversation(self):
		stream = streamWorker(self.context.process, [str(self.binary)], {}, "work")
		next(stream)
		stream.close()
		self.assertFalse(WORKERS)
		pid = int((self.root / "starts").read_text().strip())
		with self.assertRaises(ProcessLookupError):
			os.kill(pid, 0)

	def testTimeoutAndByteLimitEvictWorkers(self):
		with self.assertRaises(AutoError) as failure:
			self.runPrompt("hang", context=self.context.derive(limits=Limits({"time": .2})))
		self.assertEqual(failure.exception.kind, "timeout")
		self.assertFalse(WORKERS)
		with self.assertRaisesRegex(AutoError, "byte limit"):
			self.runPrompt("work", context=self.context.derive(maxBytes=1))
		self.assertFalse(WORKERS)


# EOF
