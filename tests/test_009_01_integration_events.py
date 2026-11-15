"""Module: test_events — Durable supervision, cursor repair and human holds."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from auto.features.plan.control import PlanHold, PlanRelease
from auto.features.plan.events import PlanEvents
from auto.features.plan.gaps import DecisionRecord, GapDecide, GapDismiss, GapRaise, PlanRepair
from auto.features.plan.operations import PlanAbandon, PlanGet, PlanRevise, PlanStatus
from auto.features.plan.store import journal
from auto.features.task.operations import RunAnswer, RunGet, TaskStart
from auto.runtime.model import Context
from auto.utils.data import AutoError, atomicWrite, documentText
from test_004_01_integration_planning import Fake, MemoryKnowledge
from support import rootFile


class EventTests(unittest.TestCase):
	def setUp(self):
		temp = tempfile.TemporaryDirectory()
		self.addCleanup(temp.cleanup)
		self.root = Path(temp.name)
		self.plan = self.root / ".auto/demo"
		self.plan.mkdir(parents=True)
		rootFile(self.plan).write_text(documentText({"id": "demo", "goal": "Demo"}, "# Demo"))
		self.context = Context(project=self.root, plan=self.plan, engines={"fake": Fake()}, config={"engine": "fake"}, knowledge=MemoryKnowledge())

	def add(self, id):
		return PlanRevise(self.context, self.plan, [{"op": "add", "task": {"id": id, "sections": {"Process": "Work"}}}], "Add work")

	def events(self):
		return list(PlanEvents(self.context))

	def testRevisionRunAndPlanEndOrderFromManualOperations(self):
		revision = self.add("work")
		run = TaskStart(self.context, "work")
		root = TaskStart(self.context, "root")
		events = self.events()
		self.assertEqual([row["kind"] for row in events], ["PlanRevised", "RunStarted", "RunEnded", "RunStarted", "RunEnded", "PlanEnded"])
		self.assertEqual(events[0]["ref"], f"auto:demo/revision/{revision['id']}")
		self.assertEqual(events[1]["ref"], f"auto:demo/run/{run['id']}")
		self.assertEqual(events[-1]["data"]["status"], "Succeeded")
		self.assertEqual(events[-2]["data"]["run"], root["id"])
		self.assertEqual([row["seq"] for row in events], list(range(1, len(events) + 1)))
		DecisionRecord(self.context, self.plan, "Keep records?", True, "Useful")
		self.assertEqual(sum(row["kind"] == "PlanEnded" for row in self.events()), 1)

	def testGapAndDecisionEventsFollowTheirJournalRevisions(self):
		gap = GapRaise(self.context, self.plan, {"question": "Which?", "needed_by": ["root"]})
		decision = GapDecide(self.context, gap, "first", "Chosen")
		direct = DecisionRecord(self.context, self.plan, "What next?", "stop", "Enough")
		other = GapRaise(self.context, self.plan, {"question": "Still needed?", "needed_by": ["root"]})
		GapDismiss(self.context, other, "No longer relevant")
		events = self.events()
		self.assertEqual([row["kind"] for row in events][1::2], ["GapRaised", "GapResolved", "DecisionRecorded", "GapRaised", "GapDismissed"])
		self.assertEqual(events[3]["data"]["decision"], decision["id"])
		self.assertEqual(events[5]["ref"], direct["identity"])
		self.assertEqual(events[1]["data"]["needed_by"], ["root"])
		self.assertTrue(all(row["data"]["revision"] in {rev["id"] for rev in journal(self.plan)} for row in events))

	def testQuestionWaitingAndResumeAreRecordedOnce(self):
		class QuestionEngine:
			def start(inner, context, request):
				if "answer" not in request:
					yield {"type": "ScriptQuestion", "content": "Choose"}
				else:
					yield {"type": "ScriptEnded", "status": "Succeeded", "output": str(request["answer"])}
		self.context.engines["fake"] = QuestionEngine()
		self.add("work")
		run = TaskStart(self.context, "work")
		self.assertEqual(self.events()[-1]["kind"], "RunWaiting")
		RunAnswer(self.context, run, "yes", "Chosen")
		PlanRepair(self.context)
		events = self.events()
		self.assertEqual(sum(row["kind"] == "RunStarted" for row in events), 1)
		self.assertEqual(sum(row["kind"] == "RunWaiting" for row in events), 1)
		self.assertEqual(sum(row["kind"] == "RunEnded" for row in events), 1)
		kinds = [row["kind"] for row in events]
		self.assertLess(kinds.index("GapRaised"), kinds.index("RunWaiting"))
		self.assertLess(kinds.index("GapResolved"), kinds.index("RunEnded"))

	def testCursorResumesAndFollowSeesAnotherWriter(self):
		self.add("first")
		cursor = self.events()[-1]["seq"]
		self.assertEqual(list(PlanEvents(self.context, since=cursor)), [])
		stream = PlanEvents(self.context, since=cursor, follow=True)
		found = []
		reader = threading.Thread(target=lambda: found.append(next(stream)))
		reader.start()
		self.add("second")
		reader.join(2)
		self.assertFalse(reader.is_alive())
		stream.close()
		self.assertGreater(found[0]["seq"], cursor)
		self.assertEqual(found[0]["kind"], "PlanRevised")
		for invalid in (-1, True, "1"):
			with self.subTest(cursor=invalid), self.assertRaises(AutoError):
				list(PlanEvents(self.context, since=invalid))

	def testRepairMissingEventAfterDurableCommit(self):
		with patch("auto.features.plan.events.recordRevision", side_effect=OSError("Interrupted before event")):
			with self.assertRaises(OSError):
				self.add("work")
		self.assertEqual(len(journal(self.plan)), 1)
		self.assertTrue((self.plan / "tsk/work.md").is_file())
		PlanRepair(self.context)
		original = self.events()
		PlanRepair(self.context)
		self.assertEqual(self.events(), original)
		self.assertEqual(original[0]["data"]["revision"], journal(self.plan)[0]["id"])

	def testRepairLostEventsDoesNotReuseCursors(self):
		self.add("work")
		TaskStart(self.context, "work")
		TaskStart(self.context, "root")
		previous = self.events()
		(self.plan / "events.jsonl").write_text("")
		PlanRepair(self.context)
		repaired = self.events()
		self.assertTrue(all(row["seq"] > previous[-1]["seq"] for row in repaired))
		self.assertEqual([row["kind"] for row in repaired], [row["kind"] for row in previous])
		PlanRepair(self.context)
		self.assertEqual(self.events(), repaired)

	def testRepairRetainsHistoricalPlanEndsAcrossExplicitRetries(self):
		rootFile(self.plan).write_text(documentText({"id": "demo", "goal": "Demo"}, "## Process\nWork"))
		class Engine:
			attempts = 0
			def start(inner, context, request):
				inner.attempts += 1
				if inner.attempts == 1:
					yield {"type": "ScriptEnded", "status": "Failed", "failure": {"kind": "output", "reason": "Bad output"}}
				else:
					yield {"type": "ScriptEnded", "status": "Succeeded", "output": "Done"}
		self.context.engines["fake"] = Engine()
		TaskStart(self.context, "root")
		TaskStart(self.context, "root")
		(self.plan / "events.jsonl").write_text("")
		PlanRepair(self.context)
		self.assertEqual([row["data"]["status"] for row in self.events() if row["kind"] == "PlanEnded"], ["Failed", "Succeeded"])

	def testReservedSequenceSurvivesInterruptedPublication(self):
		def interrupted(path, text):
			if path.name == "events.jsonl":
				raise OSError("Interrupted event publication")
			return atomicWrite(path, text)
		with patch("auto.features.plan.events.atomicWrite", side_effect=interrupted):
			with self.assertRaises(OSError):
				self.add("work")
		reserved = json.loads((self.plan / ".event-seq.json").read_text())
		PlanRepair(self.context)
		self.assertGreater(self.events()[0]["seq"], reserved)

	def testConcurrentAppendsHaveOneSequenceOrder(self):
		errors = []
		def add(id):
			try:
				self.add(id)
			except Exception as error:
				errors.append(error)
		writers = [threading.Thread(target=add, args=(f"work-{index}",)) for index in range(8)]
		for writer in writers:
			writer.start()
		for writer in writers:
			writer.join(5)
		self.assertFalse(errors, errors)
		self.assertTrue(all(not writer.is_alive() for writer in writers))
		self.assertEqual([row["seq"] for row in self.events()], list(range(1, 9)))
		self.assertEqual([row["data"]["revision"] for row in self.events()], [row["id"] for row in journal(self.plan)])

	def testInvalidEventLogIsNotOverwritten(self):
		file = self.plan / "events.jsonl"
		file.write_text('{"seq": 1}\n{bad')
		with self.assertRaisesRegex(AutoError, "Invalid plan event log"):
			self.events()
		self.assertEqual(file.read_text(), '{"seq": 1}\n{bad')

	def testHumanHoldsAreDurableAndReleaseStartsNothing(self):
		self.add("work")
		revision = PlanHold(self.context, self.plan, "Pause", "Review first")
		self.assertEqual(PlanGet(self.context)["hold"], {"mode": "Pause", "author": "human", "reason": "Review first", "revision": revision["id"]})
		with self.assertRaisesRegex(AutoError, "held"):
			TaskStart(self.context, "work")
		self.assertEqual(self.events()[-1]["kind"], "PlanHeld")
		PlanRelease(self.context, self.plan, "Continue now")
		self.assertIsNone(PlanStatus(self.context)["hold"])
		self.assertEqual(self.events()[-1]["kind"], "PlanReleased")
		self.assertFalse((self.plan / "runs").exists())
		for operation in (lambda: PlanHold(self.context.derive(author="agent"), self.plan, "Pause", "No"),
			lambda: PlanRelease(self.context.derive(author="agent"), self.plan, "No"),
			lambda: PlanHold(self.context, self.plan, "Invalid", "No")):
			before = journal(self.plan)
			with self.assertRaises(AutoError):
				operation()
			self.assertEqual(journal(self.plan), before)

	def testPauseLetsCurrentRunFinishAndStopCancelsIt(self):
		for mode in ("Pause", "Stop"):
			with self.subTest(mode=mode):
				started, finish = threading.Event(), threading.Event()
				class SlowEngine:
					def start(inner, context, request):
						started.set()
						while not finish.wait(.01):
							if context.cancelled():
								raise AutoError("Cancelled", "cancelled")
						yield {"type": "ScriptEnded", "status": "Succeeded", "output": "Done"}
				self.context.engines["fake"] = SlowEngine()
				self.add(mode.lower())
				result = []
				worker = threading.Thread(target=lambda: result.append(TaskStart(self.context, mode.lower())))
				worker.start()
				self.assertTrue(started.wait(2))
				try:
					PlanHold(self.context, self.plan, mode, "Human hold")
				finally:
					finish.set()
					worker.join(3)
				self.assertFalse(worker.is_alive())
				self.assertEqual(result[0]["status"], "Succeeded" if mode == "Pause" else "Cancelled")
				self.assertEqual(RunGet(self.context, result[0])["status"], result[0]["status"])
				PlanRelease(self.context, self.plan, "Next test")

	def testAbandonmentPublishesPlanEnd(self):
		PlanAbandon(self.context, self.plan, "No longer needed")
		self.assertEqual(self.events()[-1]["kind"], "PlanEnded")
		self.assertEqual(self.events()[-1]["data"]["status"], "Abandoned")
