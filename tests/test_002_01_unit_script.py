"""Module: test_script — Script composition, effects and conformance checks."""

from __future__ import annotations

import tempfile
import shlex
import unittest
from pathlib import Path

from auto.features.script.model import Script
from auto.features.script.operations import ScriptExpand, ScriptInputs, ScriptStart
from auto.runtime.model import Context
from auto.runtime.model import Content
from auto.runtime.operations import Value
from auto.utils.data import AutoError, digest, document, documentText


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

	def testDifferentSectionsOfTheSameFileCanComposeWithoutACycle(self):
		folder = self.root / "fragments"
		folder.mkdir()
		(folder / "value.txt").write_text("Source origin")
		file = folder / "parts.md"
		file.write_text('---\ninputs: {name: "./value.txt"}\n---\n## First\n@./parts.md#Second\n## Second\n{{ name }}\n')
		context = self.ctx.derive(maxDepth=2)
		self.assertIn("Source origin", ScriptExpand(context, str(file) + "#First"))
		self.assertEqual(ScriptInputs(context, str(file) + "#First")["inputs"], ["name"])
		with self.assertRaisesRegex(AutoError, "depth"):
			ScriptExpand(context.derive(maxDepth=1), str(file) + "#First")

	def testRepeatedSectionIdentitiesStillFailAsCycles(self):
		file = self.root / "parts.md"
		file.write_text("## First\n@./parts.md#Second\n## Second\n@./parts.md#First\n")
		for operation in (ScriptExpand, ScriptInputs):
			with self.subTest(operation=operation.__name__), self.assertRaisesRegex(AutoError, "Recursive"):
				operation(self.ctx, str(file) + "#First")

	def testCatalogueAndLocalSelectorAliasesUseTheSameSourceIdentity(self):
		file = self.root / ".auto/catalogue/task/parts.md"
		file.parent.mkdir(parents=True)
		file.write_text("## First\n@auto:catalogue/task/parts#Second\n## Second\nLeaf\n")
		self.assertIn("Leaf", ScriptExpand(self.ctx, str(file) + "#First"))
		file.write_text("## First\n@auto:catalogue/task/parts#Second\n## Second\n@./parts.md#First\n")
		with self.assertRaisesRegex(AutoError, "Recursive"):
			ScriptExpand(self.ctx, str(file) + "#First")

	def testDifferentOwnerResourcesCanShareARelativeReferenceOrigin(self):
		bodies = {"owner:first": "@owner:second", "owner:second": "Owner leaf"}
		def resolve(context, ref):
			data = bodies[ref].encode()
			return Content(ref, data, digest(data), self.root / "virtual.md")
		context = self.ctx.derive(resolvers={"owner:": resolve})
		self.assertIn("Owner leaf", ScriptExpand(context, "owner:first"))
		self.assertEqual(ScriptInputs(context, "owner:first")["inputs"], [])
		bodies["owner:second"] = "@owner:first"
		with self.assertRaisesRegex(AutoError, "Recursive"):
			ScriptExpand(context, "owner:first")

	def testLineRangeAliasesDoNotRepeatShellEffectsBeforeDetectingACycle(self):
		for kind in ("file", "owner"):
			with self.subTest(kind=kind):
				file, marker = self.root / "parts.md", self.root / ("effects-" + kind + ".txt")
				target = "./parts.md" if kind == "file" else "owner:first"
				text = "# Source\n!printf x >> " + shlex.quote(str(marker)) + "\n@" + target + "#L02-03\n"
				file.write_text(text)
				context = self.ctx.derive(resolvers={"owner:": lambda context, ref: Content(ref, text.encode(), digest(text.encode()), file)})
				ref = str(file) if kind == "file" else target
				with self.assertRaisesRegex(AutoError, "Recursive"):
					ScriptExpand(context, ref + "#L2-3")
				self.assertEqual(marker.read_text(), "x")

	def testIgnoredChildLifecycleKeysAreNotValidatedOrInherited(self):
		file = self.root / "child.md"
		file.write_text(documentText({"pre": {"unused": "shape"}, "post": "{{ missing }}", "apply": False}, "Leaf"))
		self.assertIn("Leaf", ScriptExpand(self.ctx, self.script("@./child.md")))
		self.assertEqual(ScriptInputs(self.ctx, self.script("@./child.md"))["inputs"], [])
		with self.assertRaises(AutoError):
			ScriptExpand(self.ctx, str(file))

	def testNestedSourceTextKeepsItsChecksumAcrossYamlRoundTrips(self):
		for padding in (0, 17, 53, 101):
			with self.subTest(padding=padding):
				literal = documentText({"params": {"copy": "./part.md"}, "pre": "touch /tmp/" + "x" * padding + "/marker"}, "Data: {{ params.copy }}\n")
				snapshot = documentText({"params": {"copy": "\\" + literal}, "bindings": {"value": literal}}, "{{ data }}")
				stored = documentText({"sources": {"asset": {"text": snapshot, "digest": digest(snapshot.encode())}}}, "Task")
				meta, _ = document(stored)
				self.assertEqual(meta["sources"]["asset"]["text"], snapshot)
				self.assertEqual(digest(meta["sources"]["asset"]["text"].encode()), meta["sources"]["asset"]["digest"])

	def testImplicitTransitiveInputsAndCycles(self):
		(self.root / "child.md").write_text("{{ extra }}\n{{ inputs.value }}")
		result = ScriptInputs(self.ctx, self.script("@./child.md\n{{ name }}"))
		self.assertTrue({"extra", "name", "value"} <= set(result["inputs"]))
		self.assertNotIn("inputs.value", result["inputs"])
		(self.root / "child.md").write_text("@./child.md")
		with self.assertRaises(AutoError):
			ScriptExpand(self.ctx, self.script("@./child.md"))

	def testInputDiscoveryNormalizesNamespaceAndNestedPaths(self):
		loaded = self.script("{{ inputs.name }} {{ user.name }} {{ params.mode }} {{ plan }} {{ run.id }} {{ planet }} {{ runtime }}")
		self.assertEqual(ScriptInputs(self.ctx, loaded)["inputs"], ["name", "planet", "runtime", "user"])
		prompt = ScriptExpand(self.ctx.derive(inputs={"name": "Ada", "user": {"name": "Grace"}, "planet": "Earth", "runtime": "test"},
			params={"mode": "test"}, variables={"plan": "demo", "run": {"id": "test"}}), loaded)
		self.assertIn("Ada Grace", prompt)
		self.assertEqual(ScriptInputs(self.ctx, self.script("{{ inputs.run }} {{ inputs.params }}"))["inputs"], ["params", "run"])

	def testInputDiscoverySkipsOverriddenChildValues(self):
		(self.root / "child.md").write_text('---\ninputs: {data: "./missing-input.txt"}\nparams: {mode: "./missing-param.txt"}\noutput: {template: "./missing-contract.txt"}\n---\n{{ data }} {{ params.mode }}')
		loaded = self.script("@./child.md", {"inputs": {"data": "Parent"}, "params": {"mode": "Mode"}, "output": {"template": "\\Outline"}})
		result = ScriptInputs(self.ctx, loaded)
		self.assertEqual(result["inputs"], ["data"])
		self.assertEqual(result["references"], ["./child.md"])
		self.assertIn("Parent Mode", ScriptExpand(self.ctx, loaded))

	def testInputDiscoveryUsesCallerOriginsForExplicitValues(self):
		(self.root / "nested").mkdir()
		(self.root / "data.txt").write_text("Caller data")
		loaded = Script("{{ data }} {{ params.mode }}", self.root / "nested/script.md",
			{"inputs": {"data": "./missing-input.txt"}, "params": {"mode": "./missing-param.txt"}})
		context = self.ctx.derive(inputs={"data": "./data.txt"}, params={"mode": "./data.txt"})
		self.assertEqual(ScriptInputs(context, loaded)["references"], ["./data.txt"])
		self.assertTrue(ScriptExpand(context, loaded).startswith("Caller data Caller data"))

	def testInputDiscoveryKeepsMaterializedParametersFrozen(self):
		loaded = self.script("{{ params.mode }}", {"from": "./template.md", "params": {"mode": "\\Frozen"}})
		context = self.ctx.derive(params={"mode": "./missing.txt"})
		self.assertEqual(ScriptInputs(context, loaded)["references"], [])
		self.assertTrue(ScriptExpand(context, loaded).startswith("Frozen"))

	def testInputDiscoveryPreservesSiblingContractOrder(self):
		(self.root / "first.md").write_text('---\noutput: {template: "First"}\n---\nFirst')
		(self.root / "second.md").write_text('---\noutput: {template: "./missing.txt"}\n---\nSecond')
		loaded = self.script("@./first.md\n@./second.md\n")
		self.assertEqual(ScriptInputs(self.ctx, loaded)["references"], ["./first.md", "./second.md"])
		self.assertIn("Template:\n\n```markdown\nFirst\n```", ScriptExpand(self.ctx, loaded))

	def testInlineMarkdownDiscoveryDoesNotInspectItsContentsAsAScript(self):
		(self.root / "inline.md").write_text('---\neach: {x: "./missing.txt"}\noutput: {template: "./missing.txt"}\n---\n@./missing.md\n{{ unused }}')
		loaded = self.script("Inline: {{@./inline.md}}")
		self.assertEqual(ScriptInputs(self.ctx, loaded), {"inputs": [], "references": ["./inline.md"], "unresolved": []})
		self.assertIn("@./missing.md", ScriptExpand(self.ctx, loaded))

	def testConditionalSiblingDoesNotHideARequiredFallbackFromDiscovery(self):
		(self.root / "first.md").write_text('---\noutput: {template: "First"}\n---\nFirst')
		(self.root / "second.md").write_text('---\noutput: {template: "./missing.txt"}\n---\nSecond')
		loaded = self.script("{% if inputs.first %}\n@./first.md\n{% endif %}\n@./second.md\n")
		with self.assertRaisesRegex(AutoError, "Cannot resolve"):
			ScriptInputs(self.ctx, loaded)

	def testInlineFilePathsCanContainRuntimeLiquidExpressions(self):
		(self.root / "data.txt").write_text("{{ unchanged }}")
		loaded = self.script("Before {{@./{{ inputs.which }}.txt}} After")
		self.assertEqual(ScriptInputs(self.ctx, loaded)["inputs"], ["which"])
		self.assertIn("Before {{ unchanged }} After", ScriptExpand(self.ctx.derive(inputs={"which": "data"}), loaded))

	def testInlineShellAcceptsQuotedRuntimeLiquidExpressions(self):
		loaded = self.script("Before {{!printf '%s' \"{{ inputs.value }}\"}} After")
		value = "$HOME 'quoted'"
		self.assertIn("Before " + value + " After", ScriptExpand(self.ctx.derive(inputs={"value": value}), loaded))

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
