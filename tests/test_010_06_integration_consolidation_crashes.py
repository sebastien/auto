"""Real-process Memoriser interruption, publication replay and cancellation."""

from __future__ import annotations

import importlib.util
import selectors
import subprocess
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from unittest.mock import patch

from auto.features.memory.consolidation import AgentRunScope, ConsolidationStart, ConsolidationStatus, agentConfig, runPending
from auto.integration.memo.consolidation import MemoConsolidation
from auto.utils.data import readJson
from auto.utils.layout import planPaths
import test_010_03_integration_consolidation as fixtures


@unittest.skipUnless(importlib.util.find_spec("memo"), "Install Memo to verify retained consolidation")
class ConsolidationCrashTests(unittest.TestCase):
	setUp = fixtures.ConsolidationTests.setUp

	def pending(self, context=None):
		context = context or self.context
		name, config = agentConfig(context, "ux")
		return runPending(context, name, config)

	def worker(self, point):
		file = self.root / "consolidation-worker.py"
		file.write_text('''import json
import sys
from pathlib import Path
from unittest.mock import patch
from auto.features.memory import consolidation as operations
from auto.integration.memo.consolidation import MemoConsolidation
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
        context.limits.usage["tokens"] = 4
        yield {"type": "ScriptMeta", "name": "usage", "value": {"tokens": 4}}
        if point == "model":
            pause()
        inputs = context.inputs
        item = next(item["id"] for phase in inputs["due"]["phases"] for item in phase["items"] if "id" in item)
        value = {"id": inputs["consolidation"]["id"], "agent": inputs["agent"]["id"],
            "snapshot": inputs["due"]["log_revision"], "changes": [{"entry": "agent-ux/log:Observations",
            "append": [{"key": inputs["consolidation"]["id"] + "#1", "text": "A sourced correction",
            "sources": ["agent-ux/log:Observations item=" + item], "meta": {"type": "correction"}}]}],
            "report": {"summary": "Reviewed", "acknowledged": [item]}}
        yield {"type": "ScriptEnded", "status": "Succeeded", "output": json.dumps(value)}
context = Context(project=root, knowledge=MemoKnowledge(root), engines={"fake": Engine()},
    config={"engine": "fake"}, defaults={"memory": {"agents": {"ux": {}}}})
write, apply, finish, save = operations.atomicWrite, MemoConsolidation.apply, operations.AgentRunScope.finish, operations.AgentRunScope.save
def atomicWrite(path, text):
    value = write(path, text)
    if point == "staged" and path.name == "staged.json" and not json.loads(text).get("attempted"):
        pause()
    return value
def publish(self, value):
    result = apply(self, value)
    if point == "published":
        pause()
    return result
def finishRun(self, *args):
    if point == "before-stage":
        pause()
    return finish(self, *args)
def saveRun(self, context, path, run, state=None):
    save(self, context, path, run, state)
    if point == "receipt" and run["status"] == "Succeeded":
        pause()
with patch.object(operations, "atomicWrite", side_effect=atomicWrite), patch.object(MemoConsolidation, "apply", publish), \\
    patch.object(operations.AgentRunScope, "finish", finishRun), patch.object(operations.AgentRunScope, "save", saveRun):
    print(json.dumps(operations.ConsolidationStart(context, "ux")), flush=True)
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
			self.assertTrue(selector.select(timeout=15), "Worker did not reach consolidation boundary")
		line = worker.stdout.readline().strip()
		if line != "ready":
			stdout, stderr = worker.communicate(timeout=15)
			self.fail(f"Worker stopped before boundary: {line!r} {stdout} {stderr}")
		return worker

	def interrupt(self, point):
		worker = self.worker(point)
		root = self.root / ".auto/agents/ux"
		run = ConsolidationStatus(self.context, "ux")["latest"]
		path = root / "runs" / run["id"]
		self.assertEqual(run["usage"]["tokens"], 4)
		self.assertEqual(self.pending()["status"], "Pending")
		self.assertIsNone(worker.poll())
		self.assertEqual(self.calls, [])
		original = (path / "receipt.json").read_bytes() if point == "receipt" else None
		worker.kill()
		worker.communicate(timeout=15)
		self.assertLess(worker.returncode, 0)
		self.assertEqual(self.pending()["status"], "Idle")
		receipt = readJson(path / "receipt.json")
		self.assertEqual(receipt["status"], "Cancelled" if point in {"model", "before-stage"} else "Succeeded")
		self.assertEqual(receipt["usage"]["tokens"], 4)
		if original is not None:
			self.assertEqual((path / "receipt.json").read_bytes(), original)
		original = (path / "receipt.json").read_bytes()
		for _ in range(2):
			self.assertEqual(self.pending()["status"], "Idle")
		self.assertEqual((path / "receipt.json").read_bytes(), original)
		self.assertEqual((self.root / "model-calls").read_text(), "x")
		self.assertEqual(self.calls, [])
		self.memo.backend.invalidate()
		items = self.memo.backend.get("agent-ux/log:Observations").log.items
		applied = point not in {"model", "before-stage"}
		self.assertEqual(sum(item.text == "A sourced correction" for item in items), int(applied))
		self.assertEqual(sum(item.meta.get("consolidation", {}).get("id") == run["id"] for item in items), int(applied))
		if applied:
			self.assertTrue(readJson(path / "application.json")["complete"])

	def testKilledModelDoesNotInventAnApplication(self):
		self.interrupt("model")

	def testKilledBeforeStagingDoesNotApplyUnstagedOutput(self):
		self.interrupt("before-stage")

	def testKilledAfterStagingReplaysWithoutAnotherModel(self):
		self.interrupt("staged")

	def testKilledAfterMemoPublicationDoesNotDuplicateChangesOrReport(self):
		self.interrupt("published")

	def testKilledAfterReceiptPreservesTerminalBytes(self):
		self.interrupt("receipt")

	def testCancellationAfterChecksStopsBeforeStagingAndPublication(self):
		cancelled = False
		check = AgentRunScope.check
		def cancel(scope, *args):
			nonlocal cancelled
			check(scope, *args)
			cancelled = True
		with patch.object(AgentRunScope, "check", cancel):
			run = ConsolidationStart(self.context.derive(cancelled=lambda: cancelled), "ux")
		self.assertEqual(run["status"], "Cancelled", run)
		self.assertEqual(run["failure"]["kind"], "cancelled")
		path = self.root / ".auto/agents/ux/runs" / run["id"]
		self.assertFalse((path / "staged.json").exists())
		self.memo.backend.invalidate()
		items = self.memo.backend.get("agent-ux/log:Observations").log.items
		self.assertFalse(any(item.meta.get("consolidation", {}).get("id") == run["id"] for item in items))

	def testIndependentMemoMaintenanceLockSerializesPublication(self):
		holder = subprocess.Popen([sys.executable, "-c", '''import sys
from memo.integrations.filesystem.backend import LocalBackend
from memo.features.agent.consolidation.records import RECORD_SCOPE
with LocalBackend(sys.argv[1]).lock(RECORD_SCOPE, "urn:test:ux"):
    print("ready", flush=True)
    sys.stdin.readline()
''', str(self.root / ".memo")], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
		def cleanup():
			if holder.poll() is None:
				holder.kill()
			holder.communicate(timeout=15)
		self.addCleanup(cleanup)
		with selectors.DefaultSelector() as selector:
			selector.register(holder.stdout, selectors.EVENT_READ)
			self.assertTrue(selector.select(timeout=15), "Memo lock holder did not start")
		self.assertEqual(holder.stdout.readline().strip(), "ready")
		entered = threading.Event()
		apply = MemoConsolidation.apply
		def publish(session, value):
			entered.set()
			return apply(session, value)
		with patch.object(MemoConsolidation, "apply", publish), ThreadPoolExecutor(max_workers=1) as pool:
			result = pool.submit(ConsolidationStart, self.context, "ux")
			try:
				self.assertTrue(entered.wait(timeout=15))
				with self.assertRaises(TimeoutError):
					result.result(timeout=.1)
				self.assertEqual(len(self.calls), 1)
				self.assertEqual(self.pending()["status"], "Pending")
			finally:
				holder.kill()
				holder.communicate(timeout=15)
			run = result.result(timeout=15)
		self.assertEqual(run["status"], "Succeeded", run)
		self.assertTrue(run["application"]["complete"])
		self.assertEqual(self.pending()["status"], "Idle")
		self.memo.backend.invalidate()
		items = self.memo.backend.get("agent-ux/log:Observations").log.items
		self.assertEqual(sum(item.meta.get("consolidation", {}).get("id") == run["id"] for item in items), 1)

	def testPhaseSelectionKeepsReadOnlyEnginePolicyAndNeverOpensPlanGaps(self):
		from auto.features.memory.consolidation import PHASES
		outer = self
		answers, selected = [], []
		class Engine:
			def start(inner, context, request):
				selected.append(context.inputs["phases"])
				for effect in ("repo.read", "net.read", "repo.write", "shell.run", "memory.evolve"):
					answers.append((effect, (yield {"type": "PolicyRequest", "effect": effect, "action": "Try " + effect})))
				yield {"type": "ScriptEnded", "status": "Succeeded", "output": outer.build(context.inputs)}
		context = self.context.derive(engines={"fake": Engine()}, policyLayers=[("test", {"repo.read": "allow", "net.read": "deny"})])
		for phase in PHASES:
			with self.subTest(phase=phase):
				run = ConsolidationStart(context, "ux", [phase])
				self.assertEqual(run["status"], "Succeeded", run)
				self.assertEqual(selected[-1], [phase])
				self.assertEqual(answers[-5:], [("repo.read", True), ("net.read", False),
					("repo.write", False), ("shell.run", False), ("memory.evolve", False)])
		self.assertEqual(planPaths(self.root), [])


# EOF
