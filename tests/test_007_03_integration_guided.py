"""Module: test_guided — The goal-to-result loop and its command line."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auto.app.cli.frontend import TIPS, summaryView, tipFor
from auto.app.cli.main import reference
from auto.features.orchestration.guided import PlanDo, PlanGo, PlanPause, PlanResume, PlanSummary, PlanUse, QuestionAnswer, TaskShow
from auto.features.plan.gaps import GapList
from auto.features.plan.operations import PlanRevise, PlanStatus
from auto.runtime.config import contextFor
from auto.runtime.model import Context
from auto.utils.data import AutoError, documentText
from auto.utils.layout import currentPlan
from support import rootFile
from test_004_01_integration_planning import Fake, MemoryKnowledge

ELABORATION = """---
task: root
summary: One task
---
## Rationale
The goal is small.
## task: work
```yaml
title: Do the work
```
### Process
Produce the result.
"""


class Engine:
	"""Elaborates one task, passes its evaluation, then answers task prompts, asking first when told to."""

	def __init__(self, ask: bool = False):
		self.ask = ask
		self.calls = []

	def start(self, context, request):
		prompt = request["prompt"]
		self.calls.append(prompt)
		if "set `task: root`" in prompt:
			output = ELABORATION
		elif '"score"' in prompt or "score" in prompt.lower() and "feedback" in prompt.lower():
			output = '{"score":0.9,"feedback":"Good"}'
		elif self.ask and "answer" not in request:
			yield {"type": "ScriptQuestion", "content": "Which format?"}
			return
		else:
			output = "Result: " + str(request.get("answer", "done"))
		yield {"type": "ScriptEnded", "status": "Succeeded", "output": output}


class GuidedTests(unittest.TestCase):
	def setUp(self):
		temp = tempfile.TemporaryDirectory()
		self.addCleanup(temp.cleanup)
		self.root = Path(temp.name)
		self.questions = []

	def context(self, engine, answers=()):
		answers = iter(answers)
		def question(event):
			self.questions.append(event)
			return next(answers)
		context = Context(project=self.root, engines={"fake": engine}, config={"engine": "fake"}, knowledge=MemoryKnowledge(), question=question)
		context.environ["PATH"] = str(Path(__file__).resolve().parents[1] / ".venv/bin") + os.pathsep + context.environ["PATH"]
		return context

	def testGoalIsPlannedConfirmedRunAndSummarised(self):
		context = self.context(Engine(), ["run"])
		events = list(PlanDo(context, "Write a small report"))
		kinds = [event["kind"] for event in events]
		self.assertEqual(kinds[0], "PlanProposed")
		self.assertEqual([task["id"] for task in events[0]["data"]["tasks"]], ["tsk-work"])
		self.assertEqual(self.questions[0]["type"], "PlanConfirm")
		self.assertIn("RunStarted", kinds)
		summary = events[-1]
		self.assertEqual(summary["kind"], "PlanSummary")
		self.assertEqual((summary["data"]["stop"], summary["data"]["status"]), ("ended", "Succeeded"))
		self.assertEqual(summary["data"]["results"][0]["task"], "tsk-work")
		self.assertTrue(summary["data"]["results"][0]["output"].startswith("Result:"))
		self.assertEqual(currentPlan(self.root), Path(summary["data"]["path"]))
		self.assertEqual(contextFor(self.root).plan, Path(summary["data"]["path"]))

	def testDecliningKeepsThePlanWithoutRunningIt(self):
		engine = Engine()
		context = self.context(engine, ["no"])
		events = list(PlanDo(context, "Write a small report"))
		self.assertEqual(events[-1]["data"]["stop"], "declined")
		self.assertNotIn("RunStarted", [event["kind"] for event in events])
		plan = Path(events[-1]["data"]["path"])
		self.assertEqual(PlanStatus(context, plan)["tasks"]["tsk-work"], "Ready")
		# Without a person to ask, going on runs it to the end.
		events = list(PlanGo(context.derive(question=None), plan))
		self.assertEqual(events[-1]["data"]["status"], "Succeeded")

	def testQuestionsAreAskedInlineAndTheRunResumes(self):
		context = self.context(Engine(ask=True), ["run", "markdown"])
		events = list(PlanDo(context, "Write a small report"))
		self.assertEqual(self.questions[1]["content"], "Which format?")
		summary = events[-1]["data"]
		self.assertEqual((summary["stop"], summary["status"], summary["decisions"]), ("ended", "Succeeded", 1))
		self.assertIn("markdown", summary["results"][0]["output"])

	def testWithoutAPersonQuestionsWaitAndAreAnsweredByNumber(self):
		context = self.context(Engine(ask=True)).derive(question=None)
		events = list(PlanDo(context, "Write a small report"))
		summary = events[-1]["data"]
		self.assertEqual(summary["stop"], "waiting")
		self.assertEqual(summary["questions"], ["Which format?"])
		context = context.derive(plan=Path(summary["path"]))
		with self.assertRaises(AutoError):
			QuestionAnswer(context)
		with self.assertRaises(AutoError):
			QuestionAnswer(context, "2", "markdown")
		answered = QuestionAnswer(context, "1", "markdown")
		self.assertEqual((answered["question"], answered["answer"], answered["remaining"]), ("Which format?", "markdown", 0))
		self.assertEqual(GapList(context, filter={"status": "Open"}), [])
		events = list(PlanGo(context, None))
		self.assertEqual(events[-1]["data"]["status"], "Succeeded")
		shown = TaskShow(context, "work")
		self.assertEqual((shown["id"], shown["state"], shown["attempts"]), ("tsk-work", "Succeeded", 1))
		self.assertIn("markdown", shown["output"])

	def testTheSummaryListsTheFilesTheRunChanged(self):
		subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
		(self.root / "notes.txt").write_text("mine\n")
		engine = Engine()
		start = engine.start
		def write(context, request):
			prompt = request["prompt"]
			if "Produce the result" in prompt and "set `task: root`" not in prompt and "score" not in prompt.lower():
				(self.root / "report.md").write_text("# Report\n")
			yield from start(context, request)
		engine.start = write
		summary = list(PlanDo(self.context(engine, ["run"]), "Write a small report"))[-1]["data"]
		self.assertEqual(summary["status"], "Succeeded")
		self.assertEqual(summary["changed"], [{"path": "report.md", "change": "added"}])
		# Outside git, there is nothing to list.
		(self.root / ".git").rename(self.root / "git")
		summary = list(PlanGo(self.context(Engine()), summary["path"], task="work"))[-1]["data"]
		self.assertIsNone(summary["changed"])

	def testPauseResumeAndPlanSelection(self):
		plan = self.root / ".auto/pln-000-demo"
		rootFile(plan).write_text(documentText({"id": "pln-000-demo", "goal": "Demo"}, "# Demo"))
		context = self.context(Fake(["done"])).derive(question=None, plan=plan)
		PlanRevise(context, plan, [{"op": "add", "task": {"id": "work", "sections": {"Process": "work"}}}], "Add work")
		PlanPause(context, plan)
		self.assertEqual(PlanStatus(context, plan)["hold"]["mode"], "Pause")
		self.assertEqual(list(PlanGo(context, plan))[-1]["data"]["stop"], "held")
		events = list(PlanResume(context, plan))
		self.assertEqual(events[-1]["data"]["status"], "Succeeded")
		self.assertIsNone(PlanStatus(context, plan)["hold"])
		self.assertEqual(PlanUse(context, "000")["id"], "pln-000-demo")
		self.assertEqual(currentPlan(self.root), plan)
		self.assertEqual(PlanSummary(context, plan, "ended")["done"], 1)


class GuidedViewTests(unittest.TestCase):
	def summary(self, **values):
		return {"id": "pln-001-report", "goal": "Write a report", "path": "/p", "status": "Succeeded", "stop": "ended", "done": 1, "total": 1,
			"usage": {}, "budget": {}, "decisions": 0, "questions": [], "results": [], "failed": [], "hold": None, "agents": [], "changed": None,
			**values}

	def testSummaryShowsChangedFilesAndOneTipPerPlan(self):
		changed = [{"path": f"docs/{index:02}.md", "change": "added"} for index in range(13)] + [{"path": "old.md", "change": "deleted"}]
		text = summaryView(self.summary(changed=changed))
		self.assertIn("Changed 14 files:\n  + docs/00.md", text)
		self.assertNotIn("docs/12.md", text)
		self.assertIn("… and 2 more (git status)", text)
		self.assertTrue(text.endswith("Tip: " + TIPS[1]))
		self.assertNotEqual(tipFor("pln-000-a"), tipFor("pln-001-b"))
		self.assertEqual(tipFor("pln-000-a"), tipFor(f"pln-{len(TIPS):03x}-c"))
		waiting = summaryView(self.summary(status="Active", stop="waiting", questions=["Which format?"], changed=[]))
		self.assertNotIn("Tip:", waiting)
		self.assertNotIn("Changed", waiting)

	def testHelpListsTheEverydayCommandsFirst(self):
		text = reference()
		everyday, more = text.split("\n\n")
		for command in ('auto "GOAL"', "auto go", "auto answer", "auto change", "auto show"):
			self.assertIn(command, everyday)
		for command in ("auto pause", "auto plans", "auto learned", "auto FILE.md", "auto init", "auto x"):
			self.assertIn(command, more)
		self.assertIn("(or auto new)", everyday)


class GuidedCliTests(unittest.TestCase):
	def cli(self, *args, cwd):
		env = {**os.environ, "AUTO_PLAN": "", "NO_COLOR": "1"}
		return subprocess.run([sys.executable, "-m", "auto.app.cli.main", *args], text=True, capture_output=True, cwd=cwd, check=False, env=env)

	def plan(self, root, name="pln-000-demo", goal="Ship the search feature"):
		plan = root / ".auto" / name
		(plan / "tsk").mkdir(parents=True)
		rootFile(plan).write_text(f"---\nid: {name}\ngoal: {goal}\n---\n# Search\n")
		(plan / "tsk/index.md").write_text("---\nid: index\ntitle: Build the search index\n---\n## Process\n\nIndex documents\n")
		return plan

	def testBareAutoWelcomesThenShowsTheOnlyPlan(self):
		with tempfile.TemporaryDirectory() as directory:
			root = Path(directory)
			(root / ".git").mkdir()
			welcome = self.cli(cwd=root)
			self.assertEqual(welcome.returncode, 0, welcome.stderr)
			self.assertIn('auto "Document the project architecture"', welcome.stdout)
			self.plan(root)
			status = self.cli(cwd=root)
			self.assertEqual(status.returncode, 0, status.stderr)
			for text in ("pln-000-demo  Ship the search feature", "Draft · 0/1 tasks", "○ tsk-index  Build the search index", "Next: auto go"):
				self.assertIn(text, status.stdout)
			self.assertNotIn("root", status.stdout.split("\n\n")[1])

	def testStatusShowsQuestionsHoldsAndNextSteps(self):
		with tempfile.TemporaryDirectory() as directory:
			root = Path(directory)
			(root / ".git").mkdir()
			plan = self.plan(root)
			paused = self.cli("pause", "Needs review", cwd=root)
			self.assertEqual(paused.returncode, 0, paused.stderr)
			self.assertIn("Next: auto resume", paused.stdout)
			status = self.cli("status", cwd=root)
			self.assertIn("Paused: Needs review", status.stdout)
			self.assertIn("Next: auto resume", status.stdout)
			verbose = self.cli("status", "--verbose", cwd=root)
			self.assertIn("Path: " + str(plan.resolve()), verbose.stdout)
			structured = self.cli("status", "--format", "json", cwd=root)
			self.assertEqual(json.loads(structured.stdout)["tasks"], {"root": "Blocked", "tsk-index": "Ready"})
			log = self.cli("log", cwd=root)
			self.assertEqual(log.returncode, 0, log.stderr)
			self.assertIn("⏸ paused: Needs review", log.stdout)

	def testPlansAndSelection(self):
		with tempfile.TemporaryDirectory() as directory:
			root = Path(directory)
			(root / ".git").mkdir()
			self.plan(root)
			self.plan(root, "pln-001-other", "Another goal")
			ambiguous = self.cli("status", "--no-input", "--format", "json", cwd=root)
			self.assertEqual(ambiguous.returncode, 2)
			self.assertIn("auto use PLAN", json.loads(ambiguous.stdout)["failure"]["recovery"])
			plans = self.cli("plans", cwd=root)
			self.assertIn("pln-001-other", plans.stdout)
			self.assertIn("auto use PLAN", plans.stdout)
			used = self.cli("use", "001", cwd=root)
			self.assertEqual(used.returncode, 0, used.stderr)
			self.assertIn("Now using pln-001-other", used.stdout)
			status = self.cli(cwd=root)
			self.assertIn("pln-001-other  Another goal", status.stdout)

	def testGoalShorthandAndUnknownCommands(self):
		with tempfile.TemporaryDirectory() as directory:
			root = Path(directory)
			(root / ".git").mkdir()
			for words in (("stauts",), ("write", "a", "report")):
				typo = self.cli(*words, cwd=root)
				self.assertEqual(typo.returncode, 2)
				self.assertIn('quote the goal: auto "<goal>"', typo.stderr)
			internal = self.cli("gap", "list", cwd=root)
			self.assertEqual(internal.returncode, 2)
			self.assertIn("auto x gap --help", internal.stderr)
			self.assertFalse((root / ".auto").exists())
			with patch.dict(os.environ, {"AUTO_ENGINE": "missing-engine"}):
				result = self.cli("Write a report", "--yes", "--format", "json", cwd=root)
			self.assertNotEqual(result.returncode, 2, result.stderr)
			self.assertTrue((root / ".auto").is_dir())
			self.assertEqual(len(list((root / ".auto").glob("pln-000-write-report"))), 1)

	def testInternalsLiveUnderX(self):
		with tempfile.TemporaryDirectory() as directory:
			root = Path(directory)
			(root / ".git").mkdir()
			plan = self.plan(root)
			result = self.cli("x", "policy", "show", "--format", "json", cwd=root)
			self.assertEqual(result.returncode, 0, result.stderr)
			self.assertIn("rules", json.loads(result.stdout))
			old = self.cli("plan", "status", cwd=root)
			self.assertEqual(old.returncode, 2)
			self.assertIn("auto x plan --help", old.stderr)
			self.assertEqual(len(list((root / ".auto").glob("pln-*"))), 1)
			shown = self.cli("show", "index", cwd=root)
			self.assertEqual(shown.returncode, 0, shown.stderr)
			self.assertIn("tsk-index  Build the search index", shown.stdout)
			self.assertTrue(plan.exists())

# EOF
