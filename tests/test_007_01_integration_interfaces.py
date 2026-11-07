"""Module: test_interfaces — Stable CLI output and MCP projection contracts."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
import unittest
from importlib.metadata import version
from pathlib import Path

from auto.app.operations import EXPOSURES, exitStatus


class InterfaceTests(unittest.TestCase):
	def cli(self, *args, stdin=None, cwd=None):
		return subprocess.run([sys.executable, "-m", "auto.app.cli.main", *args], input=stdin, text=True, capture_output=True, cwd=cwd, check=False)

	def testStdinYamlAndExitStatuses(self):
		result = self.cli("script", "expand", "-", "--input", "name=world", "--format", "json", stdin="---\ninputs: {name: default}\n---\nHello {{ name }}")
		self.assertEqual(result.returncode, 0, result.stderr)
		self.assertTrue(json.loads(result.stdout).startswith("Hello world"))
		self.assertEqual(exitStatus({"status": "Skipped", "failure": {"kind": "input"}}), 3)
		self.assertEqual(self.cli("nonsense").returncode, 2)
		self.assertEqual(self.cli("script", "expand", "./not-there.md").returncode, 1)

	def testExposureHelp(self):
		for exposure in EXPOSURES:
			result = self.cli(*exposure.words, "--help")
			self.assertEqual(result.returncode, 0, result.stderr)
			self.assertIn(exposure.need, result.stdout)

	def testScriptPathRelativeToWorkingDirectory(self):
		with tempfile.TemporaryDirectory() as directory:
			project = Path(directory)
			(project / ".git").mkdir()
			nested = project / "nested"
			nested.mkdir()
			(nested / "script.md").write_text("From working directory")
			(project / "script.md").write_text("From project root")
			for path in ("script.md", "./script.md", "../nested/script.md", str(nested / "script.md")):
				result = self.cli("script", "expand", path, "--format", "json", cwd=nested)
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
		async def exercise():
			from mcp import ClientSession, StdioServerParameters
			from mcp.client.stdio import stdio_client
			async with stdio_client(StdioServerParameters(command=sys.executable, args=["-m", "auto.app.cli.main", "mcp"])) as (reader, writer):
				async with ClientSession(reader, writer) as session:
					await session.initialize()
					tools = await session.list_tools()
					self.assertEqual({tool.name for tool in tools.tools}, {"_".join(item.words) for item in EXPOSURES if item.mcp})
					result = await session.call_tool("catalogue_list", {})
					self.assertFalse(result.isError)
					self.assertTrue(result.structuredContent["result"])
		asyncio.run(exercise())

# EOF
