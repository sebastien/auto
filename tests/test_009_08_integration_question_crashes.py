"""Question checkpoints survive worker death without replaying completed phases."""

from __future__ import annotations

import importlib.util
import selectors
import subprocess
import sys
import unittest

from auto.features.orchestration.operations import PlanContinue
from auto.features.plan.gaps import GapList, PlanRepair
from auto.features.task.operations import RunAnswer, RunGet, RunList
from auto.integration.memo.store import MemoKnowledge
from auto.utils.data import readJson
import test_004_01_integration_planning as fixtures


@unittest.skipUnless(importlib.util.find_spec("memo"), "Install Memo to verify durable question recovery")
class QuestionCrashTests(unittest.TestCase):
	def setUp(self):
		fixtures.PlanningTests.setUp(self)
		self.ctx = self.ctx.derive(knowledge=MemoKnowledge(self.root))

	add = fixtures.PlanningTests.add

	def worker(self, point):
		self.add("work", pre="printf x >> " + str(self.root / "pre-effects"),
			apply="printf x >> " + str(self.root / "apply-effects"))
		file = self.root / "question-worker.py"
		file.write_text('''import sys
from pathlib import Path
from unittest.mock import patch
from auto.features.task import operations
from auto.integration.memo.store import MemoKnowledge
from auto.runtime.model import Context
root, point = Path(sys.argv[1]), sys.argv[2]
def pause():
    print("ready", flush=True)
    sys.stdin.readline()
class Engine:
    def start(self, context, request):
        with (root / "model-calls").open("a") as stream:
            stream.write("x")
        if "answer" not in request:
            request["session"] = "durable-session"
            context.limits.usage["tokens"] += 2
            yield {"type": "ScriptMeta", "name": "session", "value": request["session"]}
            yield {"type": "ScriptQuestion", "content": "Continue?"}
        else:
            assert request["session"] == "durable-session"
            context.limits.usage["tokens"] += 3
            yield {"type": "ScriptMeta", "name": "usage", "value": dict(context.limits.usage)}
            pause()
            yield {"type": "ScriptEnded", "status": "Succeeded", "output": "Done"}
context = Context(project=root, plan=root / ".auto/plans/demo", knowledge=MemoKnowledge(root),
    engines={"fake": Engine()}, config={"engine": "fake"})
original = operations.saveRun
def save(context, path, run, state=None):
    original(context, path, run, state)
    if point == "waiting" and run["status"] == "Waiting":
        pause()
with patch.object(operations, "saveRun", side_effect=save):
    run = operations.TaskStart(context, "work")
    if point == "resuming":
        operations.RunAnswer(context, run, "yes", "Resume the question")
''')
		worker = subprocess.Popen([sys.executable, str(file), str(self.root), point], stdin=subprocess.PIPE,
			stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
		def cleanup():
			if worker.poll() is None:
				worker.kill()
			worker.communicate(timeout=15)
		self.addCleanup(cleanup)
		with selectors.DefaultSelector() as selector:
			selector.register(worker.stdout, selectors.EVENT_READ)
			self.assertTrue(selector.select(timeout=15), "Worker did not reach question checkpoint")
		line = worker.stdout.readline().strip()
		if line != "ready":
			stdout, stderr = worker.communicate(timeout=15)
			self.fail(f"Worker stopped before checkpoint: {line!r} {stdout} {stderr}")
		return worker

	def testKilledWaitingWorkerCanResumeWithoutRepeatingPreOrModelStart(self):
		worker = self.worker("waiting")
		run = RunList(self.ctx)[0]
		self.assertEqual(run["status"], "Waiting")
		self.assertEqual(run["usage"]["tokens"], 2)
		PlanRepair(self.ctx)
		self.assertEqual(RunGet(self.ctx, run)["status"], "Waiting")
		worker.kill()
		worker.communicate(timeout=15)
		PlanRepair(self.ctx)
		self.assertEqual(RunGet(self.ctx, run)["status"], "Waiting")
		requests = []
		class Resumed:
			def start(inner, context, request):
				requests.append(dict(request))
				context.limits.usage["tokens"] += 3
				yield {"type": "ScriptEnded", "status": "Succeeded", "output": "Done"}
		self.ctx.engines["fake"] = Resumed()
		RunAnswer(self.ctx, run, "yes", "Resume retained question")
		completed = RunGet(self.ctx, run)
		self.assertEqual(completed["status"], "Succeeded", completed)
		self.assertEqual(completed["usage"]["tokens"], 5)
		self.assertEqual(len(requests), 1)
		self.assertEqual(requests[0]["session"], "durable-session")
		self.assertEqual(requests[0]["answer"], "yes")
		self.assertEqual((self.root / "pre-effects").read_text(), "x")
		self.assertEqual((self.root / "apply-effects").read_text(), "x")
		self.assertEqual((self.root / "model-calls").read_text(), "x")
		self.assertEqual(len(RunList(self.ctx)), 1)
		self.assertEqual(GapList(self.ctx)[0]["status"], "Resolved")
		path = self.plan / "runs" / run["id"] / "receipt.json"
		receipt = path.read_bytes()
		PlanRepair(self.ctx)
		self.assertEqual(path.read_bytes(), receipt)

	def testKilledResumingWorkerRetainsUsageAndDecisionWithoutReplayingEffects(self):
		worker = self.worker("resuming")
		run = RunList(self.ctx)[0]
		self.assertEqual(run["status"], "Running")
		self.assertEqual(run["usage"]["tokens"], 5)
		PlanRepair(self.ctx)
		self.assertEqual(RunGet(self.ctx, run)["status"], "Running")
		worker.kill()
		worker.communicate(timeout=15)
		PlanRepair(self.ctx)
		repaired = RunGet(self.ctx, run)
		self.assertEqual(repaired["status"], "Failed")
		self.assertEqual(repaired["failure"]["kind"], "system")
		self.assertEqual(repaired["usage"]["tokens"], 5)
		self.assertEqual(GapList(self.ctx)[0]["status"], "Resolved")
		self.assertEqual((self.root / "pre-effects").read_text(), "x")
		self.assertFalse((self.root / "apply-effects").exists())
		self.assertEqual((self.root / "model-calls").read_text(), "xx")
		path = self.plan / "runs" / run["id"]
		receipt = (path / "receipt.json").read_bytes()
		self.assertIn("Pre", readJson(path / "state.json")["completed"])
		self.assertEqual(list(PlanContinue(self.ctx, self.plan))[-1]["data"]["stop"], "failed")
		PlanRepair(self.ctx)
		self.assertEqual((path / "receipt.json").read_bytes(), receipt)
		self.assertEqual(len(RunList(self.ctx)), 1)
		self.assertEqual(self.engine.calls, [])


# EOF
