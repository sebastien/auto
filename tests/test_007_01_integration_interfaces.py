"""Module: test_interfaces — Stable CLI output and MCP projection contracts."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import unittest
from importlib.metadata import version

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

	def testVersion(self):
		packageVersion = version("auto-orchestrator")
		self.assertIn(f"version {packageVersion}", self.cli("--help").stdout)
		result = self.cli("--version")
		self.assertEqual(result.returncode, 0, result.stderr)
		self.assertEqual(result.stdout.strip(), f"auto {packageVersion}")

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
