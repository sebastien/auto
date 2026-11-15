"""Apply and output-gate publication remain safe when lifecycle workers die."""

import importlib.util
import selectors
import subprocess
import sys
import unittest

from auto.features.orchestration.operations import PlanContinue
from auto.features.plan.gaps import GapDecide, GapList, PlanRepair
from auto.features.plan.store import journal
from auto.features.task.operations import RunAnswer, RunGet, RunList, TaskStart
from auto.integration.memo.store import MemoKnowledge
from auto.utils.data import readJson
import test_004_01_integration_planning as fixtures


@unittest.skipUnless(importlib.util.find_spec("memo"), "Install Memo to verify durable output gates")
class LifecycleCrashTests(unittest.TestCase):
	def setUp(self):
		fixtures.PlanningTests.setUp(self)
		self.ctx = self.ctx.derive(knowledge=MemoKnowledge(self.root))

	add = fixtures.PlanningTests.add

	def interrupt(self, point):
		counter = self.root / "apply-effects"
		gated = point.startswith("gate-")
		self.add("work", apply="printf x >> " + str(counter), **({"hints": {"gate": "approval"}} if gated else {}))
		self.add("consumer", process="Consume {{ upstream }}", inputs={"upstream": "task:work"})
		baseline = len(journal(self.plan))
		file = self.root / "lifecycle-worker.py"
		file.write_text('''import sys
from pathlib import Path
from unittest.mock import patch
from auto.features.task import operations
from auto.features.plan import store
from auto.integration.memo.store import MemoKnowledge
from auto.runtime.model import Context
root, point = Path(sys.argv[1]), sys.argv[2]
class Engine:
    def start(self, context, request):
        with (root / "model-calls").open("a") as stream:
            stream.write("x")
        context.limits.usage["tokens"] = 4
        yield {"type": "ScriptMeta", "name": "usage", "value": {"tokens": 4}}
        yield {"type": "ScriptEnded", "status": "Succeeded", "output": "Produced"}
context = Context(project=root, plan=root / ".auto/demo", knowledge=MemoKnowledge(root),
    engines={"fake": Engine()}, config={"engine": "fake"})
def pause():
    print("ready", flush=True)
    sys.stdin.readline()
apply, save, gap, append = operations.applyEffects, operations.saveRun, operations.GapRaise, store.append
def effects(*args, **kwargs):
    if point == "apply-before":
        pause()
    value = apply(*args, **kwargs)
    if point == "apply-after":
        pause()
    return value
def saveRun(context, path, run, state=None):
    save(context, path, run, state)
    if point == "apply-checkpoint" and run["status"] == "Running" and state and state.get("phase") == "Apply" and "Apply" in state["completed"]:
        pause()
    if point == "gate-receipt" and run["status"] == "Succeeded":
        pause()
def raiseGap(*args, **kwargs):
    if point == "gate-before":
        pause()
    value = gap(*args, **kwargs)
    if point == "gate-after":
        pause()
    return value
def publish(*args, **kwargs):
    if point == "gate-staged":
        pause()
    return append(*args, **kwargs)
with patch.object(operations, "applyEffects", side_effect=effects), patch.object(operations, "saveRun", side_effect=saveRun), \\
    patch.object(operations, "GapRaise", side_effect=raiseGap), patch.object(store, "append", side_effect=publish):
    operations.TaskStart(context, "work")
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
			self.assertTrue(selector.select(timeout=15), "Worker did not reach the lifecycle boundary")
		line = worker.stdout.readline().strip()
		if line != "ready":
			stdout, stderr = worker.communicate(timeout=15)
			self.fail(f"Worker stopped before the boundary: {line!r} {stdout} {stderr}")
		self.assertIsNone(worker.poll())
		run = RunList(self.ctx, {"task": "work"})[0]
		self.assertEqual(run["usage"]["tokens"], 4)
		self.assertEqual(run["status"], "Succeeded" if point == "gate-receipt" else "Running")
		path = self.plan / "runs" / run["id"]
		self.assertEqual((path / "output.txt").read_text(), "Produced")
		self.assertEqual((self.root / "model-calls").read_text(), "x")
		if point == "apply-before":
			self.assertFalse(counter.exists())
		else:
			self.assertEqual(counter.read_text(), "x")
		if point == "apply-checkpoint":
			self.assertIn("Apply", readJson(path / "state.json")["completed"])
		if point != "gate-staged":
			PlanRepair(self.ctx)
			self.assertEqual(RunGet(self.ctx, run)["status"], run["status"])
		worker.kill()
		worker.communicate(timeout=15)
		PlanRepair(self.ctx)
		repaired = RunGet(self.ctx, run)
		self.assertEqual(repaired["status"], "Succeeded" if point == "gate-receipt" else "Failed")
		self.assertEqual(repaired["usage"]["tokens"], 4)
		if repaired["status"] == "Failed":
			self.assertEqual(repaired["failure"]["kind"], "system")
		gapExpected = point in {"gate-staged", "gate-after", "gate-receipt"}
		gaps = GapList(self.ctx)
		self.assertEqual(len(gaps), int(gapExpected))
		self.assertEqual(len(journal(self.plan)), baseline + int(gapExpected))
		if gaps:
			self.assertEqual(gaps[0]["status"], "Open")
			self.assertEqual(gaps[0]["raised_by"], run["id"])
		receipt = (path / "receipt.json").read_bytes()
		self.assertEqual(TaskStart(self.ctx, "consumer")["status"], "Blocked")
		events = list(PlanContinue(self.ctx, self.plan))
		self.assertEqual(events[-1]["data"]["stop"], "waiting" if point == "gate-receipt" else "failed")
		PlanRepair(self.ctx)
		PlanRepair(self.ctx)
		self.assertEqual((path / "receipt.json").read_bytes(), receipt)
		self.assertEqual((self.root / "model-calls").read_text(), "x")
		self.assertEqual(self.engine.calls, [])
		self.assertEqual(counter.read_text() if counter.exists() else "", "" if point == "apply-before" else "x")
		self.assertEqual(len(GapList(self.ctx)), int(gapExpected))
		if point == "gate-receipt":
			GapDecide(self.ctx, gaps[0]["id"], True, "Approve durable output")
			self.assertEqual(TaskStart(self.ctx, "consumer")["status"], "Succeeded")
			self.assertIn("Consume Produced", self.engine.calls[-1])
			self.assertEqual(counter.read_text(), "x")
			self.assertEqual((path / "receipt.json").read_bytes(), receipt)

	def testKillBeforeApplyEffects(self):
		self.interrupt("apply-before")

	def testKillAfterApplyEffectsBeforeCheckpoint(self):
		self.interrupt("apply-after")

	def testKillAfterApplyCheckpoint(self):
		self.interrupt("apply-checkpoint")

	def testKillBeforeOutputGateCreation(self):
		self.interrupt("gate-before")

	def testKillAfterGateStagingBeforeJournal(self):
		self.interrupt("gate-staged")

	def testKillAfterOutputGateCreationBeforeReceipt(self):
		self.interrupt("gate-after")

	def testKillAfterSuccessfulReceiptPublication(self):
		self.interrupt("gate-receipt")

	def testEvaluationQuestionResumeDoesNotRepeatApply(self):
		counter = self.root / "apply-effects"
		self.add("work", apply="printf x >> " + str(counter), sections={"Process": "Produce", "Evaluation": "Evaluate"})
		class QuestionEngine:
			def start(inner, context, request):
				if request["output"]["format"] != "json":
					yield {"type": "ScriptEnded", "status": "Succeeded", "output": "Produced"}
				elif "session" not in request:
					request["session"] = "evaluation-question"
					yield {"type": "ScriptQuestion", "content": "What score?"}
				else:
					yield {"type": "ScriptEnded", "status": "Succeeded", "output": {"score": 1, "feedback": "Accepted"}}
		self.ctx.engines["fake"] = QuestionEngine()
		run = TaskStart(self.ctx, "work")
		self.assertEqual(run["status"], "Waiting", run)
		self.assertEqual(counter.read_text(), "x")
		RunAnswer(self.ctx, run, "1", "Accepted output")
		self.assertEqual(RunGet(self.ctx, run)["status"], "Succeeded")
		self.assertEqual(RunGet(self.ctx, run)["evaluation"]["score"], 1)
		self.assertEqual(counter.read_text(), "x")
