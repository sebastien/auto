"""Stateful Liquid and deferred sibling composition retain two-stage parity."""

from __future__ import annotations

import unittest

from auto.features.elaboration.operations import ElaborationApply, ElaborationValidate
from auto.features.elaboration.templates import ParameterTemplate
from auto.features.script.model import Script
from auto.features.script.operations import prepare
from auto.features.script.templates import Renderer
from auto.features.task.operations import TaskGet
from auto.utils.data import documentText
import test_006_04_integration_materialization as fixtures


class StatefulParityTests(unittest.TestCase):
	setUp = fixtures.MaterializationTests.setUp
	template = fixtures.MaterializationTests.template
	elaboration = fixtures.MaterializationTests.elaboration

	def parity(self, source, inputs):
		params = {"names": ["a", "a", "b"], "label": "{{ inputs.injected }}"}
		compiler = ParameterTemplate(params)
		compiled = compiler.expand(source)
		context = self.ctx.derive(inputs=inputs)
		actual = Renderer(context, Script(compiled, self.root / "compiled.md",
			{"params": params, "_template_bindings": compiler.bindings})).render()
		expected = Renderer(context, Script(source, self.root / "original.md", {"params": params})).render()
		self.assertEqual(actual, expected)
		return actual

	def testStatefulTagsRetainTheirCountersAndGroupsAcrossDeferredBranches(self):
		sources = (
			"{% for name in params.names %}{% cycle 'odd', 'even' %}:{{ name }}={{ inputs.value }};{% endfor %}",
			"{% increment counter %}{% for name in params.names %}{% increment counter %}:{{ inputs.value }};{% endfor %}{% increment counter %}",
			"{% decrement counter %}{% for name in params.names %}{% decrement counter %}:{{ inputs.value }};{% endfor %}{% decrement counter %}",
			"{% for name in params.names %}{% ifchanged %}{{ name }}{% endifchanged %}:{{ inputs.value }};{% endfor %}",
			"{% if inputs.enabled %}{% increment counter %}{% endif %}{% increment counter %}",
			"{% for name in params.names %}{% cycle 'group': 'a', 'b' %}{% if inputs.enabled %}{% cycle 'group': 'a', 'b' %}{% endif %}{% endfor %}",
		)
		for source in sources:
			for enabled in (True, False):
				with self.subTest(source=source, enabled=enabled):
					self.parity(source, {"value": "runtime", "enabled": enabled})

	def testAssignmentCaptureAndLoopOffsetsKeepRuntimeState(self):
		sources = (
			"{% assign text = params.label %}{% capture combined %}{{ text }}:{{ inputs.value }}{% endcapture %}{{ combined }}",
			"{% assign total = 0 %}{% for name in params.names %}{% assign total = total | plus: inputs.step %}{{ total }};{% endfor %}{{ total }}",
			"{% for name in params.names limit: 1 %}{{ name }}{% endfor %}/{% for name in params.names offset: continue %}{{ name }}={{ inputs.value }};{% endfor %}",
			"{% for name in params.names reversed limit: 2 offset: 1 %}{{ forloop.index }}:{{ name }}={{ inputs.value }};{% endfor %}",
			"{% tablerow name in params.names cols: 2 %}{{ name }}={{ inputs.value }}{% endtablerow %}",
		)
		for source in sources:
			with self.subTest(source=source):
				actual = self.parity(source, {"value": "{% increment injected %}", "step": 2})
				if "inputs.value" in source:
					self.assertIn("{% increment injected %}", actual)

	def testDeferredSiblingDefaultsStayOrderedInsideTheSelectedBranch(self):
		file = self.template({"title": "Deferred sibling contracts"},
			"## Process\n{% if inputs.enabled %}\n@./first.md\n@./second.md\n{% endif %}\n@./fallback.md\n")
		(file.parent / "first.md").write_text(documentText({"output": {"template": "\\First"}}, "First instructions"))
		(file.parent / "second.md").write_text(documentText({"output": {"template": "\\Second"}}, "Second instructions"))
		(file.parent / "fallback.md").write_text(documentText({"output": {"template": "\\Fallback"}}, "Fallback instructions"))
		value = self.elaboration(file)
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		ElaborationApply(self.ctx, self.plan, value, "deferred-sibling-order")
		for enabled in (True, False):
			context = self.ctx.derive(inputs={"enabled": enabled})
			expected = prepare(context, Script(file.read_text().split("## Process\n", 1)[1], file))
			actual = prepare(context, TaskGet(context, "work").script())
			self.assertEqual(actual["output"], expected["output"])
			self.assertEqual(actual["prompt"], expected["prompt"])
		for path in (file, file.parent / "first.md", file.parent / "second.md", file.parent / "fallback.md"):
			path.unlink()
		for enabled, outline in ((True, "First"), (False, "Fallback")):
			request = prepare(self.ctx.derive(inputs={"enabled": enabled}), TaskGet(self.ctx, "work").script())
			self.assertEqual(request["output"]["template"], outline)


# EOF
