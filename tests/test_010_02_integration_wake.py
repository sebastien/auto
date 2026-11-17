"""Agent wake allocation, literal context and durable run integration."""

import copy
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from auto.features.memory.operations import RunMemoryRequest, RunWake
from auto.features.plan.operations import PlanRevise
from auto.features.task.operations import RunAnswer, RunGet, TaskStart
from auto.runtime.model import Context
from auto.utils.data import AutoError, documentText, readJson
from test_004_01_integration_planning import Fake, MemoryKnowledge
from support import rootFile


class Snapshot:
	def __init__(self):
		self.calls = []
		self.minimum = 20

	def wake(self, agent, profile, query, budgets=None):
		self.calls.append((agent, profile, query, budgets))
		return {"layers": {name: {"text": text, "revision": name + agent, "bytes": len(text.encode()),
			"allowance": (budgets or {}).get(name, 100), "omitted": False}
			for name, text in (("prompt", "persona " + agent), ("world", "{{ hidden }}"), ("recent", "recent"))}}

	def minimums(self, *args):
		return {"world": self.minimum, "recent": self.minimum}

	def model(self, *args):
		return [{"ref": "world/view:Example", "others": ["world/view:Peer"]}]


class Store(MemoryKnowledge):
	def __init__(self):
		super().__init__()
		self.snapshot = Snapshot()
		self.snapshots = 0

	def memorySnapshot(self):
		self.snapshots += 1
		return self.snapshot


