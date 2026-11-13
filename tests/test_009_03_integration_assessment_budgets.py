"""Reassessment reservations, measured usage and interrupted publication."""

import selectors
import subprocess
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from auto.features.plan.gaps import PlanRepair
from auto.features.plan.operations import PlanRevise, PlanStatus
from auto.features.task.operations import RunCheck, RunGet, TaskStart
from auto.utils.data import AutoError, readJson
import test_009_02_integration_continuation as fixtures


class AssessmentBudgetTests(unittest.TestCase):
	setUp = fixtures.ContinuationTests.setUp
	add = fixtures.ContinuationTests.add

	def completed(self):
		PlanRevise(self.context, self.plan, [{"op": "update", "fields": {"budget": {"tokens": 10}}}], "Shared budget")
		class Producer:
			def start(inner, context, request):
				context.limits.usage["tokens"] += 2
				yield {"type": "ScriptEnded", "status": "Succeeded", "output": "Produced"}
		self.context.engines["fake"] = Producer()
		for name in ("one", "two"):
			self.add(name, budget={"tokens": 8})
		return [TaskStart(self.context, name) for name in ("one", "two")]

	def testAssessmentsReserveCapacityAndPublishUsageWithoutChangingReceipts(self):
		one, two = self.completed()
		path = self.plan / "runs" / one["id"]
		original = (path / "receipt.json").read_bytes()
		ready, release, calls = threading.Event(), threading.Event(), []
		class Checker:
			def start(inner, context, request):
				calls.append(dict(context.limits.budget))
				context.limits.usage["tokens"] += 3
				yield {"type": "ScriptMeta", "name": "usage", "value": dict(context.limits.usage)}
				ready.set()
				if not release.wait(timeout=10):
					raise RuntimeError("Check was not released")
				yield {"type": "ScriptEnded", "status": "Succeeded", "output": {"pass": True, "reason": "Reviewed"}}
		self.context.engines["fake"] = Checker()
		with ThreadPoolExecutor(max_workers=1) as pool:
			first = pool.submit(RunCheck, self.context, one, {"content": "Review"})
			try:
				self.assertTrue(ready.wait(timeout=10))
				self.assertEqual(RunGet(self.context, one)["usage"]["tokens"], 5)
				self.assertEqual(PlanStatus(self.context)["usage"]["tokens"], 7)
				with self.assertRaises(AutoError) as denied:
					RunCheck(self.context, two, {"content": "Review"})
				self.assertEqual(denied.exception.kind, "budget")
				self.assertEqual(len(calls), 1)
				self.add("manual", budget={"tokens": 1})
				with self.assertRaisesRegex(AutoError, "Plan tokens budget cannot cover"):
					TaskStart(self.context, "manual")
				PlanRepair(self.context)
				self.assertTrue((path / "assessment.json").exists())
			finally:
				release.set()
			self.assertTrue(first.result(timeout=10)["pass"])
		self.assertTrue(RunCheck(self.context, two, {"content": "Review"})["pass"])
		self.assertEqual(PlanStatus(self.context)["usage"]["tokens"], 10)
		self.assertEqual((path / "receipt.json").read_bytes(), original)
		self.assertFalse((path / "assessment.json").exists())
		with self.assertRaises(AutoError) as exhausted:
			RunCheck(self.context, one, {"content": "Review"})
		self.assertEqual(exhausted.exception.kind, "budget")
		self.assertEqual(len(calls), 2)

	def testKilledAssessmentChargesKnownUsageOnceAndReleasesItsReservation(self):
		one, two = self.completed()
		path = self.plan / "runs" / one["id"]
		original = (path / "receipt.json").read_bytes()
		file = self.root / "assessment-worker.py"
		file.write_text('''import sys
from pathlib import Path
from auto.features.task.operations import RunCheck
from auto.runtime.model import Context
class Store:
    def list(self, plan, kind):
        return []
class Checker:
    def start(self, context, request):
        context.limits.usage["tokens"] += 3
        yield {"type": "ScriptMeta", "name": "usage", "value": dict(context.limits.usage)}
        print("ready", flush=True)
        sys.stdin.readline()
        yield {"type": "ScriptEnded", "status": "Succeeded", "output": {"pass": True, "reason": "Reviewed"}}
root = Path(sys.argv[1])
context = Context(project=root, plan=root / ".auto/plans/demo", knowledge=Store(), engines={"fake": Checker()}, config={"engine": "fake"})
RunCheck(context, sys.argv[2], {"content": "Review"})
''')
		worker = subprocess.Popen([sys.executable, str(file), str(self.root), one["id"]],
			stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
		try:
			with selectors.DefaultSelector() as selector:
				selector.register(worker.stdout, selectors.EVENT_READ)
				self.assertTrue(selector.select(timeout=10), "Assessment did not start")
			self.assertEqual(worker.stdout.readline().strip(), "ready")
			PlanRepair(self.context)
			self.assertTrue((path / "assessment.json").exists())
			self.assertEqual(PlanStatus(self.context)["usage"]["tokens"], 7)
			second = subprocess.run([sys.executable, str(file), str(self.root), two["id"]],
				input="done\n", capture_output=True, text=True, timeout=10)
			self.assertNotEqual(second.returncode, 0)
			self.assertEqual(second.stdout, "")
			self.assertIn("budget exhausted before engine execution", second.stderr)
			self.add("manual", budget={"tokens": 3})
			with self.assertRaisesRegex(AutoError, "Plan tokens budget cannot cover"):
				TaskStart(self.context, "manual")
			worker.kill()
			worker.communicate(timeout=10)
			self.assertLess(worker.returncode, 0)
		finally:
			if worker.poll() is None:
				worker.kill()
			worker.communicate(timeout=10)
		PlanRepair(self.context)
		self.assertFalse((path / "assessment.json").exists())
		self.assertEqual((path / "receipt.json").read_bytes(), original)
		self.assertEqual(RunGet(self.context, one)["usage"]["tokens"], 5)
		self.assertEqual(PlanStatus(self.context)["usage"]["tokens"], 7)
		journal = (path / "journal.jsonl").read_bytes()
		self.assertIn(b'"interrupted":true', journal.replace(b" ", b""))
		PlanRepair(self.context)
		self.assertEqual((path / "journal.jsonl").read_bytes(), journal)
		self.assertEqual(TaskStart(self.context, "manual")["status"], "Succeeded")

	def testCompletedChargeSurvivesInterruptedCheckpointDeletionWithoutDuplication(self):
		one, _ = self.completed()
		path = self.plan / "runs" / one["id"]
		original = (path / "receipt.json").read_bytes()
		class Checker:
			def start(inner, context, request):
				context.limits.usage["tokens"] += 3
				yield {"type": "ScriptEnded", "status": "Succeeded", "output": {"pass": True, "reason": "Reviewed"}}
		self.context.engines["fake"] = Checker()
		unlink = Path.unlink
		def interrupted(file, *args, **kwargs):
			if file == path / "assessment.json":
				raise OSError("Interrupted checkpoint deletion")
			return unlink(file, *args, **kwargs)
		with patch.object(Path, "unlink", interrupted):
			with self.assertRaisesRegex(OSError, "Interrupted checkpoint deletion"):
				RunCheck(self.context, one, {"content": "Review"})
		self.assertTrue(readJson(path / "assessment.json"))
		self.assertEqual(RunGet(self.context, one)["usage"]["tokens"], 5)
		journal = (path / "journal.jsonl").read_bytes()
		PlanRepair(self.context)
		self.assertFalse((path / "assessment.json").exists())
		self.assertEqual((path / "journal.jsonl").read_bytes(), journal)
		self.assertEqual(RunGet(self.context, one)["usage"]["tokens"], 5)
		self.assertEqual((path / "receipt.json").read_bytes(), original)
