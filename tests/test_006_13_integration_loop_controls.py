"""Parameter loop control selects structure while runtime loop control stays live."""

import unittest

from auto.features.elaboration.operations import ElaborationApply, ElaborationValidate
from auto.features.elaboration.templates import ParameterTemplate
from auto.features.script.model import Script
from auto.features.script.templates import Renderer
from auto.features.task.operations import TaskGet, effectiveTask
from auto.utils.data import documentText
import test_006_04_integration_materialization as fixtures


class LoopControlTests(unittest.TestCase):
	setUp = fixtures.MaterializationTests.setUp
	template = fixtures.MaterializationTests.template
	elaboration = fixtures.MaterializationTests.elaboration

	def testKnownLoopControlsCompileAndRetainRuntimeOutput(self):
		params = {"names": ["one", "skip", "two", "stop", "three"]}
		controls = (
			"{% if name == 'skip' %}{% continue %}{% endif %}{% if name == 'stop' %}{% break %}{% endif %}",
			"{% case name %}{% when 'skip' %}{% continue %}{% when 'stop' %}{% break %}{% endcase %}",
			"{% unless name != 'skip' %}{% continue %}{% endunless %}{% if forloop.index == 4 %}{% break %}{% endif %}",
		)
		for control in controls:
			with self.subTest(control=control):
				source = "{% for name in params.names %}" + control + "{{ name }}={{ inputs.value }};{% endfor %}"
				compiler = ParameterTemplate(params)
				compiled = compiler.expand(source)
				self.assertNotIn("{% for ", compiled)
				context = self.ctx.derive(inputs={"value": "runtime"})
				self.assertEqual(Renderer(context, Script(compiled, self.root / "compiled.md",
					{"params": params, "_template_bindings": compiler.bindings})).render(),
					Renderer(context, Script(source, self.root / "original.md", {"params": params})).render())

	def testRuntimeControlledBreakAndContinueRemainInsideTheirLoops(self):
		params = {"names": ["one", "two", "three"]}
		for command in ("break", "continue"):
			for enabled in (True, False):
				with self.subTest(command=command, enabled=enabled):
					source = "{% for name in params.names %}{% if inputs.enabled and name == 'two' %}{% " + command + " %}{% endif %}{{ name }};{% endfor %}"
					compiler = ParameterTemplate(params)
					compiled = compiler.expand(source)
					self.assertIn("{% for ", compiled)
					context = self.ctx.derive(inputs={"enabled": enabled})
					self.assertEqual(Renderer(context, Script(compiled, self.root / "compiled.md",
						{"params": params, "_template_bindings": compiler.bindings})).render(),
						Renderer(context, Script(source, self.root / "original.md", {"params": params})).render())

	def testParameterLoopControlCanSelectTaskSections(self):
		file = self.template({"title": "Loop sections", "params": {"sections": ["Unused", "Process", "Post", "Process"]}},
			"{% for heading in params.sections %}\n{% if heading == 'Unused' %}{% continue %}{% endif %}\n"
			"{% if forloop.index > 3 %}{% break %}{% endif %}\n## {{ heading }}\n@./{{ heading }}.md\n{% endfor %}\n")
		for heading in ("Process", "Post"):
			(file.parent / (heading + ".md")).write_text(heading + " content")
		value = self.elaboration(file)
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		ElaborationApply(self.ctx, self.plan, value, "loop-task-sections")
		for heading in ("Process", "Post"):
			(file.parent / (heading + ".md")).unlink()
		task = TaskGet(self.ctx, "work")
		self.assertEqual(set(task.sections), {"Process", "Post"})
		self.assertEqual(Renderer(self.ctx, task.script()).render().strip(), "Process content")
		self.assertEqual(Renderer(self.ctx, task.script("Post")).render().strip(), "Post content")

	def testParameterLoopControlCanSelectArtifactSections(self):
		file = self.template({"title": "Loop artifact", "artifact": "./artifact.md",
			"params": {"sections": ["Unused", "Post", "Evaluation", "Post"]}}, "## Process\nProduce\n")
		artifact = file.parent / "artifact.md"
		artifact.write_text(documentText({}, "{% for heading in params.sections %}\n"
			"{% if heading == 'Unused' %}{% continue %}{% endif %}\n"
			"{% if forloop.index > 3 %}{% break %}{% endif %}\n## {{ heading }}\n"
			"{{ heading }} {{ inputs.value }}\n{% endfor %}\n"))
		value = self.elaboration(file)
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		ElaborationApply(self.ctx, self.plan, value, "loop-artifact-sections")
		artifact.unlink()
		task = effectiveTask(self.ctx, TaskGet(self.ctx, "work"))
		context = self.ctx.derive(inputs={"value": "runtime"})
		self.assertEqual(Renderer(context, task.script("Post")).render().strip(), "Post runtime")
		self.assertEqual(Renderer(context, task.script("Evaluation")).render().strip(), "Evaluation runtime")

	def testNestedLoopExitsRetainTheirOwnLoopAndParentCounters(self):
		params = {"names": ["one", "skip", "two", "stop", "three"], "values": ["a", "b", "c"]}
		source = "{% for name in params.names %}{% if name == 'skip' %}{% continue %}{% endif %}" \
			"{% if name == 'stop' %}{% break %}{% endif %}{% for value in params.values %}" \
			"{% if value == 'b' %}{% continue %}{% endif %}{% if forloop.index > 2 %}{% break %}{% endif %}" \
			"{{ forloop.parentloop.index }}.{{ forloop.index }}:{{ name }}-{{ value }}={{ inputs.value }};{% endfor %}{% endfor %}"
		compiler = ParameterTemplate(params)
		compiled = compiler.expand(source)
		self.assertNotIn("{% for ", compiled)
		context = self.ctx.derive(inputs={"value": "runtime"})
		self.assertEqual(Renderer(context, Script(compiled, self.root / "compiled.md",
			{"params": params, "_template_bindings": compiler.bindings})).render(), "1.1:one-a=runtime;3.1:two-a=runtime;")

	def testRuntimeAlternativesAndAssignmentsDoNotHoistLoopExits(self):
		params = {"names": ["one", "two", "three"]}
		controls = (
			"{% assign stop = false %}{% for name in params.names %}{% assign stop = inputs.enabled %}{% if stop %}{% break %}{% endif %}{{ name }};{% endfor %}",
			"{% for name in params.names %}{% if name == 'one' %}First;{% elsif inputs.enabled %}{% break %}{% endif %}{{ name }};{% endfor %}",
			"{% for name in params.names %}{% case name %}{% when inputs.stop %}{% continue %}{% endcase %}{{ name }};{% endfor %}",
			"{% for name in params.names %}{% capture text %}{% if inputs.enabled %}{% break %}{% endif %}Captured;{% endcapture %}{{ text }}{{ name }};{% endfor %}",
		)
		for source in controls:
			for enabled in (True, False):
				with self.subTest(source=source, enabled=enabled):
					compiler = ParameterTemplate(params)
					compiled = compiler.expand(source)
					self.assertIn("{% for ", compiled)
					context = self.ctx.derive(inputs={"enabled": enabled, "stop": "two"})
					self.assertEqual(Renderer(context, Script(compiled, self.root / "compiled.md",
						{"params": params, "_template_bindings": compiler.bindings})).render(),
						Renderer(context, Script(source, self.root / "original.md", {"params": params})).render())