class WakeTests(unittest.TestCase):
	def setUp(self):
		temp = tempfile.TemporaryDirectory()
		self.addCleanup(temp.cleanup)
		self.root = Path(temp.name)
		self.plan = self.root / ".auto/demo"
		self.plan.mkdir(parents=True)
		self.store = Store()
		self.context = Context(project=self.root, plan=self.plan, knowledge=self.store,
			defaults={"memory": {"agents": {"ux": {"agent": "private/agent:Ux"}, "dev": {"agent": "private/agent:Dev"}}}})
		self.select(["auto:agent/ux", "auto:agent/dev"])

	def select(self, agents):
		rootFile(self.plan).write_text(documentText({"id": "demo", "goal": "demo", "agents": agents}, "Demo"))

	def state(self):
		return {"task": {"path": str(self.plan / "tsk/work.md"), "goal": "Design a page"}, "inputs": {}}

	def testSharedBudgetsAndStableOrderKeepLearningTextLiteral(self):
		run, state = {"id": "r1", "task": "work"}, self.state()
		state["memory"] = RunWake(self.context, run, state)
		request = RunMemoryRequest(self.context, run, state, {"prompt": "Do work"})
		text = request["prompt"]
		self.assertLess(text.index("dev: prompt"), text.index("ux: world"))
		self.assertLess(text.index("dev: world"), text.index("ux: recent"))
		self.assertIn("{{ hidden }}", text)
		self.assertIn("instance = auto:demo/run/r1", text)
		self.assertIn("env MEMO_INSTANCE=auto:demo/run/r1 memo agent note --agent private/agent:Ux < OBSERVATION.json", text)
		for layer in ("world", "recent"):
			self.assertEqual(sum(data["layers"][layer]["allowance"] for data in run["memory"]["agents"].values()), 100)
		for data in run["memory"]["agents"].values():
			self.assertEqual(data["layers"]["prompt"]["allowance"], 100)
			self.assertNotIn("text", data["layers"]["world"])
			self.assertNotIn("cached_bytes", data["layers"]["world"])
		original = text
		RunMemoryRequest(self.context, run, state, request)
		self.assertEqual(request["prompt"], original)

	def testInsufficientMinimumsFailInsteadOfDroppingAgents(self):
		self.store.snapshot.minimum = 60
		with self.assertRaisesRegex(AutoError, "cannot cover"):
			RunWake(self.context, {"task": "work"}, self.state())

	def testCacheMeasurementsAreAttributedOnlyWhenReportedAndValidated(self):
		run, state = {"id": "r1", "task": "work"}, self.state()
		RunWake(self.context, run, state)
		event = {"name": "memory_cache", "value": {"agent": "auto:agent/ux", "layer": "world", "cached_bytes": 5}}
		self.context.runtime.recordRunMetadata(run, event)
		self.assertEqual(run["memory"]["agents"]["auto:agent/ux"]["layers"]["world"]["cached_bytes"], 5)
		for changes in ({"cached_bytes": 999}, {"cached_bytes": True}, {"agent": "auto:agent/missing"}, {"layer": "missing"}):
			with self.subTest(changes=changes), self.assertRaisesRegex(AutoError, "invalid per-layer"):
				self.context.runtime.recordRunMetadata(run, {**event, "value": {**event["value"], **changes}})

	def testChangedAgentMappingAndPlanRulesRestrictCapture(self):
		run, state = {"id": "r1", "task": "work"}, self.state()
		state["memory"] = RunWake(self.context, run, state)
		defaults = copy.deepcopy(self.context.defaults)
		defaults["memory"]["agents"]["ux"]["agent"] = "new/agent:Ux"
		text = RunMemoryRequest(self.context.derive(policyDefaults=defaults), run, state, {"prompt": "Work"})["prompt"]
		self.assertIn("Agent auto:agent/ux; memory.observe = deny", text)
		self.assertIn("Agent auto:agent/dev; memory.observe = allow", text)
		rootFile(self.plan).write_text(documentText({"id": "demo", "goal": "demo", "agents": ["auto:agent/ux", "auto:agent/dev"],
			"policy": {"memory.observe": "deny"}}, "Demo"))
		text = RunMemoryRequest(self.context, run, state, {"prompt": "Work"})["prompt"]
		self.assertIn("Agent auto:agent/dev; memory.observe = deny", text)

	def testNoSelectedAgentsAvoidsKnowledgeAccess(self):
		self.select([])
		self.assertIsNone(RunWake(self.context, {"task": "work"}, self.state()))
		self.assertEqual(self.store.snapshots, 0)

	def testElaboratorUsesFullWakeAndAddsWorldViewsAndPlanLearnings(self):
		run, state = {"id": "r1", "task": "elaborator"}, self.state()
		state["inputs"] = {"goal": "Target goal", "learnings": ["plan learning"], "context": ["existing context"]}
		RunWake(self.context, run, state)
		self.assertTrue(all(call[1:3] == ("full", "Target goal") for call in self.store.snapshot.calls))
		self.assertEqual(state["inputs"]["learnings"]["plan"], ["plan learning"])
		self.assertEqual(state["inputs"]["context"][0], "existing context")
		self.assertEqual(state["inputs"]["context"][1]["role"], "context")
		self.assertIn("others", state["inputs"]["context"][1]["views"][0])

	def testResumeReusesSnapshotButRefreshesCapturePolicy(self):
		self.select(["auto:agent/ux"])
		class Asking(Fake):
			def start(inner, context, request):
				inner.calls.append(request["prompt"])
				if "answer" not in request:
					yield {"type": "ScriptQuestion", "content": "Which colour?"}
				else:
					yield {"type": "ScriptEnded", "status": "Succeeded", "output": "done"}
		engine = Asking()
		context = self.context.derive(engines={"fake": engine}, config={"engine": "fake"})
		PlanRevise(context, self.plan, [{"op": "add", "task": {"id": "work", "title": "Work", "sections": {"Process": "Render {{ value }}"}, "inputs": {"value": "task text"}}}], "fixture")
		run = TaskStart(context, "work")
		self.assertEqual(run["status"], "Waiting")
		defaults = copy.deepcopy(context.defaults)
		defaults["memory"]["agents"]["ux"]["policy"] = {"memory.observe": "deny"}
		RunAnswer(context.derive(defaults=defaults), run["id"], "Blue", "Human chose blue")
		resumed = RunGet(context, run["id"])
		self.assertEqual(resumed["status"], "Succeeded")
		self.assertEqual(self.store.snapshots, 1)
		self.assertIn("memory.observe = deny", engine.calls[-1])
		self.assertIn("{{ hidden }}", engine.calls[-1])
		self.assertIn("Render task text", engine.calls[-1])
		self.assertEqual(engine.calls[-1].count("ux: prompt"), 1)
		state = readJson(self.plan / "runs" / run["id"] / "state.json")
		self.assertEqual(state["memory"]["agents"]["auto:agent/ux"]["layers"]["world"]["text"], "{{ hidden }}")

	def testElaborationStartUsesGoalProseWithSelectedMemoryAgents(self):
		from auto.features.elaboration.operations import ElaborationStart
		elaborator = self.root / "elaborator.md"
		elaborator.write_text(documentText({"title": "Elaborator"}, "## Process\n\nReview {{ goal }}"))
		for details in ({}, {"post": ["true"]}):
			with self.subTest(details=details):
				rootFile(self.plan).write_text(documentText({"id": "demo", "goal": "Design a useful page",
					"agents": ["auto:agent/ux", "auto:agent/dev"], "elaborator": {"task": str(elaborator)}, **details}, "Demo"))
				self.store.snapshot.calls.clear()
				run = ElaborationStart(self.context.derive(engines={"fake": Fake()}, config={"engine": "fake"}), self.plan, "root")
				self.assertEqual(run["status"], "Succeeded", run.get("failure"))
				state = readJson(self.plan / "runs" / run["id"] / "state.json")
				goal = state["inputs"]["goal"]
				self.assertTrue(goal.startswith("Design a useful page\n"))
				if details:
					self.assertIn("commands:", goal)
				self.assertTrue(self.store.snapshot.calls)
				self.assertTrue(all(call[1:3] == ("full", goal) for call in self.store.snapshot.calls))

	@unittest.skipUnless(importlib.util.find_spec("memo"), "Memo checkout is not installed")
	def testCaptureCommandWritesThroughTheRealCliWithRunProvenance(self):
		from memo import Memo
		from memo.features.repository.model import entryForId
		self.select(["auto:agent/ux"])
		memo = Memo.Default(str(self.root / ".memo"), cwd=str(self.root))
		log = "private/log:Observations"
		memo.log.createLog(log, identity="urn:test:observations")
		memo.backend.put(entryForId("private/agent:Ux").clone(
			meta={"identity": "urn:test:ux", "observations": log}, body="Design for people."))
		run, state = {"id": "r1", "task": "work"}, self.state()
		state["memory"] = RunWake(self.context, run, state)
		text = RunMemoryRequest(self.context, run, state, {"prompt": "Do work"})["prompt"]
		command = text.split("capture through `", 1)[1].split("`", 1)[0]
		source = "auto:demo/run/r1"
		(self.root / "OBSERVATION.json").write_text(json.dumps(
			{"text": "Keep forms concise.", "meta": {"source": source, "type": "observation"}}))
		environ = {**os.environ, "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""),
			"MEMO_CWD": str(self.root), "MEMO_INSTANCE": "inherited-wrong-instance"}
		result = subprocess.run(command, shell=True, cwd=self.root, env=environ, capture_output=True, text=True, timeout=30)
		self.assertEqual(result.returncode, 0, result.stderr)
		items = memo.agent.observationSnapshot("private/agent:Ux")["log"].log.items
		self.assertEqual(len(items), 1)
		self.assertEqual(items[0].text, "Keep forms concise.")
		self.assertEqual(items[0].writer, source)
		self.assertIn(source, items[0].sources)

	@unittest.skipUnless(importlib.util.find_spec("memo"), "Memo checkout is not installed")
	def testRealMemoSnapshotAndLayerMinimums(self):
		from memo.features.repository.model import entryForId
		from auto.integration.memo.store import MemoKnowledge
		store = MemoKnowledge(self.root)
		owner = entryForId("agent-ux/agent:Ux")
		store.backend.put(owner.clone(body="You design useful interfaces."))
		snapshot = store.memorySnapshot()
		for query in ("123", "你好", "Do it", '""', "..."):
			with self.subTest(query=query):
				self.assertEqual(snapshot.wake(owner.id, "standard", query), snapshot.wake(owner.id, "standard", None))
				self.assertEqual(snapshot.model(owner.id, query), snapshot.model(owner.id, None))
		wake = snapshot.wake(owner.id, "standard", 'Design "a page')
		self.assertIn("You design useful interfaces.", wake["layers"]["prompt"]["text"])
		current = store.backend.get(owner.id)
		store.backend.put(current.clone(body="Changed persona"), expected=current.revision())
		self.assertEqual(snapshot.wake(owner.id, "standard", 'Design "a page'), wake)
		minimums = snapshot.minimums(owner.id, "standard", "Design a page")
		for layer, minimum in minimums.items():
			self.assertGreater(minimum, 0)
			budgets = {"prompt": 0, "world": 0, "recent": 0, layer: minimum}
			self.assertLessEqual(snapshot.wake(owner.id, "standard", "Design a page", budgets)["layers"][layer]["bytes"], minimum)
			with self.assertRaises(AutoError):
				snapshot.wake(owner.id, "standard", "Design a page", {**budgets, layer: minimum - 1})
