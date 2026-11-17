"""Ordinary Memoriser lifecycle and retained Memo application recovery."""

import fcntl
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auto.features.memory.consolidation import ConsolidationStart, ConsolidationStatus
from auto.runtime.model import Context, Limits
from auto.utils.data import documentText, readJson
from auto.features.plan.gaps import DecisionRecord
from auto.runtime.operations import Resolve
from auto.utils.layout import planPaths
from support import rootFile


@unittest.skipUnless(importlib.util.find_spec("memo"), "Memo checkout is not installed")
class ConsolidationTests(unittest.TestCase):
	def setUp(self):
		from memo import Memo
		from memo.integrations.filesystem.backend import LocalBackend
		from memo.utils.clock import SystemClock
		from memo.features.repository.model import entryForId
		from auto.integration.memo.store import MemoKnowledge
		temp = tempfile.TemporaryDirectory()
		self.addCleanup(temp.cleanup)
		self.root = Path(temp.name)
		self.memo = Memo(LocalBackend(str(self.root / ".memo")), SystemClock(), cwd=str(self.root))
		self.memo.log.createLog("agent-ux/log:Observations", identity="urn:test:ux-log")
		self.owner = entryForId("agent-ux/agent:Ux").clone(meta={"identity": "urn:test:ux", "observations": "agent-ux/log:Observations"}, body="Design for people.")
		self.memo.backend.put(self.owner)
		self.item = self.memo.agent.observe(self.owner.id, {"text": "A useful outcome", "sources": ["archive:conversation/1"]}, "auto:fixture/run/one").split("item=")[1]
		self.calls = []
		self.build = lambda inputs: {"id": inputs["consolidation"]["id"], "agent": inputs["agent"]["id"],
			"snapshot": inputs["due"]["log_revision"], "changes": [], "report": {"summary": "Reviewed the observation", "acknowledged": [self.item]}}
		outer = self
		class Fake:
			def start(inner, context, request):
				outer.calls.append(request)
				yield {"type": "ScriptEnded", "status": "Succeeded", "output": json.dumps(outer.build(context.inputs))}
		self.context = Context(project=self.root, engines={"fake": Fake()}, config={"engine": "fake"}, knowledge=MemoKnowledge(self.root),
			defaults={"memory": {"agents": {"ux": {}}}})

	def testBuiltinMemoriserRecordsInputsAndAppliesReportWithoutAPlan(self):
		run = ConsolidationStart(self.context, "auto:agent/ux", reason="Review work")
		self.assertEqual(run["status"], "Succeeded", run.get("failure"))
		self.assertTrue(run["application"]["published"])
		self.assertEqual(run["report"]["acknowledged"], [self.item])
		self.assertEqual(len(self.calls), 1)
		path = self.root / ".auto/agents/ux/runs" / run["id"]
		state = readJson(path / "state.json")
		self.assertEqual(state["inputs"]["record"], ["archive:conversation/1"])
		self.assertIn("authorization", state["inputs"])
		self.assertEqual(planPaths(self.root), [])
		self.assertTrue((path / "staged.json").exists())
		ref = "auto:agent/ux/run/" + run["id"]
		self.assertEqual(json.loads(Resolve(self.context, ref).text)["id"], run["id"])
		self.assertEqual(json.loads(Resolve(self.context, ref + "/receipt").text)["status"], "Succeeded")
		self.assertIn("ConsolidationApplied", Resolve(self.context, ref + "/journal").text)
		status = ConsolidationStatus(self.context, "ux")
		self.assertEqual(status["latest"]["id"], run["id"])
		self.assertEqual(status["due"]["counts"]["unrouted"], 0)

	def testNoWorkCompletesWithoutInventingPublishedChanges(self):
		self.build = lambda inputs: {"id": inputs["consolidation"]["id"], "agent": inputs["agent"]["id"], "snapshot": inputs["due"]["log_revision"],
			"changes": [], "report": {"summary": "Nothing to apply"}}
		run = ConsolidationStart(self.context, "ux")
		self.assertEqual(run["status"], "Succeeded", run.get("failure"))
		self.assertFalse(run["application"]["published"])
		self.assertTrue(run["application"]["complete"])

	def testNonCoreLearningsDoNotRequirePromotionAuthorization(self):
		base = self.build
		self.build = lambda inputs: {**base(inputs), "changes": [
			{"entry": "agent-ux/learning:Tip", "expected": None, "create": {"kind": "learning", "format": "document"},
				"content": "A useful non-core learning", "properties": {"sources": ["agent-ux/log:Observations item=" + self.item]}},
			{"entry": self.owner.id, "expected": inputs["agent"]["revision"], "properties": {"learnings": ["agent-ux/learning:Tip"]}}]}
		run = ConsolidationStart(self.context, "ux")
		self.assertEqual(run["status"], "Succeeded", run.get("failure"))
		self.assertEqual(run["validation"]["effects"], ["memory.consolidate"])

	def testRetiringSelectedCoreLearningRequiresPromotionAuthorization(self):
		from memo.features.repository.model import entryForId
		from memo.features.memory.model import MemoryNode, MemoryTree
		entry = entryForId("agent-ux/learning:Rule").clone(meta={"identity": "urn:test:rule", "agent_nodes": {
			"root": {"role": "guidance", "core": True, "status": "active"}}},
			memory=MemoryTree(nodes={"root": MemoryNode(text="A core rule", sources=("agent-ux/log:Observations item=" + self.item,))}))
		self.memo.backend.put(entry)
		owner = self.memo.backend.get(self.owner.id)
		self.memo.backend.put(owner.clone(meta={**owner.meta, "learnings": [entry.id]}), expected=owner.revision())
		self.memo.backend.invalidate()
		current = self.memo.backend.get(entry.id)
		base = self.build
		self.build = lambda inputs: {**base(inputs), "changes": [{"entry": entry.id, "expected": current.revision(),
			"policies": {"root": {"status": "retired", "reason": "Superseded"}}}]}
		run = ConsolidationStart(self.context, "ux")
		self.assertEqual(run["status"], "Failed", run)
		self.assertIn("memory.promote", run["failure"]["reason"])

	def testInvalidOutputLeavesInspectibleReceiptAndNoApplication(self):
		self.build = lambda inputs: {"id": "wrong", "agent": self.owner.id, "snapshot": inputs["due"]["log_revision"], "changes": [], "report": {"summary": "Invalid"}}
		run = ConsolidationStart(self.context, "ux")
		self.assertEqual(run["status"], "Failed")
		self.assertEqual(run["failure"]["kind"], "output")
		path = self.root / ".auto/agents/ux/runs" / run["id"]
		self.assertFalse((path / "staged.json").exists())
		self.assertTrue((path / "receipt.json").exists())

	def testPerspectiveChangeRequiresAllowAndConfirmBecomesAProposal(self):
		base = self.build
		change = {"entry": self.owner.id, "expected": None, "properties": {"focus": "Inclusive design"},
			"history": {"text": "Focus on inclusion", "sources": ["agent-ux/log:Observations item=" + self.item]}}
		def output(inputs):
			change["expected"] = inputs["agent"]["revision"]
			return {**base(inputs), "changes": [change]}
		self.build = output
		denied = ConsolidationStart(self.context, "ux")
		self.assertEqual(denied["status"], "Failed", denied)
		self.assertIn("memory.evolve", denied["failure"]["reason"])
		self.assertFalse((self.root / ".auto/agents/ux/runs" / denied["id"] / "staged.json").exists())
		self.build = lambda inputs: {**base(inputs), "report": {"summary": "Propose a perspective", "proposals": [
			{"text": "Focus on inclusion", "reason": "Observed need", "changes": [change], "sources": ["agent-ux/log:Observations item=" + self.item]}]}}
		proposed = ConsolidationStart(self.context, "ux")
		self.assertEqual(proposed["status"], "Succeeded", proposed.get("failure"))
		self.assertEqual(proposed["validation"]["proposal_effects"], [["memory.consolidate", "memory.evolve"]])
		self.memo.backend.invalidate()
		self.assertIsNone(self.memo.backend.get(self.owner.id).meta.get("focus"))
		settings = {"memory": {"agents": {"ux": {"policy": {"memory.evolve": "deny"}}}}}
		deniedProposal = ConsolidationStart(self.context.derive(defaults=settings), "ux")
		self.assertEqual(deniedProposal["status"], "Failed")
		self.assertIn("denied memory effect", deniedProposal["failure"]["reason"])

	def testAcceptedProposalAuthorizesOnePassAndDismissedDoesNot(self):
		base = self.build
		change = {"entry": self.owner.id, "expected": None, "properties": {"focus": "Inclusive design"},
			"history": {"text": "Focus on inclusion", "sources": ["agent-ux/log:Observations item=" + self.item]}}
		def applying(inputs):
			change["expected"] = inputs["agent"]["revision"]
			self.inputs = inputs
			return {**base(inputs), "changes": [change]}
		self.build = lambda inputs: {**base(inputs), "report": {"summary": "Propose", "proposals": [
			{"text": "Focus on inclusion", "reason": "Observed need", "changes": [], "sources": ["agent-ux/log:Observations item=" + self.item]}]}}
		proposed = ConsolidationStart(self.context, "ux")
		self.assertEqual(proposed["status"], "Succeeded", proposed.get("failure"))
		self.memo.backend.invalidate()
		proposal = ConsolidationStatus(self.context, "ux")["proposals"][0]["id"]
		# A dismissed proposal grants nothing.
		self.memo.agent.observe(self.owner.id, {"text": "Rejected", "sources": ["archive:conversation/2"], "meta": {"type": "response", "dismisses": [{"item": proposal, "reason": "Rejected by the owner"}]}}, "auto-agent:conversation/lobby")
		self.build = applying
		rejected = ConsolidationStart(self.context, "ux")
		self.assertEqual(rejected["status"], "Failed", rejected)
		self.assertEqual(self.inputs["accepted"], [])
		self.assertEqual(self.inputs["authorization"]["memory.evolve"], "confirm")
		# A second proposal, accepted by a human, grants its pass the confirmed effects.
		self.build = lambda inputs: {**base(inputs), "report": {"summary": "Propose again", "proposals": [
			{"text": "Focus on inclusion, again", "reason": "Observed need", "changes": [], "sources": ["agent-ux/log:Observations item=" + self.item]}]}}
		self.assertEqual(ConsolidationStart(self.context, "ux")["status"], "Succeeded")
		self.memo.backend.invalidate()
		second = ConsolidationStatus(self.context, "ux")["proposals"][0]["id"]
		self.memo.agent.observe(self.owner.id, {"text": "Accepted", "sources": ["archive:conversation/3"], "meta": {"type": "response", "resolves": [second]}}, "auto-agent:conversation/lobby")
		self.build = applying
		accepted = ConsolidationStart(self.context, "ux")
		self.assertEqual(accepted["status"], "Succeeded", accepted.get("failure"))
		self.assertEqual([item["id"] for item in self.inputs["accepted"]], [second])
		self.assertEqual(self.inputs["authorization"]["memory.evolve"], "allow")
		self.assertEqual(self.inputs["authorization"]["memory.promote"], "allow")
		self.memo.backend.invalidate()
		self.assertEqual(self.memo.backend.get(self.owner.id).meta.get("focus"), "Inclusive design")
		# Taken up: the next pass is back under policy.
		change["properties"] = {"focus": "Accessible design"}
		again = ConsolidationStart(self.context, "ux")
		self.assertEqual(again["status"], "Failed", again)
		self.assertEqual(self.inputs["accepted"], [])
		self.assertIn("memory.evolve", again["failure"]["reason"])

	def testAcceptedProposalsNeverLoosenDeniedEffects(self):
		from auto.features.memory.consolidation import AgentRunScope
		policy = {"memory.observe": "allow", "memory.consolidate": "allow", "memory.promote": "confirm", "memory.evolve": "deny"}
		with patch("auto.features.memory.consolidation.memoryPolicy", return_value=policy):
			authorization = AgentRunScope("ux", {}, None).authorization(self.context, {"grants": ["memory.promote", "memory.evolve"]})
		self.assertEqual((authorization["memory.promote"], authorization["memory.evolve"]), ("allow", "deny"))

	def testCliAgentArgumentDoesNotBecomeHarnessAgentAndMcpKeepsIt(self):
		import asyncio
		from auto.app.cli.main import parser, commandLine
		from auto.app.operations import EXPOSURES
		from auto.app.mcp.main import createServer
		from mcp import types
		args = parser().parse_args(["x", "consolidation", "run", "auto:agent/ux", "--agent", "reader", "--phases", "[verify]"])
		self.assertEqual(args.agent, "auto:agent/ux")
		self.assertEqual(args.harnessAgent, "reader")
		self.assertIn("--agent reader", commandLine(args, args.exposure))
		statusExposure = next(item for item in EXPOSURES if item.name == "ConsolidationStatus")
		self.assertTrue(statusExposure.mcp)
		server = createServer(self.context)
		async def inline(operation, *args):
			return operation(*args)
		async def call():
			request = types.CallToolRequest(method="tools/call", params=types.CallToolRequestParams(name="consolidation_status", arguments={"agent": "auto:agent/ux"}))
			with patch("auto.app.mcp.main.asyncio.to_thread", new=inline):
				return (await server.request_handlers[types.CallToolRequest](request)).root
		result = asyncio.run(call())
		self.assertFalse(result.isError)
		self.assertEqual(result.structuredContent["agent"], "auto:agent/ux")
		(self.root / ".auto").mkdir(exist_ok=True)
		(self.root / ".auto/config.toml").write_text("[memory.agents.ux]\n")
		cli = subprocess.run([sys.executable, "-m", "auto.app.cli.main", "x", "consolidation", "status", "auto:agent/ux", "--no-input", "--format", "json"],
			cwd=self.root, capture_output=True, text=True, timeout=20)
		self.assertEqual(cli.returncode, 0, cli.stderr)
		self.assertEqual(json.loads(cli.stdout)["agent"], "auto:agent/ux")

	def testConcurrentRequestsCoalesceBehindAgentLock(self):
		root = self.root / ".auto/agents/ux"
		root.mkdir(parents=True)
		with (root / ".lock").open("a+") as lock:
			fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
			first = ConsolidationStart(self.context, "ux", ["verify"], "First")
			second = ConsolidationStart(self.context, "ux", ["derive"], "Second")
			self.assertEqual(first["status"], "Pending")
			self.assertEqual(first["pending"]["id"], second["pending"]["id"])
			self.assertEqual(second["pending"]["phases"], ["verify", "derive"])
			self.assertEqual(self.calls, [])
		run = ConsolidationStart(self.context, "ux", ["surface"], "Third")
		self.assertEqual(run["request"]["phases"], ["verify", "derive", "surface"])
		self.assertEqual(run["request"]["reasons"], ["First", "Second", "Third"])

	def testPartialApplicationIsReconciledBeforeAnotherModelRun(self):
		from memo.integrations.filesystem.backend import LocalBackend
		original = LocalBackend.appendLog
		failed = False
		def append(backend, ref, item):
			nonlocal failed
			if item.meta.get("type") == "outcome" and not failed:
				failed = True
				raise OSError("Injected crash before report publication")
			return original(backend, ref, item)
		base = self.build
		self.build = lambda inputs: {**base(inputs), "changes": [{"entry": "agent-ux/log:Observations", "append": [
			{"key": inputs["consolidation"]["id"] + "#1", "text": "A sourced correction", "sources": ["agent-ux/log:Observations item=" + self.item], "meta": {"type": "correction"}}]}]}
		with patch.object(LocalBackend, "appendLog", append):
			run = ConsolidationStart(self.context, "ux")
		self.assertEqual(run["status"], "Failed", run)
		self.assertTrue(run["application"]["completed"])
		path = self.root / ".auto/agents/ux/runs" / run["id"]
		receipt = (path / "receipt.json").read_bytes()
		self.build = base
		second = ConsolidationStart(self.context, "ux")
		self.assertEqual(second["status"], "Succeeded", second.get("failure"))
		self.assertTrue(readJson(path / "application.json")["complete"])
		self.assertEqual((path / "receipt.json").read_bytes(), receipt)
		self.memo.backend.invalidate()
		items = self.memo.backend.get("agent-ux/log:Observations").log.items
		self.assertEqual(sum(item.text == "A sourced correction" for item in items), 1)
		self.assertEqual(sum(item.meta.get("consolidation", {}).get("id") == run["id"] for item in items), 1)

	def testAllowanceCapsRunAndChargesFailuresWithoutUsingPlanBudget(self):
		outer = self
		class Metered:
			def start(inner, context, request):
				outer.calls.append(request)
				context.limits.usage["tokens"] += 30
				yield {"type": "ScriptMeta", "name": "usage", "value": dict(context.limits.usage)}
				yield {"type": "ScriptEnded", "status": "Failed", "failure": {"kind": "output", "reason": "A measured failure"}}
		context = self.context.derive(engines={"fake": Metered()}, limits=Limits({"tokens": 1}), defaults={"memory": {"agents": {"ux": {"budget": {"tokens": 40},
			"allowance": {"tokens": 50, "per": "day"}}}}, "housekeeping": {"allowance": {"tokens": 100, "per": "day"}}})
		first = ConsolidationStart(context, "ux")
		self.assertEqual(first["status"], "Failed")
		self.assertEqual(first["usage"]["tokens"], 30)
		blocked = ConsolidationStart(context, "ux")
		self.assertEqual(blocked["status"], "Blocked", blocked)
		self.assertEqual(len(self.calls), 1)
		self.assertTrue(blocked["pending"])
		override = ConsolidationStart(context, "ux", overrideAllowance=True)
		self.assertEqual(override["budget"]["tokens"], 40)
		self.assertEqual(len(self.calls), 2)
		allowances = ConsolidationStatus(context, "ux")["allowances"]
		self.assertTrue(all(item["used"]["tokens"] == 60 for item in allowances))
		self.assertEqual(context.limits.usage["tokens"], 0)
		self.assertEqual(context.limits.budget["tokens"], 1)

	def testDecisionReferencesAreFilteredAndCursorsAdvanceOnlyAfterApplication(self):
		from auto.features.task.operations import TaskStart
		plan = self.root / ".auto/demo"
		plan.mkdir(parents=True)
		rootFile(plan).write_text(documentText({"id": "demo", "goal": "Design", "agents": ["auto:agent/ux"]}, "Design"))
		context = self.context.derive(defaults={"memory": {"agents": {"ux": {"concerns": ["design"]}}}})
		root = TaskStart(context.derive(plan=plan), "root")
		self.assertEqual(root["status"], "Succeeded", root.get("failure"))
		relevant = DecisionRecord(context, plan, "Use a simple layout?", True, "Clarity", ["design"])
		DecisionRecord(context, plan, "Storage choice?", "A", "Backend", ["backend"])
		unlabelled = DecisionRecord(context, plan, "Project decision?", True, "General")
		base = self.build
		self.build = lambda inputs: {**base(inputs), "id": "invalid"}
		failed = ConsolidationStart(context, "ux")
		self.assertEqual(failed["status"], "Failed")
		self.assertFalse((self.root / ".auto/agents/ux/cursors.json").exists())
		self.build = base
		run = ConsolidationStart(context, "ux")
		self.assertEqual(run["status"], "Succeeded", run.get("failure"))
		state = readJson(self.root / ".auto/agents/ux/runs" / run["id"] / "state.json")
		self.assertEqual({item["ref"] for item in state["inputs"]["decisions"]}, {relevant["identity"], unlabelled["identity"]})
		self.assertTrue(all(set(item) == {"ref", "digest"} for item in state["inputs"]["decisions"]))
		cursorFile = self.root / ".auto/agents/ux/cursors.json"
		self.assertGreater(readJson(cursorFile)["auto:demo"], 0)
		cursorFile.unlink()  # Crash after application publication, before cursor publication.
		nextRun = ConsolidationStart(context, "ux")
		nextState = readJson(self.root / ".auto/agents/ux/runs" / nextRun["id"] / "state.json")
		self.assertEqual(nextState["inputs"]["decisions"], [])

	def testDecisionCreatedDuringModelRunRemainsForNextConsolidation(self):
		plan = self.root / ".auto/demo"
		plan.mkdir(parents=True)
		rootFile(plan).write_text(documentText({"id": "demo", "goal": "Design", "agents": ["auto:agent/ux"]}, "Design"))
		late, outer = [], self
		class Deciding:
			def start(inner, context, request):
				if not late:
					late.append(DecisionRecord(outer.context, plan, "A new choice?", True, "Arrived during consolidation"))
				yield {"type": "ScriptEnded", "status": "Succeeded", "output": json.dumps(outer.build(context.inputs))}
		context = self.context.derive(engines={"fake": Deciding()})
		first = ConsolidationStart(context, "ux")
		self.assertEqual(first["status"], "Succeeded", first.get("failure"))
		second = ConsolidationStart(context, "ux")
		self.assertEqual(second["status"], "Succeeded", second.get("failure"))
		state = readJson(self.root / ".auto/agents/ux/runs" / second["id"] / "state.json")
		self.assertEqual([item["ref"] for item in state["inputs"]["decisions"]], [late[0]["identity"]])

	def testStaleFreshApplicationFailsAsOutputAndNextRunUsesFreshInputs(self):
		from auto.integration.memo.consolidation import MemoConsolidation
		original = MemoConsolidation.validate
		base = self.build
		self.build = lambda inputs: {**base(inputs), "changes": [{"entry": self.owner.id, "expected": inputs["agent"]["revision"],
			"properties": {"focus": "Inclusive design"}, "history": {"text": "A sourced perspective", "sources": ["agent-ux/log:Observations item=" + self.item]}}]}
		context = self.context.derive(defaults={"memory": {"agents": {"ux": {"policy": {"memory.evolve": "allow"}}}}})
		def validate(session, value):
			result = original(session, value)
			current = session.memo.backend.get(self.owner.id)
			session.memo.backend.put(current.clone(body=current.body + "\nNew human note"), expected=current.revision())
			return result
		with patch.object(MemoConsolidation, "validate", new=validate):
			stale = ConsolidationStart(context, "ux")
		self.assertEqual(stale["status"], "Failed", stale)
		self.assertEqual(stale["failure"]["kind"], "output")
		self.assertEqual(stale["application"]["rejected"], "stale before application")
		fresh = ConsolidationStart(context, "ux")
		self.assertEqual(fresh["status"], "Succeeded", fresh.get("failure"))
		self.assertEqual(len(self.calls), 2)

	def testQuestionsFailAsOutputWithoutCreatingPlanGaps(self):
		class Asking:
			def start(inner, context, request):
				yield {"type": "ScriptQuestion", "content": "What should I learn?"}
		context = self.context.derive(engines={"fake": Asking()})
		run = ConsolidationStart(context, "ux")
		self.assertEqual(run["status"], "Failed", run)
		self.assertEqual(run["failure"]["kind"], "output")
		self.assertIn("report", run["failure"]["reason"])
		self.assertEqual(planPaths(self.root), [])

	def testCustomMemoriserKeepsOrdinaryChecksWithMandatoryDryPost(self):
		from auto.integration.memo.consolidation import MemoConsolidation
		file = self.root / "custom.md"
		file.write_text("---\nartifact: auto:catalogue/artifact/memory-consolidation\n---\n## Process\nReview\n## Post\nCheck sources\n## Evaluation\nJudge usefulness\n")
		order, outer = [], self
		class Checking:
			def start(inner, context, request):
				properties = request["output"]["schema"]["properties"]
				if "pass" in properties:
					order.append("Post")
					output = {"pass": True, "reason": "Grounded"}
				elif "score" in properties:
					order.append("Evaluation")
					output = {"score": .8, "feedback": "Useful"}
				else:
					order.append("Process")
					output = outer.build(context.inputs)
				yield {"type": "ScriptEnded", "status": "Succeeded", "output": json.dumps(output)}
		validate, apply = MemoConsolidation.validate, MemoConsolidation.apply
		def dry(session, value):
			order.append("dry")
			return validate(session, value)
		def publish(session, value):
			order.append("apply")
			return apply(session, value)
		context = self.context.derive(engines={"fake": Checking()}, defaults={"memory": {"agents": {"ux": {"memoriser": str(file)}}}})
		with patch.object(MemoConsolidation, "validate", new=dry), patch.object(MemoConsolidation, "apply", new=publish):
			run = ConsolidationStart(context, "ux")
		self.assertEqual(run["status"], "Succeeded", run.get("failure"))
		self.assertEqual(order, ["Process", "Post", "dry", "Evaluation", "apply"])
