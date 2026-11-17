"""Module: test_interfaces — Stable CLI output and MCP projection contracts."""

from __future__ import annotations

import asyncio
import json
import shlex
import subprocess
import sys
import tempfile
import unittest
from importlib.metadata import version
from pathlib import Path

from auto.app.operations import EXPOSURES, exitStatus
from support import rootFile


class InterfaceTests(unittest.TestCase):
	def cli(self, *args, stdin=None, cwd=None):
		return subprocess.run([sys.executable, "-m", "auto.app.cli.main", *args], input=stdin, text=True, capture_output=True, cwd=cwd, check=False)

	def testStdinYamlAndExitStatuses(self):
		result = self.cli("x", "script", "expand", "-", "--input", "name=world", "--format", "json", stdin="---\ninputs: {name: default}\n---\nHello {{ name }}")
		self.assertEqual(result.returncode, 0, result.stderr)
		self.assertTrue(json.loads(result.stdout).startswith("Hello world"))
		self.assertEqual(exitStatus({"status": "Skipped", "failure": {"kind": "input"}}), 3)
		self.assertEqual(self.cli("nonsense").returncode, 2)
		self.assertEqual(self.cli("x", "script", "expand", "./not-there.md").returncode, 1)
		self.assertEqual(exitStatus({"kind": "PlanSummary", "data": {"stop": "declined"}}), 0)
		self.assertEqual(exitStatus({"kind": "PlanSummary", "data": {"stop": "waiting"}}), 3)
		self.assertEqual(exitStatus({"kind": "PlanSummary", "data": {"stop": "ended", "status": "Succeeded"}}), 0)

	def testExposureHelp(self):
		for exposure in EXPOSURES:
			result = self.cli(*exposure.words, "--help")
			self.assertEqual(result.returncode, 0, result.stderr)
			self.assertIn(exposure.need, result.stdout)

	def testHelpMakesMarkdownScriptsDiscoverable(self):
		for arguments in (("help",), ("--help",)):
			with self.subTest(arguments=arguments):
				result = self.cli(*arguments)
				self.assertEqual(result.returncode, 0, result.stderr)
				self.assertIn("Auto runs Markdown scripts", result.stdout)
				everyday = result.stdout.split("Every day:", 1)[1].split("More, when you need it:", 1)[0]
				self.assertIn("auto run FILE.md  Run a Markdown script on its own, without a plan (or auto FILE.md)", everyday)

	def testHelpCommandMatchesHelpOption(self):
		commands = [(exposure.command,) for exposure in EXPOSURES if exposure.command]
		commands += [("init",), ("x",), ("x", "script", "expand"), ("x", "config"), ("script.md",)]
		for command in commands:
			with self.subTest(command=command):
				result = self.cli("help", *command)
				expected = self.cli(*command, "--help")
				self.assertEqual(result.returncode, 0, result.stderr)
				self.assertEqual(result.stdout, expected.stdout)
				self.assertEqual(result.stderr, expected.stderr)

	def testHumanResultsKeepStdoutClean(self):
		result = self.cli("x", "script", "expand", "-", stdin="Hello world")
		self.assertEqual(result.returncode, 0, result.stderr)
		self.assertTrue(result.stdout.startswith("Hello world"))
		self.assertEqual(result.stderr, "")
		structured = self.cli("x", "script", "expand", "-", "--format", "json", stdin="Hello world")
		self.assertTrue(json.loads(structured.stdout).startswith("Hello world"))
		self.assertEqual(structured.stderr, "")

	def testPolicyCliShowPreservesMcpResolveName(self):
		with tempfile.TemporaryDirectory() as directory:
			plan = Path(directory)
			rootFile(plan).write_text("---\nid: example\n---\n# Example\n")
			result = self.cli("x", "policy", "show", "--plan", str(plan), "--format", "json")
			self.assertEqual(result.returncode, 0, result.stderr)
			self.assertIn("rules", json.loads(result.stdout))
			exposure = next(item for item in EXPOSURES if item.name == "PolicyResolve")
			self.assertEqual(exposure.toolName, "policy_resolve")

	def testPlanStatusExplainsIdentityTasksAndExecution(self):
		with tempfile.TemporaryDirectory() as directory:
			root = Path(directory)
			(root / ".git").mkdir()
			plan = root / ".auto/demo"
			(plan / "tsk").mkdir(parents=True)
			rootFile(plan).write_text("---\nid: demo\ngoal: Ship the search feature\n---\n# Search\n")
			(plan / "tsk/index.md").write_text("---\nid: index\ntitle: Build the search index\n---\n## Process\n\nIndex documents\n")
			result = self.cli("status", "--plan", str(plan), cwd=root)
			self.assertEqual(result.returncode, 0, result.stderr)
			for text in ("demo  Ship the search feature", "Draft · 0/1 tasks", "○ tsk-index  Build the search index", "Next: auto go"):
				self.assertIn(text, result.stdout)
			structured = self.cli("status", "--plan", str(plan), "--format", "json", cwd=root)
			data = json.loads(structured.stdout)
			self.assertEqual(data["tasks"], {"root": "Blocked", "tsk-index": "Ready"})
			self.assertEqual(data["taskDetails"]["tsk-index"]["goal"], "Build the search index")
			self.assertIsNone(data["taskDetails"]["tsk-index"]["from"])
			self.assertEqual(data["goal"], "Ship the search feature")
			self.assertEqual(data["path"], str(plan))
			held = self.cli("x", "plan", "hold", "Pause", "Needs review", "--plan", str(plan), "--format", "json", cwd=root)
			self.assertEqual(held.returncode, 0, held.stderr)
			(plan / "continuation.json").write_text(json.dumps({"id": "continue1", "ended": "2026-01-01", "stop": "failed", "error": "Engine gone"}))
			result = self.cli("status", "--plan", str(plan), cwd=root)
			self.assertIn("Paused: Needs review", result.stdout)
			self.assertIn("Last run stopped: Engine gone", result.stdout)
			self.assertIn("Next: auto resume", result.stdout)
			self.assertNotIn("Next: auto go", result.stdout)

	def testPlanStatusDistinguishesLifecycleFromExecutionAndShowsFailures(self):
		with tempfile.TemporaryDirectory() as directory:
			root = Path(directory)
			(root / ".git").mkdir()
			plan = root / ".auto/demo"
			run = plan / "runs/run1"
			run.mkdir(parents=True)
			rootFile(plan).write_text("---\nid: demo\n---\n# A plan without an explicit goal\n")
			for state, status, execution in (("Running", "Active", "auto log --follow"),
				("Failed", "Failed", "auto show root"), ("Succeeded", "Succeeded", 'auto change "…"')):
				with self.subTest(state=state):
					receipt = {"id": "run1", "task": "root", "status": state, "started": "2026-01-01",
						"usage": {"tokens": 10, "cost": .01, "time": 2}, "children": {}}
					if state == "Failed":
						receipt["failure"] = {"kind": "engine", "reason": "Engine unavailable"}
					(run / "receipt.json").write_text(json.dumps(receipt))
					result = self.cli("status", "--plan", str(plan), "--verbose", cwd=root)
					self.assertEqual(result.returncode, 1 if state == "Failed" else 0, result.stderr)
					self.assertIn("demo  # A plan without an explicit goal", result.stdout)
					self.assertIn(status + " · no tasks yet", result.stdout)
					self.assertIn(f"[run1 {state}]", result.stdout)
					self.assertIn("Next: " + execution, result.stdout)
					if state == "Failed":
						self.assertIn("root  # A plan without an explicit goal  Engine unavailable", result.stdout)
					structured = self.cli("status", "--plan", str(plan), "--format", "json", cwd=root)
					self.assertEqual(json.loads(structured.stdout)["status"], status)

	def testFailureInspectionHintsAreExecutableAndPreserveJson(self):
		with tempfile.TemporaryDirectory(prefix="auto hints ") as directory:
			root = Path(directory)
			(root / ".git").mkdir()
			plan = root / ".auto/demo"
			run = plan / "runs/failed1"
			run.mkdir(parents=True)
			rootFile(plan).write_text("---\nid: demo\n---\n# Demo\n")
			receipt = {"id": "failed1", "task": "root", "status": "Failed", "started": "2026-01-01",
				"failure": {"kind": "engine", "reason": "Engine unavailable"}, "children": {}}
			(run / "receipt.json").write_text(json.dumps(receipt))
			(run / "journal.jsonl").write_text(json.dumps({"type": "RunEnded", "status": "Failed"}) + "\n")
			(run / "output.txt").write_text("Partial output")
			status = self.cli("status", "--plan", str(plan), cwd=root)
			hint = next(line.removeprefix("Next: ") for line in status.stdout.splitlines() if line.startswith("Next: "))
			self.assertEqual(hint, "auto show root to see why, then auto go --task root to retry")
			result = self.cli(*shlex.split(hint.partition(" to see why")[0])[1:], "--plan", str(plan), cwd=root)
			self.assertEqual(result.returncode, 0, result.stderr)
			self.assertIn("Failure: Engine unavailable", result.stdout)
			self.assertIn("Partial output", result.stdout)
			self.assertIn("auto x run journal failed1", result.stdout)
			result = self.cli("x", "run", "journal", "failed1", "--plan", str(plan), cwd=root)
			self.assertIn("RunEnded", result.stdout)
			result = self.cli("x", "run", "output", "failed1", "--plan", str(plan), cwd=root)
			self.assertEqual(result.returncode, 0, result.stderr)
			self.assertEqual(result.stdout.strip(), "Partial output")
			result = self.cli("x", "run", "get", "failed1", "--plan", str(plan), "--format", "json", cwd=root)
			self.assertEqual(json.loads(result.stdout)["failure"]["reason"], "Engine unavailable")
			self.assertEqual(result.stderr, "")
			# Failed historical runs must remain inspectable after a successful retry.
			retry = plan / "runs/retry1"
			retry.mkdir()
			(retry / "receipt.json").write_text(json.dumps({**receipt, "id": "retry1", "status": "Succeeded",
				"started": "2026-01-02", "failure": None}))
			status = self.cli("status", "--plan", str(plan), "--verbose", cwd=root)
			self.assertIn("Succeeded · no tasks yet", status.stdout)
			self.assertIn("Earlier failures: failed1 (auto x run get RUN)", status.stdout)

	def testRunInspectionFindsPlanAndRejectsAmbiguousIds(self):
		from unittest.mock import patch
		with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"AUTO_PLAN": ""}):
			root = Path(directory)
			(root / ".git").mkdir()
			for name in ("first", "second"):
				plan = root / ".auto" / name
				plan.mkdir(parents=True)
				rootFile(plan).write_text(f"---\nid: {name}\n---\n# {name}\n")
			run = root / ".auto/first/runs/run1"
			run.mkdir(parents=True)
			receipt = {"id": "run1", "task": "root", "status": "Succeeded", "started": "2026-01-01"}
			(run / "receipt.json").write_text(json.dumps(receipt))
			(run / "journal.jsonl").write_text('{"type": "RunEnded", "status": "Succeeded"}\n')
			(run / "output.txt").write_text("Done")
			for verb, expected in (("get", "Succeeded"), ("journal", "RunEnded"), ("output", "Done")):
				result = self.cli("x", "run", verb, "run1", "--format", "json", cwd=root)
				self.assertEqual(result.returncode, 0, result.stderr)
				self.assertIn(expected, result.stdout)
			# Active runs are discoverable too, before a receipt exists.
			active = root / ".auto/second/runs/live1"
			active.mkdir(parents=True)
			(active / "active.json").write_text(json.dumps({**receipt, "id": "live1", "status": "Running"}))
			result = self.cli("x", "run", "get", "live1", "--format", "json", cwd=root)
			self.assertEqual(json.loads(result.stdout)["status"], "Running")
			duplicate = root / ".auto/second/runs/run1"
			duplicate.mkdir()
			(duplicate / "receipt.json").write_text(json.dumps(receipt))
			ambiguous = self.cli("x", "run", "get", "run1", "--format", "json", cwd=root)
			self.assertEqual(ambiguous.returncode, 2)
			self.assertIn("Ambiguous run: run1", ambiguous.stdout)
			self.assertIn("--plan", ambiguous.stderr)
			for name in ("first", "second"):
				self.assertIn(name, ambiguous.stderr)
				selected = self.cli("x", "run", "get", "run1", "--plan", name, "--format", "json", cwd=root)
				self.assertEqual(selected.returncode, 0, selected.stderr)
			missing = self.cli("x", "run", "get", "missing", "--format", "json", cwd=root)
			self.assertEqual(missing.returncode, 2)
			self.assertIn("Run not found in this project: missing", missing.stdout)
			self.assertIn("auto plans", missing.stderr)

	def testContinuationHoldsAndEventCursorCli(self):
		with tempfile.TemporaryDirectory() as directory:
			root = Path(directory)
			(root / ".git").mkdir()
			plan = root / ".auto/demo"
			plan.mkdir(parents=True)
			rootFile(plan).write_text("---\nid: demo\n---\n# Demo\n")
			continued = self.cli("x", "plan", "continue", "--plan", str(plan), "--format", "json", cwd=root)
			self.assertEqual(continued.returncode, 0, continued.stderr)
			events = [json.loads(line) for line in continued.stdout.splitlines()]
			self.assertEqual(events[-1]["kind"], "ContinuationStopped")
			cursor = events[-1]["seq"]
			held = self.cli("x", "plan", "hold", "Pause", "Review", "--plan", str(plan), "--format", "json", cwd=root)
			self.assertEqual(held.returncode, 0, held.stderr)
			continued = self.cli("x", "plan", "continue", "--plan", str(plan), "--format", "json", cwd=root)
			self.assertEqual(continued.returncode, 3, continued.stderr)
			self.assertEqual(json.loads(continued.stdout.splitlines()[-1])["data"]["stop"], "held")
			released = self.cli("x", "plan", "release", "Ready", "--plan", str(plan), "--format", "json", cwd=root)
			self.assertEqual(released.returncode, 0, released.stderr)
			history = self.cli("log", "--plan", str(plan), "--since", str(cursor), "--format", "json", cwd=root)
			self.assertEqual(history.returncode, 0, history.stderr)
			rows = [json.loads(line) for line in history.stdout.splitlines()]
			self.assertTrue(all(row["seq"] > cursor for row in rows))
			self.assertEqual(rows[-1]["kind"], "PlanReleased")

	def testErrorRecovery(self):
		result = self.cli("x", "script", "expand")
		self.assertEqual(result.returncode, 2)
		self.assertIn("the following arguments are required: script", result.stderr)
		self.assertIn("Recovery: Give script (a markdown script). Run auto x script expand --help", result.stderr)
		result = self.cli("x", "plan")
		self.assertEqual(result.returncode, 2)
		self.assertIn("a verb is required", result.stderr)
		self.assertIn("Choose one of: create, continue, hold, release", result.stderr)
		result = self.cli("x", "script", "expand", "./not-there.md", "--format", "json")
		self.assertEqual(result.returncode, 1)
		self.assertIn("auto x script expand --help", json.loads(result.stdout)["failure"]["recovery"])
		self.assertIn("auto x config", result.stderr)
		with tempfile.TemporaryDirectory() as directory:
			from unittest.mock import patch
			with patch.dict("os.environ", {"AUTO_PLAN": ""}):
				result = self.cli("x", "task", "run", ".auto/catalogue/task/audit-architecture.md", "--format", "json", cwd=directory)
		self.assertEqual(result.returncode, 2)
		failure = json.loads(result.stdout)["failure"]
		self.assertEqual(failure["reason"], "No plan selected")
		self.assertIn("--plan <name-or-path>", failure["recovery"])
		self.assertIn('auto "GOAL"', failure["recovery"])
		self.assertIn("not a catalogue Markdown path", failure["recovery"])

	def testInvalidContextAndValuesAreUsageErrorsWithCandidates(self):
		with tempfile.TemporaryDirectory() as directory:
			root = Path(directory)
			(root / ".git").mkdir()
			plan = root / ".auto/demo"
			plan.mkdir(parents=True)
			rootFile(plan).write_text("---\nid: demo\n---\n# Demo\n")
			missing = self.cli("status", "--plan", "absent", "--format", "json", cwd=root)
			self.assertEqual(missing.returncode, 2, missing.stderr)
			self.assertIn("Available plans: demo", json.loads(missing.stdout)["failure"]["recovery"])
			unknown = self.cli("x", "task", "run", "absent", "--plan", "demo", "--format", "json", cwd=root)
			self.assertEqual(unknown.returncode, 2, unknown.stderr)
			self.assertIn("root", json.loads(unknown.stdout)["failure"]["recovery"])
			for budget in ("oops", "{cost: -1}", "{cost: many}"):
				invalid = self.cli("x", "plan", "create", "Work", "--budget", budget, "--format", "json", cwd=root)
				self.assertEqual(invalid.returncode, 2, invalid.stderr)
			self.assertEqual([path.name for path in plan.parent.iterdir()], ["demo"])

	def testUnknownOptionRecovery(self):
		for arguments, command in (
			(("run", "audit.md"), "auto run"),
			(("audit.md",), "auto run"),
			(("x", "config"), "auto x config"),
		):
			with self.subTest(command=arguments):
				result = self.cli(*arguments, "--set", "model=openai/gpt-6.1-sol")
				self.assertEqual(result.returncode, 2)
				self.assertIn("Use --model openai/gpt-6.1-sol instead of --set", result.stderr)
				self.assertIn(f"Run {command} --help", result.stderr)
				self.assertTrue(result.stderr.startswith(f"usage: {command} "))
		result = self.cli("run", "audit.md", "--unknown")
		self.assertEqual(result.returncode, 2)
		self.assertIn("Run auto run --help", result.stderr)

	def testScriptPathRelativeToWorkingDirectory(self):
		with tempfile.TemporaryDirectory() as directory:
			project = Path(directory)
			(project / ".git").mkdir()
			nested = project / "nested"
			nested.mkdir()
			(nested / "script.md").write_text("From working directory")
			(project / "script.md").write_text("From project root")
			for path in ("script.md", "./script.md", "../nested/script.md", str(nested / "script.md")):
				result = self.cli("x", "script", "expand", path, "--format", "json", cwd=nested)
				self.assertEqual(result.returncode, 0, result.stderr)
				self.assertTrue(json.loads(result.stdout).startswith("From working directory"))

	def testVersion(self):
		packageVersion = version("auto-orchestrator")
		self.assertIn(f"version {packageVersion}", self.cli("--help").stdout)
		result = self.cli("--version")
		self.assertEqual(result.returncode, 0, result.stderr)
		self.assertEqual(result.stdout.strip(), f"auto {packageVersion}")

	def testScriptPathShorthand(self):
		with tempfile.TemporaryDirectory() as directory:
			project = Path(directory)
			(project / ".git").mkdir()
			working = project / "working"
			working.mkdir()
			(working / "script.md").write_text('---\neach:\n  item: "!pwd > location"\n---\nNo items to run')
			for path in ("script.md", "./script.md", "../working/script.md", str(working / "script.md")):
				with self.subTest(path=path):
					result = self.cli(path, "--engine", "pi", "--format", "json", cwd=working)
					self.assertEqual(result.returncode, 0, result.stderr)
					self.assertEqual((project / "location").read_text().strip(), str(project))
					self.assertEqual(json.loads(result.stdout.splitlines()[-1])["status"], "Succeeded")
			self.assertEqual(self.cli("./missing.md", cwd=working).returncode, 1)

	def testMcpStdio(self):
		async def exercise(plan):
			from mcp import ClientSession, StdioServerParameters
			from mcp.client.stdio import stdio_client
			async with stdio_client(StdioServerParameters(command=sys.executable,
				args=["-m", "auto.app.cli.main", "x", "mcp", "--plan", str(plan)])) as (reader, writer):
				async with ClientSession(reader, writer) as session:
					await session.initialize()
					tools = await session.list_tools()
					self.assertEqual({tool.name for tool in tools.tools}, {item.toolName for item in EXPOSURES if item.mcp})
					result = await session.call_tool("catalogue_list", {})
					self.assertFalse(result.isError)
					self.assertTrue(result.structuredContent["result"])
					policy = await session.call_tool("policy_resolve", {})
					self.assertFalse(policy.isError)
					self.assertIn("rules", policy.structuredContent)
					events = await session.call_tool("plan_events", {})
					self.assertFalse(events.isError)
					self.assertEqual(events.structuredContent, {"result": None})
					unknown = await session.call_tool("consolidation_status", {"agent": "missing-agent"})
					self.assertTrue(unknown.isError)
					self.assertIn("Unknown Auto agent", str(unknown.content))
		with tempfile.TemporaryDirectory() as directory:
			plan = Path(directory)
			rootFile(plan).write_text("---\nid: demo\n---\n# Demo\n")
			asyncio.run(exercise(plan))

	def testMcpPlanEventsEmptyAndProgressResults(self):
		from types import SimpleNamespace
		from unittest.mock import AsyncMock, PropertyMock, patch
		from mcp import types
		from auto.app.mcp.main import createServer
		from auto.features.plan.operations import PlanRevise
		from auto.runtime.model import Context
		with tempfile.TemporaryDirectory() as directory:
			plan = Path(directory)
			rootFile(plan).write_text("---\nid: example\n---\n# Example\n")
			context = Context(project=plan, plan=plan)
			server = createServer(context)
			async def inline(operation, *args):
				return operation(*args)
			async def call():
				request = types.CallToolRequest(method="tools/call", params=types.CallToolRequestParams(name="plan_events", arguments={}))
				# Exercise projection directly; stdio integration covers thread dispatch.
				with patch("auto.app.mcp.main.asyncio.to_thread", new=inline):
					response = await server.request_handlers[types.CallToolRequest](request)
				return response.root
			result = asyncio.run(call())
			self.assertFalse(result.isError)
			self.assertEqual(result.structuredContent, {"result": None})
			PlanRevise(context, plan, [{"op": "add", "task": {"id": "work", "sections": {"Process": "Work"}}}], "Add work")
			session = SimpleNamespace(send_progress_notification=AsyncMock())
			with patch.object(type(server), "request_context", new_callable=PropertyMock,
				return_value=SimpleNamespace(meta=SimpleNamespace(progressToken="events"), session=session)):
				result = asyncio.run(call())
			self.assertFalse(result.isError)
			self.assertEqual(result.structuredContent["kind"], "PlanRevised")
			self.assertEqual(session.send_progress_notification.await_count, 1)

# EOF
