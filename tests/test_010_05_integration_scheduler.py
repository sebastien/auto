"""Event-triggered requests and independent invocation-time consolidation."""

import asyncio
import fcntl
import importlib.util
import io
import json
import unittest
from unittest.mock import patch

from auto.features.memory.consolidation import ConsolidationStart
from auto.features.memory.scheduler import HousekeepingCheck
from auto.features.plan.gaps import DecisionRecord
from auto.features.task.operations import TaskStart
from auto.utils.data import AutoError, atomicWrite, documentText, readJson
import test_010_03_integration_consolidation as fixtures
from support import rootFile


@unittest.skipUnless(importlib.util.find_spec("memo"), "Memo checkout is not installed")
class SchedulerTests(unittest.TestCase):
	setUp = fixtures.ConsolidationTests.setUp

	def plan(self, triggers, concerns=None):
		path = self.root / ".auto/demo"
		path.mkdir(parents=True)
		rootFile(path).write_text(documentText({"id": "demo", "goal": "Design", "agents": ["auto:agent/ux"]}, "Design"))
		self.context.defaults["memory"]["agents"]["ux"]["triggers"] = triggers
		if concerns is not None:
			self.context.defaults["memory"]["agents"]["ux"]["concerns"] = concerns
		return path

	def testPlanEndAndBacklogCoalesceAndNeverRunInsidePlanWriter(self):
		path = self.plan(["plan-ended", "backlog:1"])
		root = TaskStart(self.context.derive(plan=path), "root")
		self.assertEqual(root["status"], "Succeeded", root.get("failure"))
		pendingFile = self.root / ".auto/agents/ux/pending.json"
		pending = readJson(pendingFile)
		self.assertTrue(any(reason.startswith("PlanEnded:") for reason in pending["reasons"]))
		self.assertTrue(any(reason.startswith("RunEnded:") for reason in pending["reasons"]))
		self.assertEqual(self.calls, [])
		outer = self
		class Checking:
			def start(inner, context, request):
				with (path / ".lock").open("a+") as lock:
					fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
				outer.calls.append(request)
				yield {"type": "ScriptEnded", "status": "Succeeded", "output": json.dumps(outer.build(context.inputs))}
		result = HousekeepingCheck(self.context.derive(engines={"fake": Checking()}))
		self.assertEqual(result[0]["status"], "Succeeded", result)
		self.assertEqual(len(self.calls), 1)
		self.assertFalse(pendingFile.exists())
		self.assertEqual(HousekeepingCheck(self.context), [])

	def testDecisionConcernFilteringAndLostTriggerCursorDoNotDuplicateRequests(self):
		path = self.plan(["decision"], ["design"])
		DecisionRecord(self.context, path, "Backend choice?", True, "Other concern", ["backend"])
		pendingFile = self.root / ".auto/agents/ux/pending.json"
		self.assertFalse(pendingFile.exists())
		decision = DecisionRecord(self.context, path, "Project choice?", True, "Unlabelled reaches everyone")
		pending = readJson(pendingFile)
		cache = pendingFile.with_name("triggers.json")
		cache.unlink()
		HousekeepingCheck(self.context)
		self.assertEqual(len(self.calls), 1)
		request = readJson(self.root / ".auto/agents/ux/runs" / pending["id"] / "request.json")
		self.assertEqual(request["id"], pending["id"])
		cache.unlink(missing_ok=True)
		self.assertEqual(HousekeepingCheck(self.context), [])
		self.assertEqual(len(self.calls), 1)
		state = readJson(self.root / ".auto/agents/ux/runs" / request["id"] / "state.json")
		self.assertEqual([value["ref"] for value in state["inputs"]["decisions"]], [decision["identity"]])

	def testUnavailableMemoDoesNotClaimRunnableWorkOrFailDecision(self):
		path = self.plan(["decision"])
		with patch("auto.features.memory.scheduler.sessionFor", side_effect=AutoError("Retained Memo contract unavailable", "system")):
			decision = DecisionRecord(self.context, path, "A choice?", True, "Human decision")
		self.assertTrue(decision["id"])
		root = self.root / ".auto/agents/ux"
		self.assertFalse((root / "pending.json").exists())
		self.assertIn("unavailable", readJson(root / "maintenance.json")["failure"]["reason"])
		result = HousekeepingCheck(self.context)
		self.assertEqual(result[0]["status"], "Succeeded", result)
		self.assertEqual(len(self.calls), 1)
		self.assertEqual(readJson(root / "maintenance.json")["status"], "Succeeded")

	def testInvocationRepairsMissingDecisionCursorWithoutAnotherModel(self):
		path = self.plan(["decision"])
		DecisionRecord(self.context, path, "A choice?", True, "Trigger")
		HousekeepingCheck(self.context)
		self.assertEqual(len(self.calls), 1)
		file = self.root / ".auto/agents/ux/cursors.json"
		before = readJson(file)
		file.unlink()
		result = HousekeepingCheck(self.context)
		self.assertEqual(result[0]["status"], "Idle", result)
		self.assertEqual(len(self.calls), 1)
		self.assertEqual(readJson(file), before)

	def testAgentWithoutExplicitMemoIdentityIsNotQueuedAsRunnable(self):
		from memo.features.repository.model import entryForId
		from pathlib import Path
		self.memo.log.createLog("agent-new/log:Observations", identity="urn:test:new-log")
		self.memo.backend.put(entryForId("agent-new/agent:New").clone(meta={"observations": "agent-new/log:Observations"}, body="A new agent"))
		legacy = self.memo.backend.get("agent-new/agent:New")
		Path(legacy.path).write_text(documentText({"observations": "agent-new/log:Observations"}, legacy.body))
		self.memo.backend.invalidate()
		path = self.plan([])
		self.context.defaults["memory"]["agents"]["new"] = {"triggers": ["decision"]}
		rootFile(path).write_text(documentText({"id": "demo", "goal": "Design", "agents": ["auto:agent/new"]}, "Design"))
		DecisionRecord(self.context, path, "A choice?", True, "Trigger")
		root = self.root / ".auto/agents/new"
		self.assertFalse((root / "pending.json").exists())
		self.assertIn("explicit identity", readJson(root / "maintenance.json")["failure"]["reason"])
		self.assertEqual(self.calls, [])

	def testBacklogThresholdIsCheckedAfterRunEnded(self):
		path = self.plan(["backlog:2"])
		TaskStart(self.context.derive(plan=path), "root")
		pending = self.root / ".auto/agents/ux/pending.json"
		self.assertFalse(pending.exists())
		self.memo.agent.observe(self.owner.id, {"text": "Another outcome"}, "auto:fixture/run/two")
		self.assertFalse(pending.exists())
		TaskStart(self.context.derive(plan=path), "root")
		self.assertTrue(pending.exists())
		self.assertEqual(self.calls, [])

	def testAllowanceDeferredRequestRunsInNextWindowWithoutOverride(self):
		path = self.plan(["decision"])
		self.context.defaults["housekeeping"] = {"allowance": {"tokens": 50, "per": "day"}}
		self.context.defaults["memory"]["agents"]["ux"]["budget"] = {"tokens": 40}
		clock, outer = {"at": "2026-11-13T12:00:00+00:00"}, self
		class Metered:
			def start(inner, context, request):
				outer.calls.append(request)
				context.limits.usage["tokens"] += 30
				yield {"type": "ScriptMeta", "name": "usage", "value": dict(context.limits.usage)}
				yield {"type": "ScriptEnded", "status": "Succeeded", "output": json.dumps(outer.build(context.inputs))}
		context = self.context.derive(engines={"fake": Metered()})
		with patch("auto.features.memory.accounting.timestamp", side_effect=lambda: clock["at"]), patch("auto.features.memory.consolidation.timestamp", side_effect=lambda: clock["at"]):
			self.assertEqual(ConsolidationStart(context, "ux")["status"], "Succeeded")
			DecisionRecord(context, path, "New work?", True, "Trigger another pass")
			deferred = HousekeepingCheck(context)
			self.assertEqual(deferred[0]["status"], "Blocked", deferred)
			self.assertEqual(len(self.calls), 1)
			clock["at"] = "2026-11-14T00:00:01+00:00"
			result = HousekeepingCheck(context)
			self.assertEqual(result[0]["status"], "Succeeded", result)
			self.assertFalse(result[0]["housekeeping"]["override"])
			self.assertEqual(len(self.calls), 2)

	def testNewRequestDuringRunRemainsPendingForNextInvocation(self):
		path = self.plan(["decision"])
		DecisionRecord(self.context, path, "First choice?", True, "Initial trigger")
		late, outer = [], self
		class Producing:
			def start(inner, context, request):
				outer.calls.append(request)
				if not late:
					late.append(DecisionRecord(outer.context, path, "Concurrent choice?", True, "Arrived while a pass was running"))
				yield {"type": "ScriptEnded", "status": "Succeeded", "output": json.dumps(outer.build(context.inputs))}
		context = self.context.derive(engines={"fake": Producing()})
		first = HousekeepingCheck(context)
		self.assertEqual(first[0]["status"], "Succeeded", first)
		self.assertEqual(len(self.calls), 1)
		pendingFile = self.root / ".auto/agents/ux/pending.json"
		pending = readJson(pendingFile)
		self.assertNotEqual(pending["id"], first[0]["id"])
		second = HousekeepingCheck(context)
		self.assertEqual(second[0]["status"], "Succeeded", second)
		self.assertEqual(len(self.calls), 2)
		self.assertFalse(pendingFile.exists())

	def testMcpSchedulesMaintenanceWithoutAwaitingIt(self):
		from auto.app.mcp.main import createServer
		from mcp import types
		path = self.plan(["decision"])
		DecisionRecord(self.context, path, "A choice?", True, "Trigger")
		server = createServer(self.context.derive(plan=path))
		called = []
		async def inline(operation, *args):
			return operation(*args)
		async def call():
			request = types.CallToolRequest(method="tools/call", params=types.CallToolRequestParams(name="plan_status", arguments={}))
			with patch("auto.app.mcp.main.asyncio.to_thread", new=inline), patch("auto.features.memory.scheduler.HousekeepingCheck", side_effect=lambda context: called.append(context.author)):
				result = (await server.request_handlers[types.CallToolRequest](request)).root
				self.assertFalse(result.isError)
				self.assertEqual(called, [])
				await asyncio.sleep(0)
				self.assertEqual(len(called), 1)
				await asyncio.sleep(0)
		asyncio.run(call())

	def testMaintenanceFailureDoesNotReplaceCompletedCliResult(self):
		from auto.app.cli.main import main
		root = self.root / ".auto/agents/ux"
		root.mkdir(parents=True)
		atomicWrite(root / "pending.json", "invalid JSON")
		stdout = io.StringIO()
		with patch("auto.app.cli.main.contextFor", return_value=self.context), patch("sys.stdout", stdout):
			status = main(["x", "catalogue", "list", "--format", "json", "--no-input"])
		self.assertEqual(status, 0)
		self.assertIsInstance(json.loads(stdout.getvalue()), list)
		self.assertTrue(readJson(root / "maintenance.json")["failure"])

	def testCliPublishesResultBeforeRunningPendingHousekeeping(self):
		from auto.app.cli.main import main
		path = self.plan(["decision"])
		DecisionRecord(self.context, path, "A choice?", True, "Trigger")
		class Output(io.StringIO):
			published = ""
			def flush(inner):
				inner.published = inner.getvalue()
		stdout, outer = Output(), self
		class Checking:
			def start(inner, context, request):
				outer.assertIsInstance(json.loads(stdout.published), list)
				outer.assertEqual(context.environ["AUTO_HOUSEKEEPING"], "1")
				outer.assertEqual(HousekeepingCheck(context), [])
				outer.calls.append(request)
				yield {"type": "ScriptEnded", "status": "Succeeded", "output": json.dumps(outer.build(context.inputs))}
		context = self.context.derive(engines={"fake": Checking()})
		with patch("auto.app.cli.main.contextFor", return_value=context), patch("sys.stdout", stdout):
			status = main(["x", "catalogue", "list", "--format", "json", "--no-input"])
		self.assertEqual(status, 0)
		self.assertEqual(len(self.calls), 1)
		self.assertIsInstance(json.loads(stdout.getvalue()), list)
