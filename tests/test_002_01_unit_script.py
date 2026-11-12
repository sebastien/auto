"""Module: test_script — Script composition, effects and conformance checks."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from auto.features.script.model import Script
from auto.features.script.operations import ScriptExpand, ScriptInputs, ScriptStart
from auto.runtime.model import Context
from auto.runtime.operations import Value
from auto.utils.data import AutoError, document


class ScriptTests(unittest.TestCase):
	def setUp(self):
		self.temp = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp.cleanup)
		self.root = Path(self.temp.name)
		self.ctx = Context(project=self.root)

	def script(self, body, meta=None):
		return Script(body, self.root / "script.md", meta or {})

	def testValuesAndYaml(self):
		meta, body = document('---\ninputs: {yes: yes, flag: false, path: "!printf hi"}\n---\nHello')
		self.assertEqual(meta["inputs"]["yes"], "yes")
		self.assertEqual(body, "Hello")
		self.assertEqual(Value(self.ctx, "!printf 'hi\\n'"), "hi")
		self.assertEqual(Value(self.ctx, "\\./literal"), "./literal")
		self.assertEqual(Value(self.ctx, ["./missing"]), ["./missing"])
		with self.assertRaises(AutoError):
			document("---\nx: 1\nx: 2\n---\n")

	def testLazyTransclusionAndLiteralInsertion(self):
		(self.root / "code.py").write_text('print("{{ missing }}")\n```\n')
		body = "{% if false %}\n!touch should-not-exist\n@./missing\n{% endif %}\n@./code.py\n"
		result = ScriptExpand(self.ctx, self.script(body))
		self.assertIn('print("{{ missing }}")', result)
		self.assertIn("````py", result)
		self.assertFalse((self.root / "should-not-exist").exists())

	def testInheritedDefaultsAndSourceOrigins(self):
		(self.root / "child").mkdir()
		(self.root / "child/data.txt").write_text("child data")
		(self.root / "child/child.md").write_text('---\ninputs: {other: "./data.txt", name: child}\nparams: {style: child}\n---\n{{ name }} {{ other }} {{ params.style }}')
		ctx = self.ctx.derive(inputs={"name": "caller"})
		result = ScriptExpand(ctx, self.script("@./child/child.md", {"inputs": {"name": "parent"}, "params": {"style": "parent"}}))
		self.assertIn("caller child data parent", result)

	def testMarkdownSectionSelectorKeepsDefaults(self):
		(self.root / "base.md").write_text("---\ninputs: {name: base}\n---\n# Base\n\nIntro\n\n## Process\n\nHello {{ name }}\n\n### Detail\n\nKept\n\n## Post\n\nDropped\n")
		result = ScriptExpand(self.ctx, self.script("@./base.md#Process"))
		self.assertIn("Hello base", result)
		self.assertIn("Kept", result)
		self.assertNotIn("Intro", result)
		self.assertNotIn("Dropped", result)
		self.assertNotIn("## Process", result)
		with self.assertRaises(AutoError):
			ScriptExpand(self.ctx, self.script("@./base.md#Missing"))

	def testImplicitTransitiveInputsAndCycles(self):
		(self.root / "child.md").write_text("{{ extra }}\n{{ inputs.value }}")
		result = ScriptInputs(self.ctx, self.script("@./child.md\n{{ name }}"))
		self.assertTrue({"extra", "name", "inputs.value"} <= set(result["inputs"]))
		(self.root / "child.md").write_text("@./child.md")
		with self.assertRaises(AutoError):
			ScriptExpand(self.ctx, self.script("@./child.md"))

	def testLiquidAndFences(self):
		body = '{% if false %}a{% elif params.x contains "b" %}{% for x in params.x %}{{ x }}{% endfor %}{% else %}c{% endif %}\n```\n{{ untouched }}\n!false\n```'
		result = ScriptExpand(self.ctx, self.script(body, {"params": {"x": ["b", "d"]}}))
		self.assertTrue(result.startswith("bd"))
		self.assertIn("{{ untouched }}", result)

	def testConformanceAndCancellation(self):
		class Fake:
			closed = False
			def start(inner, context, request):
				try:
					yield {"type": "ScriptMessage", "content": "hello"}
					yield {"type": "ScriptEnded", "status": "Succeeded", "output": '{"x": "bad"}'}
				finally:
					inner.closed = True
		fake = Fake()
		ctx = self.ctx.derive(engines={"fake": fake}, config={"engine": "fake"})
		script = self.script("Hello", {"output": {"format": "json", "schema": {"type": "object", "properties": {"x": {"type": "integer"}}}}})
		stream = ScriptStart(ctx, script)
		next(stream)
		stream.close()
		self.assertTrue(fake.closed)
		result = list(ScriptStart(ctx, script))[-1]
		self.assertEqual(result["failure"]["kind"], "output")
		self.assertFalse((self.root / ".auto").exists())

	def testShellSubstitutionsAreData(self):
		value = "' ; touch injected ; echo '$HOME"
		for quote in ("", "'", '"'):
			body = "!printf '%s' " + quote + "{{ name }}" + quote
			result = ScriptExpand(self.ctx.derive(inputs={"name": value}), self.script(body))
			self.assertIn(value, result)
			self.assertFalse((self.root / "injected").exists())

	def lifecycleContext(self):
		class Fake:
			prompts = []
			def start(inner, context, request):
				inner.prompts.append(request["prompt"])
				yield {"type": "ScriptEnded", "status": "Succeeded", "output": "msg " + request["prompt"].split("\n", 1)[0]}
		fake = Fake()
		return fake, self.ctx.derive(engines={"fake": fake}, config={"engine": "fake"})

	def testEachPrePostAndApply(self):
		fake, ctx = self.lifecycleContext()
		(self.root / "done").write_text("")
		meta = {"each": {"rev": "!printf 'a\\nb\\n./c\\ndone\\n'"},
			"pre": ["test ! -e {{ rev }}"],
			"post": "grep -q '^msg '",
			"apply": ["cat > {{ rev }}.out"]}
		events = list(ScriptStart(ctx, self.script("Describe {{ rev }}", meta)))
		final = events[-1]
		self.assertEqual(final["status"], "Succeeded", final)
		self.assertEqual([item["status"] for item in final["output"]], ["Succeeded", "Succeeded", "Succeeded", "Skipped"])
		self.assertEqual((self.root / "a.out").read_text(), "msg Describe a")
		self.assertEqual((self.root / "c.out").read_text(), "msg Describe ./c")
		self.assertEqual(len(fake.prompts), 3)
		self.assertEqual([event["type"] for event in events].count("ScriptItemEnded"), 4)

	def testPostFailureSkipsApply(self):
		_, ctx = self.lifecycleContext()
		result = list(ScriptStart(ctx, self.script("Hello", {"post": "false", "apply": "touch applied"})))[-1]
		self.assertEqual(result["status"], "Failed")
		self.assertEqual(result["failure"]["kind"], "output")
		self.assertFalse((self.root / "applied").exists())
		result = list(ScriptStart(ctx, self.script("Hello", {"pre": "false"})))[-1]
		self.assertEqual(result["status"], "Skipped")

	def testDescribeEmptyPrecondition(self):
		from auto.features.script.templates import Renderer
		from auto.runtime.operations import Shell
		path = Path(__file__).resolve().parents[1] / "examples/action-describe_empty.md"
		meta, body = document(path.read_text())
		rev = "'; touch injected; #"
		renderer = Renderer(self.ctx, Script(body, path, meta))
		command = renderer.shellArgument(meta["pre"][0], {"rev": rev})
		# Exercise the actual example command with a fake jj, without touching history.
		for description, expected in (("", 0), ("Already described", 1)):
			config = {"env": {"DESCRIPTION": description, "REV": rev}}
			prefix = 'jj() { test "$4" = "$REV" || exit 99; printf "%s" "$DESCRIPTION"; }; '
			self.assertEqual(Shell(self.ctx, prefix + command, config)[2], expected)
		self.assertFalse((self.root / "injected").exists())

	def testDescribeEmptyPostcondition(self):
		from auto.runtime.operations import Shell
		path = Path(__file__).resolve().parents[1] / "examples/action-describe_empty.md"
		meta, _ = document(path.read_text())
		command = meta["post"][0]
		self.assertEqual(Shell(self.ctx, command, {}, "[Update] valid\n")[2], 0)
		_, stderr, code = Shell(self.ctx, command, {}, "invalid\n[Update] later\n")
		self.assertNotEqual(code, 0)
		self.assertIn("Expected the first line", stderr)

	def testTranscludedLifecycle(self):
		_, ctx = self.lifecycleContext()
		(self.root / "child.md").write_text('---\napply: touch child-applied\n---\nChild')
		result = list(ScriptStart(ctx, self.script("@./child.md")))[-1]
		self.assertEqual(result["status"], "Succeeded", result)
		self.assertFalse((self.root / "child-applied").exists())
		(self.root / "child.md").write_text('---\neach: {x: [1]}\n---\nChild')
		with self.assertRaises(AutoError):
			ScriptExpand(ctx, self.script("@./child.md"))

	def testChildEngineAndOutputDefaults(self):
		from auto.features.script.operations import prepare
		(self.root / "child.md").write_text('---\nengine: child\noutput: {format: json, schema: "./schema.json"}\n---\nHello')
		(self.root / "schema.json").write_text('{"type":"object"}')
		ctx = self.ctx.derive(defaults={"engine": "default"})
		result = prepare(ctx, self.script("@./child.md"))
		self.assertEqual(result["config"]["engine"], "child")
		self.assertEqual(result["output"]["schema"], {"type": "object"})
		self.assertEqual(prepare(ctx.derive(config={"engine": "caller"}), self.script("@./child.md"))["config"]["engine"], "caller")

# EOF
