"""Module: test_transform — Explicit lifecycle output transformations."""

from __future__ import annotations

import tempfile
import unittest
import io
from pathlib import Path

from auto.features.script.model import Script
from auto.features.script.operations import ScriptStart
from auto.runtime.config import validateConfig
from auto.runtime.model import Context
from auto.utils.data import AutoError


class TransformTests(unittest.TestCase):
	def setUp(self):
		self.temp = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp.cleanup)
		self.root = Path(self.temp.name)
		self.modelOutput = '{"raw":1}'
		class Fake:
			def start(inner, context, request):
				yield {"type": "ScriptEnded", "status": "Succeeded", "output": self.modelOutput}
		self.ctx = Context(project=self.root, engines={"fake": Fake()}, config={"engine": "fake"})

	def runScript(self, **meta):
		return list(ScriptStart(self.ctx, Script("Hello", self.root / "script.md", meta)))

	def testJsonProjectionAndOrder(self):
		meta = {"output": {"format": "json", "schema": {"type": "object", "required": ["raw"]}},
			"post": "grep -q raw", "transform": ["printf '{\"processed\":2}'", "grep -q processed && printf '{\"final\":3}'"],
			"apply": "cat > applied.json"}
		events = self.runScript(**meta)
		self.assertEqual(events[-1]["output"], {"final": 3})
		self.assertEqual(events[-1]["status"], "Succeeded", events[-1])
		self.assertEqual([event["name"] for event in events if event["type"] == "ScriptAction"], ["post", "transform", "transform", "apply"])
		self.assertEqual((self.root / "applied.json").read_text(), '{"final":3}')

	def testFailuresDoNotReturnRawOrApply(self):
		for command in ["printf '{'; exit 1", "printf ''", "printf '{bad}'", "printf '{\"a\":1,\"a\":2}'", "printf NaN"]:
			with self.subTest(command=command):
				final = self.runScript(output={"format": "json"}, transform=command, apply="touch applied")[-1]
				self.assertEqual(final["status"], "Failed")
				self.assertEqual(final["failure"]["kind"], "output")
				self.assertIsNone(final["output"])
				self.assertFalse((self.root / "applied").exists())

	def testPreAndPostPreventTransformation(self):
		for name in ("pre", "post"):
			meta = {name: "false", "transform": "touch transformed"}
			final = self.runScript(**meta)[-1]
			self.assertIn(final["status"], ("Skipped", "Failed"))
			self.assertFalse((self.root / "transformed").exists())

	def testTextAndOutputVariable(self):
		final = self.runScript(output={"format": "text"}, transform=["printf hello", "printf '%s world' {{ output }}"])[-1]
		self.assertEqual(final["output"], "hello world")

	def testJsonStringStdinAndRawDuplicateRejection(self):
		self.modelOutput = '"hello"'
		final = self.runScript(output={"format": "json"}, transform="cat", apply="cat > applied.json")[-1]
		self.assertEqual(final["output"], "hello")
		self.assertEqual((self.root / "applied.json").read_text(), '"hello"')
		self.modelOutput = '{"raw":1,"raw":2}'
		final = self.runScript(output={"format": "json"}, transform="touch transformed")[-1]
		self.assertEqual(final["status"], "Failed")
		self.assertIsNone(final["output"])
		self.assertFalse((self.root / "transformed").exists())

	def testChildFormatButNotChildLifecycle(self):
		(self.root / "child.md").write_text('---\noutput: {format: json}\ntransform: touch child-transform\n---\nChild')
		final = list(ScriptStart(self.ctx, Script("@./child.md", self.root / "script.md", {"transform": "printf '{\"ok\":true}'"})))[-1]
		self.assertEqual(final["output"], {"ok": True})
		self.assertFalse((self.root / "child-transform").exists())

	def testConfiguration(self):
		for transform in ("cat", ["cat"], []):
			validateConfig({"transform": transform})
		for transform in (False, {"command": "cat"}, [1]):
			with self.assertRaises(AutoError):
				validateConfig({"transform": transform})

	def testTextFrontendDoesNotStreamRaw(self):
		from auto.app.cli.frontend import Frontend
		stdout, stderr = io.StringIO(), io.StringIO()
		frontend = Frontend("text", stdout=stdout, stderr=stderr)
		frontend.scriptEvent({"type": "ExecutionStarted", "output": {"format": "text"}, "transform": True})
		frontend.scriptEvent({"type": "ScriptMessage", "content": "raw model text"})
		self.assertEqual(stdout.getvalue(), "")
		frontend.scriptEvent({"type": "ScriptEnded", "status": "Succeeded", "output": "processed"})
		self.assertEqual(stdout.getvalue(), "processed\n")


if __name__ == "__main__":
	unittest.main()

# EOF
