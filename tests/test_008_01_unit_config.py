"""Module: test_config — Layered configuration and provider propagation."""

from __future__ import annotations

import os
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auto.app.cli.main import showConfig
from auto.features.script.model import Script
from auto.features.script.operations import prepare, ScriptStart
from auto.integration.opencode import OpenCode
from auto.runtime.config import contextFor
from auto.runtime.model import Context
from auto.utils.data import AutoError


class ConfigTests(unittest.TestCase):
	def testConfigTableColorsAndPlainOutput(self):
		class Terminal(io.StringIO):
			def isatty(self):
				return True
		values = {"model": "猫", "engine": "pi"}
		sources = {("model",): "AUTO_MODEL", ("engine",): "built-in"}
		terminal = Terminal()
		with patch("sys.stdout", terminal), patch.dict(os.environ, {"TERM": "xterm"}, clear=True):
			showConfig(values, sources)
		self.assertIn("SETTING", terminal.getvalue())
		self.assertIn("\033[36mengine\033[0m", terminal.getvalue())
		self.assertIn("AUTO_MODEL", terminal.getvalue())
		terminal = Terminal()
		with patch("sys.stdout", terminal), patch.dict(os.environ, {"TERM": "xterm", "NO_COLOR": "1"}, clear=True):
			showConfig(values, sources)
		self.assertNotIn("\033[", terminal.getvalue())
		self.assertIn("SETTING", terminal.getvalue())

	def testLayersAndOverrides(self):
		with tempfile.TemporaryDirectory() as directory:
			root = Path(directory)
			home, xdg, project = root / "home", root / "xdg", root / "project"
			for path in (home / ".auto", xdg / "auto", project / ".auto"):
				path.mkdir(parents=True)
			(home / ".auto/config.toml").write_text('engine = "pi"\n[features.example]\nhome = true\nvalue = "home"\n')
			(xdg / "auto/config.yaml").write_text('model: yaml\nfeatures:\n  example:\n    yaml: true\n')
			(xdg / "auto/config.toml").write_text('model = "xdg"\n[features.example]\nvalue = "xdg"\n')
			(project / ".auto/config.toml").write_text('provider = "openrouter"\nmodel = "project"\ntemperature = 0.2\n[features.example]\nproject = true\n')
			with patch.dict(os.environ, {"HOME": str(home), "XDG_CONFIG_HOME": str(xdg), "AUTO_MODEL": "environment"}, clear=True):
				ctx = contextFor(project, model="flag")
				self.assertEqual(ctx.defaults["features"]["example"], {"home": True, "yaml": True, "value": "xdg", "project": True})
				self.assertEqual(ctx.defaults["model"], "project")
				request = prepare(ctx, Script("hello", project / "test.md", {"model": "script", "provider": "script-provider"}))
				self.assertEqual(request["config"]["model"], "flag")
				self.assertEqual(request["config"]["provider"], "script-provider")
				self.assertEqual(request["config"]["temperature"], 0.2)
				self.assertEqual(contextFor(project).config["model"], "environment")
				(project / ".auto/config.toml").write_text("invalid = [")
				with self.assertRaisesRegex(AutoError, "config.toml"):
					contextFor(project)

	def testProviderEngineMapping(self):
		class Stub(OpenCode):
			def api(self, context, method, path, data=None, config=None):
				self.calls.append((path, data))
				if path.endswith("/form"):
					return {"data": []}
				if "/message?" in path:
					return {"data": [{"type": "idle", "outcome": "succeeded"}], "cursor": {}}
				return {}

		for settings, expected in (({"provider": "openrouter", "model": "deepseek/chat"}, {"providerID": "openrouter", "id": "deepseek/chat"}), ({"model": "opencode/deepseek"}, {"providerID": "opencode", "id": "deepseek"})):
			engine = Stub()
			engine.calls = []
			ctx = Context(engines={"opencode": engine}, config={"engine": "opencode", "isolated": False, **settings})
			list(ScriptStart(ctx, Script("hello", Path("test.md"))))
			self.assertEqual(engine.calls[0][1]["model"], expected)
		for engine in ("pi", "llm"):
			result = list(ScriptStart(Context(config={"engine": engine, "provider": "source"}), Script("hello", Path("test.md"))))
			self.assertIn("provider", result[-1]["failure"]["reason"])

	def testConfigCommandSources(self):
		with tempfile.TemporaryDirectory() as directory:
			root = Path(directory)
			home, xdg, project = root / "home", root / "xdg", root / "project"
			for path in (home / ".auto", xdg / "auto", project / ".auto"):
				path.mkdir(parents=True)
			(xdg / "auto/config.toml").write_text('model = "old"\n[features.example]\na = 1\nb = 2\n')
			(project / ".auto/config.toml").write_text('[features.example]\nb = 3\n')
			environ = {**os.environ, "HOME": str(home), "XDG_CONFIG_HOME": str(xdg), "AUTO_MODEL": "env"}
			result = subprocess.run([sys.executable, "-m", "auto.app.cli.main", "config", "--model", "flag"], cwd=project, env=environ, text=True, capture_output=True)
			self.assertEqual(result.returncode, 0, result.stderr)
			self.assertIn('engine = "pi" # source=built-in', result.stdout)
			self.assertIn('model = "flag" # source=--model', result.stdout)
			self.assertIn(f'features.example.a = 1 # source={xdg / "auto/config.toml"}', result.stdout)
			self.assertIn(f'features.example.b = 3 # source={project / ".auto/config.toml"}', result.stdout)
			result = subprocess.run([sys.executable, "-m", "auto.app.cli.main", "config"], cwd=project, env=environ, text=True, capture_output=True)
			self.assertIn('model = "env" # source=AUTO_MODEL', result.stdout)

# EOF
