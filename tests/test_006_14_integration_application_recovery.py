"""Killed Elaborators recover their durable application without repeating work."""

import selectors
import subprocess
import sys
import unittest

from auto.features.plan.gaps import PlanRepair
from auto.features.orchestration.operations import PlanContinue
from auto.features.plan.store import journal
from auto.features.task.operations import RunList, TaskExpand
from auto.utils.data import documentText, readJson
import test_004_01_integration_planning as fixtures
from support import rootFile


class ApplicationRecoveryTests(unittest.TestCase):
	setUp = fixtures.PlanningTests.setUp

	def interruptApplication(self, point, *, viaContinuation=False):
		elaborator = self.root / "elaborator.md"
		elaborator.write_text(documentText({"title": "Elaborate"}, "## Process\nProduce an elaboration\n"))
		rootFile(self.plan).write_text(documentText({"id": "demo", "goal": "Demo",
			"elaborator": {"task": str(elaborator)}}, "# Demo"))
		counter = self.root / "parameter-effects"
		template = self.root / "template.md"
		template.write_text(documentText({"title": "Work", "params": {"label": "!printf x >> " + str(counter) + "; printf Stored"}},
			"## Process\n{{ params.label }}\n"))
		workerFile = self.root / "application-worker.py"
		workerFile.write_text('''import sys
from pathlib import Path
from unittest.mock import patch
from auto.features.elaboration import operations
from auto.features.plan import store
from auto.runtime.model import Context
from auto.utils.data import documentText, yamlText
root, point = Path(sys.argv[1]), sys.argv[2]
output = documentText({"task": "root"}, "## task: work\\n```yaml\\n" + yamlText({"title": "Work", "from": str(root / "template.md")}) + "```\\n")
class Knowledge:
    def list(self, plan, kind):
        return []
class Engine:
    def start(self, context, request):
        with (root / "model-calls").open("a") as stream:
            stream.write("x")
        context.limits.usage["tokens"] = 7
        yield {"type": "ScriptMeta", "name": "usage", "value": {"tokens": 7}}
        yield {"type": "ScriptEnded", "status": "Succeeded", "output": output}
context = Context(project=root, plan=root / ".auto/demo", knowledge=Knowledge(),
    engines={"fake": Engine()}, config={"engine": "fake"})
def pause():
    print("ready", flush=True)
    sys.stdin.readline()
originalApply, originalAppend, originalWrite = operations.ElaborationApply, store.append, store.atomicWrite
def apply(*args, **kwargs):
    pause()
    return originalApply(*args, **kwargs)
def append(*args, **kwargs):
    if point == "after-journal":
        originalAppend(*args, **kwargs)
    pause()
    if point == "before-journal":
        originalAppend(*args, **kwargs)
def write(path, text):
    if path.name == ".pending.json":
        pause()
    return originalWrite(path, text)
target, replacement = ("auto.features.elaboration.operations.ElaborationApply", apply) if point == "before-application" else \\
    ("auto.features.plan.store.atomicWrite", write) if point == "before-staging" else ("auto.features.plan.store.append", append)
with patch(target, side_effect=replacement):
    operations.PlanRefine(context, context.plan, "Recover this application")
''')
		worker = subprocess.Popen([sys.executable, str(workerFile), str(self.root), point], stdin=subprocess.PIPE,
			stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
		def cleanup():
			if worker.poll() is None:
				worker.kill()
			worker.communicate(timeout=15)
		self.addCleanup(cleanup)
		with selectors.DefaultSelector() as selector:
			selector.register(worker.stdout, selectors.EVENT_READ)
			self.assertTrue(selector.select(timeout=15), "Worker did not reach the application boundary")
		line = worker.stdout.readline().strip()
		if line != "ready":
			stdout, stderr = worker.communicate(timeout=15)
			self.fail(f"Worker stopped before application: {line!r} {stdout} {stderr}")
		self.assertIsNone(worker.poll())
		run = RunList(self.ctx)[0]
		self.assertEqual(run["task"], "elaborator")
		self.assertEqual(run["status"], "Succeeded")
		self.assertEqual(run["usage"]["tokens"], 7)
		receipt = self.plan / "runs" / run["id"] / "receipt.json"
		originalReceipt = receipt.read_bytes()
		self.assertTrue(readJson(receipt.parent / "state.json")["applyElaboration"])
		self.assertEqual((self.root / "model-calls").read_text(), "x")
		if point == "before-application":
			self.assertFalse(counter.exists())
			self.assertFalse((self.plan / "tsk/work.md").exists())
		else:
			self.assertEqual(counter.read_text(), "x")
			if point == "before-staging":
				self.assertFalse((self.plan / ".pending.json").exists())
			else:
				self.assertEqual(readJson(self.plan / ".pending.json")["revision"]["elaborationRun"], run["id"])
		self.assertEqual(len(journal(self.plan)), 1 if point == "after-journal" else 0)
		worker.kill()
		worker.communicate(timeout=15)
		if viaContinuation:
			events = list(PlanContinue(self.ctx, self.plan))
			self.assertEqual(events[-1]["data"]["stop"], "ended")
			published = [event for event in events if event["kind"] == "PlanRevised"]
			self.assertEqual(len(published), 1)
			started = next(event for event in events if event["kind"] == "RunStarted" and event["data"]["task"] == "tsk-work")
			self.assertLess(published[0]["seq"], started["seq"])
		else:
			PlanRepair(self.ctx)
		self.assertEqual(TaskExpand(self.ctx, "work").split("\n\n## Output contract", 1)[0].strip(), "Stored")
		# Without durable staging, external parameter effects cannot be recovered.
		effects = "xx" if point == "before-staging" else "x"
		self.assertEqual(counter.read_text(), effects)
		self.assertEqual(len(journal(self.plan)), 1)
		self.assertEqual(journal(self.plan)[0]["elaborationRun"], run["id"])
		self.assertEqual(journal(self.plan)[0]["author"], run["id"])
		self.assertEqual(receipt.read_bytes(), originalReceipt)
		self.assertFalse((self.plan / ".pending.json").exists())
		template.unlink()
		PlanRepair(self.ctx)
		PlanRepair(self.ctx)
		self.assertEqual(counter.read_text(), effects)
		self.assertEqual((self.root / "model-calls").read_text(), "x")
		if viaContinuation:
			self.assertEqual(len(self.engine.calls), 1)
			self.assertIn("Stored", self.engine.calls[0])
		else:
			self.assertEqual(self.engine.calls, [])
		self.assertEqual(len(journal(self.plan)), 1)
		self.assertEqual(receipt.read_bytes(), originalReceipt)

	def testKilledAfterSuccessfulRunBeforeApplication(self):
		self.interruptApplication("before-application")

	def testKilledAfterStagingBeforeJournalPublication(self):
		self.interruptApplication("before-journal")

	def testKilledAfterJournalBeforeStagingCleanup(self):
		self.interruptApplication("after-journal")

	def testContinuationRecoversSuccessfulElaboratorBeforeSelectingWork(self):
		self.interruptApplication("before-application", viaContinuation=True)

	def testContinuationRecoversStagedApplicationBeforeSelectingWork(self):
		self.interruptApplication("before-journal", viaContinuation=True)

	def testContinuationRecoversJournaledApplicationBeforeSelectingWork(self):
		self.interruptApplication("after-journal", viaContinuation=True)

	def testKilledBeforeStagingRepeatsUncheckpointedParameterEffectOnExplicitRepair(self):
		self.interruptApplication("before-staging")

	def testContinuationCannotDeduplicateParameterEffectsLostBeforeStaging(self):
		self.interruptApplication("before-staging", viaContinuation=True)
