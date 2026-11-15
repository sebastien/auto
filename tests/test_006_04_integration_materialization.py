"""Application-time parameter structure and runtime data/effect boundaries."""

import unittest
import fcntl
import shlex
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

from auto.features.elaboration.materialize import materialize
from auto.features.elaboration.model import createElaborationFromDict
from auto.features.elaboration.operations import ElaborationApply, ElaborationValidate
from auto.features.elaboration.templates import ParameterTemplate
from auto.features.script.model import Script
from auto.features.script.templates import Renderer
from auto.features.script.operations import ScriptInputs, prepare
from auto.features.task.operations import RunCheck, TaskDependencies, TaskExpand, TaskGet, TaskStart, effectiveTask
from auto.features.plan.operations import PlanRevise, PlanStatus
from auto.features.plan.gaps import PlanRepair
from auto.features.plan.store import journal
from auto.runtime.operations import Resolve, Value
from auto.runtime.model import Content
from auto.utils.data import AutoError, documentText, digest
import test_004_01_integration_planning as fixtures


class MaterializationTests(unittest.TestCase):
	setUp = fixtures.PlanningTests.setUp

	def template(self, meta, body):
		path = self.root / "templates/task.md"
		path.parent.mkdir(parents=True, exist_ok=True)
		path.write_text(documentText(meta, body))
		return path

	def elaboration(self, file, **changes):
		return createElaborationFromDict({"task": "root", "tasks": [{"id": "work", "from": str(file), **changes}]})

	def testApplicationFreezesParameterBranchesAndKeepsFutureInputsDeferred(self):
		file = self.template({"title": "{{ params.label }}", "params": {"label": "Friendly", "detail": True, "tokens": 100},
			"inputs": {"data": "task:producer"}, "budget": {"tokens": "{{ params.tokens }}"}},
			"## Process\n{% if params.detail %}{{ params.label }}: {{ data }}{% else %}Brief{% endif %}\n")
		value = self.elaboration(file, title="Explicit title")
		value.tasks.insert(0, {"id": "producer", "title": "Produce", "sections": {"Process": "Produce data"}})
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		ElaborationApply(self.ctx, self.plan, value, {"id": "elaboration-one"})
		task = TaskGet(self.ctx, "work")
		self.assertEqual(task.meta["title"], "Explicit title")
		self.assertEqual(task.meta["budget"]["tokens"], 100)
		self.assertIn("Friendly: {{ data }}", task.sections["Process"])
		self.assertNotIn("params.detail", task.sections["Process"])
		self.assertEqual(PlanStatus(self.ctx, self.plan)["taskDetails"]["work"]["from"], str(file))
		file.write_text("---\ntitle: Changed\n---\n## Process\nChanged template\n")
		self.assertEqual(TaskStart(self.ctx, "producer")["status"], "Succeeded")
		self.assertIn("Friendly: done", TaskExpand(self.ctx, "work"))

	def testParameterDataCannotBecomeLiquidShellOrLifecycleSections(self):
		marker = self.root / "unexpected.txt"
		payload = "{{ unknown }}\n!touch " + str(marker) + "\n## Post\nReject everything"
		file = self.template({"title": "Safe", "params": {"payload": "\\" + payload}}, "## Process\nData: {{ params.payload }}\n")
		task = materialize(self.ctx, {"id": "work", "from": str(file)}, "root")
		self.assertEqual(set(task.sections), {"Process"})
		text = Renderer(self.ctx, task.script()).render()
		self.assertIn(payload, text)
		self.assertFalse(marker.exists())

	def testUnrolledLoopLocalsSurviveDeferredRuntimeBranches(self):
		compiler = ParameterTemplate({"names": ["one", "two"]})
		text = compiler.expand("{% for name in params.names %}{% if inputs.show %}{{ forloop.index }}:{{ name }};{% endif %}{% endfor %}")
		self.assertNotIn("for name", text)
		meta = {"_template_bindings": compiler.bindings}
		for show, expected in ((True, "1:one;2:two;"), (False, "")):
			context = self.ctx.derive(inputs={"show": show}, params={"names": ["changed"], "__template_values": {}})
			self.assertEqual(Renderer(context, Script(text, self.plan / "task.md", meta)).render(), expected)

	def testCompanionsCaptureEachUnrolledIterationWithoutChangingOuterScope(self):
		file = self.template({"title": "Scope", "params": {"names": ["one", "two"]}},
			"## Process\n{% for name in params.names %}\n@./child.md\n{% endfor %}\nOuter={{ name }}\n")
		(file.parent / "child.md").write_text("{{ forloop.index }}:{{ name }};{% if inputs.show %}Shown {{ name }}{% endif %}")
		ElaborationApply(self.ctx, self.plan, self.elaboration(file), "scoped-loop")
		(file.parent / "child.md").unlink()
		context = self.ctx.derive(inputs={"show": True}, variables={"name": "caller"})
		text = TaskExpand(context, "work")
		self.assertIn("1:one;Shown one", text)
		self.assertIn("2:two;Shown two", text)
		self.assertIn("Outer=caller", text)
		self.assertEqual(TaskDependencies(self.ctx, TaskGet(self.ctx, "work")), [])
		self.assertEqual(context.variables, {"name": "caller"})

	def testLateSelectedCompanionsInheritCapturedParentLocals(self):
		file = self.template({"title": "Late scope", "params": {"names": ["one", "two"]}},
			"## Process\n{% for name in params.names %}\n@./child.md\n{% endfor %}\n")
		(file.parent / "child.md").write_text("@./{{ inputs.which }}.md\n")
		task = materialize(self.ctx, {"id": "work", "from": str(file)}, "root")
		(file.parent / "child.md").unlink()
		(file.parent / "future.md").write_text("{{ name }}/{{ forloop.index }}:{{ inputs.value }}")
		context = self.ctx.derive(inputs={"which": "future", "value": "Data"})
		text = Renderer(context, task.script()).render()
		self.assertIn("one/1:Data", text)
		self.assertIn("two/2:Data", text)

	def testLoopsInsideCompanionsRetainTheParentLoopAndDoNotLeakAssignments(self):
		file = self.template({"title": "Nested loops", "params": {"names": ["one", "two"], "values": ["a", "b"]}},
			"## Process\n{% for name in params.names %}\n@./child.md\n{% endfor %}\n{{ name }}\n")
		(file.parent / "child.md").write_text("{% for value in params.values %}{{ forloop.parentloop.index }}.{{ forloop.index }}:{{ name }}-{{ value }};{% endfor %}{% if inputs.change %}{% assign name = inputs.value %}{% endif %}Final={{ name }}")
		task = materialize(self.ctx, {"id": "work", "from": str(file)}, "root")
		context = self.ctx.derive(inputs={"change": True, "value": "local"}, variables={"name": "caller"})
		text = Renderer(context, task.script()).render()
		self.assertIn("1.1:one-a;1.2:one-b;Final=local", text)
		self.assertIn("2.1:two-a;2.2:two-b;Final=local", text)
		self.assertTrue(text.rstrip().endswith("caller"))

	def testNestedParameterBranchesAreCompiledBeforeDependencyDiscovery(self):
		file = self.template({"title": "Nested params"}, "## Process\n@./child.md\n")
		(file.parent / "child.md").write_text(documentText({"params": {"enabled": True}},
			"{% if params.enabled %}Selected{% else %}\n@./missing.md\n{{ inputs.unused }}{% endif %}\n"))
		value = self.elaboration(file)
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		ElaborationApply(self.ctx, self.plan, value, "nested-branch")
		task = TaskGet(self.ctx, "work")
		self.assertNotIn("unused", ScriptInputs(self.ctx, task.script())["inputs"])
		self.assertIn("Selected", TaskExpand(self.ctx, "work"))

	def testScopedMetadataPreservesDifferentTaskDependenciesForTheSameBody(self):
		file = self.template({"title": "Scoped inputs", "params": {"names": ["a", "b"]}},
			"## Process\n{% for name in params.names %}\n@./child.md\n{% endfor %}\n")
		(file.parent / "child.md").write_text(documentText({"inputs": {"selected": "task:{{ name }}"}}, "{{ selected }}"))
		value = self.elaboration(file)
		value.tasks[0:0] = [{"id": name, "title": name, "sections": {"Process": "Produce " + name}} for name in ("a", "b")]
		ElaborationApply(self.ctx, self.plan, value, "scoped-inputs")
		self.assertEqual(TaskDependencies(self.ctx, TaskGet(self.ctx, "work")), ["a", "b"])
		self.engine.outputs = iter(["Output A", "Output B"])
		TaskStart(self.ctx, "a")
		TaskStart(self.ctx, "b")
		text = TaskExpand(self.ctx, "work")
		self.assertIn("Output A", text)
		self.assertIn("Output B", text)

	def testCompanionMetadataCanCaptureTypedLoopValues(self):
		file = self.template({"title": "Typed loop", "params": {"names": ["one", "two"]}},
			"## Process\n{% for name in params.names %}\n@./child.md\n{% endfor %}\n")
		(file.parent / "child.md").write_text(documentText({"inputs": {"loop": "{{ forloop }}"}}, "{{ inputs.loop.index }}"))
		task = materialize(self.ctx, {"id": "work", "from": str(file)}, "root")
		text = Renderer(self.ctx, task.script()).render()
		self.assertIn("1", text)
		self.assertIn("2", text)

	def testCompiledSourceReferencesResolveAfterOriginalDeletionAndTaskRemoval(self):
		file = self.template({"title": "Owned source", "params": {"name": "Stored"}}, "## Process\n@./child.md\n")
		(file.parent / "child.md").write_text("{{ params.name }}")
		ElaborationApply(self.ctx, self.plan, self.elaboration(file), "owned-source")
		task = TaskGet(self.ctx, "work")
		ref = next(ref for ref in task.meta["_template_sources"] if "/task/work/source/" in ref)
		(file.parent / "child.md").unlink()
		self.assertIn("Stored", Resolve(self.ctx, ref).text)
		PlanRevise(self.ctx, self.plan, [{"op": "remove", "task": "work"}], "Remove unused task")
		self.assertIn("Stored", Resolve(self.ctx, ref).text)

	def testAssignmentsCapturesAndRuntimeMutationKeepTheirScopes(self):
		compiler = ParameterTemplate({"label": "Original"})
		text = compiler.expand("{% capture x %}{{ params.label }}{% endcapture %}{% if inputs.change %}{% assign x = inputs.value %}{% endif %}{{ x }}")
		for change, expected in ((True, "Replacement"), (False, "Original")):
			context = self.ctx.derive(inputs={"change": change, "value": "Replacement"})
			self.assertEqual(Renderer(context, Script(text, self.plan / "task.md", {"_template_bindings": compiler.bindings})).render(), expected)

	def testDeadParameterBranchDoesNotResolveItsTransclusion(self):
		file = self.template({"title": "Choose", "params": {"extra": False}}, "## Process\n{% if params.extra %}\n@./missing.md\n{% endif %}\nBase\n")
		value = self.elaboration(file)
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		ElaborationApply(self.ctx, self.plan, value, "elaboration-dead")
		self.assertNotIn("missing.md", TaskGet(self.ctx, "work").sections["Process"])

	def testCaptureDataAndBlankControlAssignmentsPreserveRuntimeBehavior(self):
		params = {"label": "{{ unknown }}", "flag": True}
		for source in (
			"{% capture x %}{{ params.label }}{% endcapture %}{{ x }}",
			"{% capture x %}{% assign y = params.label %}Text{% endcapture %}{% if inputs.show %}{% assign y = inputs.value %}{% endif %}{{ y }}",
			"{% if params.flag %}  {% assign x = params.label %}{% endif %}{% if inputs.show %}{% assign x = inputs.value %}{% endif %}{{ x }}",
		):
			for show in (True, False):
				with self.subTest(source=source, show=show):
					context = self.ctx.derive(params={"label": "\\" + params["label"], "flag": True}, inputs={"show": show, "value": "Replacement"})
					expected = Renderer(context, Script(source, self.plan / "source.md")).render()
					compiler = ParameterTemplate(params)
					compiled = compiler.expand(source)
					actual = Renderer(context, Script(compiled, self.plan / "compiled.md", {"_template_bindings": compiler.bindings})).render()
					self.assertEqual(actual, expected)

	def testFileAndShellParameterValuesAreResolvedOnceAtApplication(self):
		count = self.root / "count.txt"
		parameter = "!printf x >> " + str(count) + "; printf Detailed"
		file = self.template({"title": "Param values", "params": {"label": parameter, "note": "./note.txt"}}, "## Process\n{% if params.label == 'Detailed' %}Detailed{% else %}Brief{% endif %}: {{ params.label }}; {{ params.note }}\n")
		note = file.parent / "note.txt"
		note.write_text("Reference note")
		value = self.elaboration(file)
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		self.assertFalse(count.exists())
		revision = ElaborationApply(self.ctx, self.plan, value, "elaboration-shell")
		self.assertEqual(count.read_text(), "x")
		self.assertIn(str(note), [pin["ref"] for pin in revision["context"]])
		self.assertIn("Detailed: Detailed", TaskExpand(self.ctx, "work"))
		ElaborationApply(self.ctx, self.plan, value, "elaboration-shell")
		self.assertEqual(count.read_text(), "x")

	def testShellTransclusionsRemainRuntimeAndQuoteParameterData(self):
		marker = self.root / "unexpected.txt"
		message = "safe; touch " + str(marker)
		file = self.template({"title": "Quote", "params": {"message": message}}, "## Process\n!printf '%s' {{ params.message }}\n")
		task = materialize(self.ctx, {"id": "work", "from": str(file)}, "root")
		self.assertFalse(marker.exists())
		text = Renderer(self.ctx, task.script()).render()
		self.assertIn(message, text)
		self.assertFalse(marker.exists())

	def testRawAndFencedExamplesKeepTheirLiteralMeaning(self):
		compiler = ParameterTemplate({"label": "Value"})
		text = compiler.expand("{% raw %}{{ unknown }}{% endraw %}\n```text\n{{!touch never}}\n{{ params.label }}\n```\n{{ params.label }}")
		output = Renderer(self.ctx, Script(text, self.plan / "task.md", {"_template_bindings": compiler.bindings})).render()
		self.assertIn("{{ unknown }}", output)
		self.assertIn("{{!touch never}}", output)
		self.assertIn("{{ params.label }}", output)
		self.assertTrue(output.endswith("Value"))

	def testCaseSelectionExpandsBeforeRuntimeInputIsAvailable(self):
		compiler = ParameterTemplate({"mode": "full"})
		text = compiler.expand("{% case params.mode %}{% when 'full' %}Full {{ inputs.value }}{% else %}Brief{% endcase %}")
		self.assertEqual(text, "Full {{ inputs.value }}")

	def testUnselectedConditionsDoNotRequireMissingParameters(self):
		compiler = ParameterTemplate({"flag": True})
		self.assertEqual(compiler.expand("{% if params.flag %}Selected{% elsif params.missing %}Other{% endif %}"), "Selected")
		compiler = ParameterTemplate({"flag": False})
		self.assertEqual(compiler.expand("{% if params.flag and params.missing %}Other{% else %}Selected{% endif %}"), "Selected")

	def testParameterConditionsSelectLifecycleSectionsBeforeOverrides(self):
		file = self.template({"title": "Sections", "params": {"check": True}},
			"## Process\nTemplate process\n{% if params.check %}\n## Post\nTemplate check\n{% endif %}\n")
		task = materialize(self.ctx, {"id": "work", "from": str(file), "sections": {"Process": "Explicit process"}}, "root")
		self.assertEqual(task.sections["Process"], "Explicit process")
		self.assertEqual(task.sections["Post"], "Template check")

	def testShellDependentLifecycleSectionsAreSelectedBeforeCommit(self):
		counter, checked = self.root / "section-count.txt", self.root / "checked.txt"
		file = self.template({"title": "Conditional lifecycle", "params": {"mode": "!printf x >> " + str(counter) + "; printf checked"}},
			"{% if params.mode == 'checked' %}\n## Process\nChosen process\n## Post\n!?printf checked >> " + str(checked) +
			"\n{% else %}\n## Process\nOther process\n## Evaluation\nOther evaluation\n{% endif %}\n")
		value = self.elaboration(file)
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		self.assertFalse(counter.exists())
		ElaborationApply(self.ctx, self.plan, value, "conditional-lifecycle")
		task = TaskGet(self.ctx, "work")
		self.assertEqual(set(task.sections), {"Process", "Post"})
		self.assertEqual(task.sections["Process"], "Chosen process")
		self.assertEqual(counter.read_text(), "x")
		self.assertFalse(checked.exists())
		self.assertEqual(TaskStart(self.ctx, "work")["status"], "Succeeded")
		self.assertEqual(checked.read_text(), "checked")
		ElaborationApply(self.ctx, self.plan, value, "conditional-lifecycle")
		self.assertEqual(counter.read_text(), "x")

	def testUnknownLifecycleControlsPreserveSelectedScopesAndExplicitOverrides(self):
		for id, body, params, result, expected in (
			("case", "{% case params.mode %}{% when 'ready' %}\n## Process\nSelected\n{% else %}\n## Post\nOther\n{% endcase %}",
				{"mode": "!printf ready"}, "ready", {"Process"}),
			("unless", "## Process\nOriginal\n{% unless params.mode == 'skip' %}\n## Post\n!?true\n{% endunless %}",
				{"mode": "!printf check"}, "check", {"Process", "Post"}),
			("loop", "{% assign names = params.names | split: ',' %}{% for name in names %}\n## {{ name }}\nSection {{ name }}\n{% endfor %}",
				{"names": "!printf Process,Post"}, "Process,Post", {"Process", "Post"}),
			("assigned", "{% assign section = params.heading %}\n## {{ section }}\nChosen\n",
				{"heading": "!printf Process"}, "Process", {"Process"})):
			with self.subTest(id=id):
				file = self.template({"title": "Lifecycle controls", "params": params}, body)
				value = self.elaboration(file, id=id, sections={"Process": "Explicit process"})
				with patch("auto.integration.process.capture", return_value=(result, "", 0)) as shell:
					self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
					self.assertEqual(shell.call_count, 0)
					ElaborationApply(self.ctx, self.plan, value, "controls-" + id)
					self.assertEqual(shell.call_count, 1)
				task = TaskGet(self.ctx, id)
				self.assertEqual(set(task.sections), expected)
				self.assertEqual(task.sections["Process"], "Explicit process")

	def testDynamicLifecycleHeadingCanBeValidatedWithoutEvaluatingItsShellValue(self):
		file = self.template({"title": "Dynamic heading", "params": {"suffix": "!printf cess"}}, "## Pro{{ params.suffix }}\nChosen\n")
		value = self.elaboration(file)
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		ElaborationApply(self.ctx, self.plan, value, "dynamic-heading")
		self.assertEqual(TaskGet(self.ctx, "work").sections, {"Process": "Chosen"})

	def testConditionalBranchesDoNotResolveUnselectedFilesOrDependenciesDuringPreflight(self):
		file = self.template({"title": "Conditional grounding", "params": {"mode": "!printf safe"}},
			"{% if params.mode == 'unsafe' %}\n## Process\n@./missing.md\n@task:missing\n{% else %}\n## Process\nSafe\n{% endif %}\n")
		value = self.elaboration(file)
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		ElaborationApply(self.ctx, self.plan, value, "conditional-grounding")
		self.assertEqual(TaskDependencies(self.ctx, TaskGet(self.ctx, "work")), [])
		self.assertIn("Safe", TaskExpand(self.ctx, "work"))

	def testKnownLifecycleErrorsStillFailBeforeShellDependentBranchesResolve(self):
		branch = "{% if params.mode == 'checked' %}\n## Post\n!?true\n{% endif %}\n"
		for body, message in (("No process\n" + branch, "requires Process"),
			("## Process\nWork\n## Unexpected\nInvalid\n" + branch, "Unknown task sections"),
			("## Process\nWork\n## Process\nDuplicate\n" + branch, "Duplicate section"),
			("## Process\n@./missing.md\n" + branch, "Cannot resolve"),
			("## Process\n@task:work\n" + branch, "Dependency cycle")):
			with self.subTest(message=message):
				file = self.template({"title": "Known error", "params": {"mode": "!printf checked"}}, body)
				value = self.elaboration(file)
				with patch("auto.integration.process.capture") as shell:
					self.assertIn(message, str(ElaborationValidate(self.ctx, self.plan, value)))
					with self.assertRaisesRegex(AutoError, message):
						ElaborationApply(self.ctx, self.plan, value, "known-section-error")
					self.assertEqual(shell.call_count, 0)

	def testResolvedInvalidLifecycleStructuresCannotBeCommitted(self):
		for body, message in (("{% if params.mode == 'valid' %}\n## Process\nWork\n{% endif %}\n", "requires Process"),
			("## Process\nWork\n{% if params.mode == 'invalid' %}\n## Unexpected\nInvalid\n{% endif %}\n", "Unknown task sections"),
			("## Process\nWork\n{% if params.mode == 'invalid' %}\n## Process\nDuplicate\n{% endif %}\n", "Duplicate section"),
			("## Process\nWork\n{% if params.mode == 'invalid' %}\n@./missing.md\n{% endif %}\n", "Cannot resolve"),
			("## Process\nWork\n{% if params.mode == 'invalid' %}\n@task:work\n{% endif %}\n", "Dependency cycle")):
			with self.subTest(message=message):
				file = self.template({"title": "Resolved error", "params": {"mode": "!printf invalid"}}, body)
				value = self.elaboration(file)
				with patch("auto.integration.process.capture", return_value=("invalid", "", 0)) as shell:
					self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
					with self.assertRaisesRegex(AutoError, message):
						ElaborationApply(self.ctx, self.plan, value, "resolved-section-error")
					self.assertEqual(shell.call_count, 1)
				self.assertFalse((self.plan / "tasks/work.md").exists())
				self.assertEqual(journal(self.plan), [])

	def testCompanionShellBranchesDelayTheirFileSelectionUntilApplication(self):
		file = self.template({"title": "Nested selection"}, "## Process\n@./child.md\n")
		(file.parent / "child.md").write_text(documentText({"params": {"mode": "!printf safe"}},
			"{% if params.mode == 'unsafe' %}\n@./missing.md\n{% else %}\nSelected companion\n{% endif %}\n"))
		value = self.elaboration(file)
		with patch("auto.integration.process.capture", return_value=("safe", "", 0)) as shell:
			self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
			self.assertEqual(shell.call_count, 0)
			ElaborationApply(self.ctx, self.plan, value, "nested-selected-branch")
			self.assertEqual(shell.call_count, 1)
		(file.parent / "child.md").unlink()
		self.assertIn("Selected companion", TaskExpand(self.ctx, "work"))

	def testMetadataShellCommandsKeepRuntimeQuoting(self):
		marker = self.root / "unexpected.txt"
		message = "safe; touch " + str(marker)
		file = self.template({"title": "Commands", "params": {"message": message}, "post": "printf '%s' {{ params.message }}"}, "## Process\nWork\n")
		task = materialize(self.ctx, {"id": "work", "from": str(file)}, "root")
		renderer = Renderer(self.ctx, task.script())
		command = renderer.shellArgument(task.meta["post"], renderer.bindings())
		self.assertIn("'" + message + "'", command)
		self.assertFalse(marker.exists())

	def testCaptureDoesNotTurnRuntimeTransclusionIntoLiteralText(self):
		file = self.template({"title": "Capture"}, "## Process\n{% capture x %}\n!printf captured\n{% endcapture %}{{ x }}\n")
		task = materialize(self.ctx, {"id": "work", "from": str(file)}, "root")
		self.assertIn("capture x", task.sections["Process"])
		self.assertIn("captured", Renderer(self.ctx, task.script()).render())

	def testSourceRelativeFileParametersAndReferencesKeepTheirOrigins(self):
		file = self.template({"title": "Origins", "params": {"label": "./label.txt", "filename": "fragment.md"}},
			"## Process\n{{ params.label }}\n@./parts/{{ params.filename }}\n")
		(file.parent / "label.txt").write_text("Stored label")
		(file.parent / "parts").mkdir()
		(file.parent / "parts/fragment.md").write_text("Source fragment")
		task = materialize(self.ctx, {"id": "work", "from": str(file)}, "root")
		(file.parent / "label.txt").write_text("Changed label")
		text = Renderer(self.ctx, task.script()).render()
		self.assertIn("Stored label", text)
		self.assertIn("Source fragment", text)

	def testAppliedCompanionsSurviveChangesDeletionAndRepair(self):
		file = self.template({"title": "Sources", "params": {"names": ["one", "two"]}},
			"## Process\n{% for name in params.names %}\n@./parts/{{ name }}.md\n{% endfor %}\n")
		parts = file.parent / "parts"
		parts.mkdir()
		(parts / "one.md").write_text("First {{ inputs.value }}\n@./nested.md\n")
		(parts / "two.md").write_text("Second\n{{@./literal.txt}}\n")
		(parts / "nested.md").write_text("Nested original")
		(parts / "literal.txt").write_text("{{ untouched }}")
		ElaborationApply(self.ctx, self.plan, self.elaboration(file), "sources-application")
		for companion in parts.iterdir():
			companion.unlink()
		file.write_text("Changed template")
		task = TaskGet(self.ctx, "work")
		self.assertIn("value", ScriptInputs(self.ctx, task.script())["inputs"])
		context = self.ctx.derive(inputs={"value": "Filled"})
		text = TaskExpand(context, "work")
		self.assertIn("First Filled", text)
		self.assertIn("Nested original", text)
		self.assertIn("Second", text)
		self.assertIn("{{ untouched }}", text)
		PlanRepair(self.ctx, self.plan)
		self.assertEqual(TaskExpand(context, "work"), text)
		self.assertEqual(TaskStart(context, "work")["status"], "Succeeded")

	def testCompanionParametersAndContractsUseCapturedSources(self):
		file = self.template({"title": "Contracts", "output": {"format": "json", "schema": "./schema.json"}}, "## Process\n@./child.md\n")
		(file.parent / "schema.json").write_text('{"type":"object","required":["original"]}')
		(file.parent / "child.md").write_text(documentText({"params": {"label": "./label.txt"}}, "Label: {{ params.label }}\n@./deep.md\n"))
		(file.parent / "label.txt").write_text("Original label")
		(file.parent / "deep.md").write_text("Deep original")
		task = materialize(self.ctx, {"id": "work", "from": str(file)}, "root")
		(file.parent / "schema.json").unlink()
		(file.parent / "label.txt").write_text("Changed label")
		(file.parent / "deep.md").unlink()
		request = prepare(self.ctx.derive(params={"label": "Caller replacement"}), task.script())
		self.assertIn("Label: Original label", request["prompt"])
		self.assertIn("Deep original", request["prompt"])
		self.assertEqual(request["output"]["schema"]["required"], ["original"])

	def testCompanionSnapshotDoesNotFreezeRuntimeTaskInputs(self):
		file = self.template({"title": "Live inputs", "inputs": {"data": "./data.txt"}}, "## Process\n@./child.md\n")
		(file.parent / "child.md").write_text("Value: {{ data }}")
		(file.parent / "data.txt").write_text("Initial")
		task = materialize(self.ctx, {"id": "work", "from": str(file)}, "root")
		(file.parent / "data.txt").write_text("Current")
		(file.parent / "child.md").write_text("Changed companion")
		self.assertIn("Value: Current", prepare(self.ctx, task.script())["prompt"])

	def testCompanionShellCommandsAreNotExecutedDuringSnapshot(self):
		marker = self.root / "shell-count.txt"
		file = self.template({"title": "Shell companion"}, "## Process\n@./child.md\n")
		(file.parent / "child.md").write_text("!printf x >> " + str(marker) + "; printf Captured\n")
		task = materialize(self.ctx, {"id": "work", "from": str(file)}, "root")
		self.assertFalse(marker.exists())
		(file.parent / "child.md").unlink()
		self.assertIn("Captured", Renderer(self.ctx, task.script()).render())
		self.assertEqual(marker.read_text(), "x")

	def testCompanionCycleAndSnapshotSizeFailBeforePlanCommit(self):
		file = self.template({"title": "Cycle"}, "## Process\n@./child.md\n")
		(file.parent / "child.md").write_text("@./child.md\n")
		violations = ElaborationValidate(self.ctx, self.plan, self.elaboration(file))
		self.assertIn("Recursive template source", str(violations))
		self.assertFalse((self.plan / "tasks/work.md").exists())
		(file.parent / "child.md").write_text("x" * 150)
		(file.parent / "other.md").write_text("y" * 150)
		file = self.template({"title": "Size"}, "## Process\n@./child.md\n@./other.md\n")
		with self.assertRaisesRegex(AutoError, "snapshots exceed byte limit"):
			materialize(self.ctx.derive(maxBytes=256), {"id": "work", "from": str(file)}, "root")

	def testCompanionSelectorsAndSymlinkOriginsStayStable(self):
		file = self.template({"title": "Selectors"}, "## Process\n@./link.md#First\n@./link.md#Second\n@./nested.md\n")
		(file.parent / "original.md").write_text("## First\nFirst {{ inputs.first }}\n## Second\nSecond {{ inputs.second }}\n")
		(file.parent / "replacement.md").write_text("Changed")
		(file.parent / "link.md").symlink_to("original.md")
		(file.parent / "nested.md").write_text("@./link.md#First\n")
		task = materialize(self.ctx, {"id": "work", "from": str(file)}, "root")
		(file.parent / "link.md").unlink()
		(file.parent / "link.md").symlink_to("replacement.md")
		(file.parent / "original.md").unlink()
		self.assertEqual(ScriptInputs(self.ctx, task.script())["inputs"], ["first", "second"])
		text = Renderer(self.ctx.derive(inputs={"first": "One", "second": "Two"}), task.script()).render()
		self.assertEqual(text.count("First One"), 2)
		self.assertIn("Second Two", text)
		self.assertNotIn("Changed", text)

	def testStagedRevisionRecoversItsCompanionsWithoutRereadingTemplates(self):
		file = self.template({"title": "Recovery"}, "## Process\n@./child.md\n")
		(file.parent / "child.md").write_text("Original companion")
		value = self.elaboration(file)
		before = len(journal(self.plan))
		with patch("auto.features.plan.store.append", side_effect=OSError("interrupted publication")):
			with self.assertRaisesRegex(OSError, "interrupted publication"):
				ElaborationApply(self.ctx, self.plan, value, "recover-sources")
		self.assertTrue((self.plan / ".pending.json").exists())
		file.unlink()
		(file.parent / "child.md").unlink()
		revision = ElaborationApply(self.ctx, self.plan, value, "recover-sources")
		self.assertEqual(len(journal(self.plan)), before + 1)
		self.assertEqual(revision["elaborationRun"], "recover-sources")
		self.assertIn("Original companion", TaskExpand(self.ctx, "work"))
		self.assertFalse((self.plan / ".pending.json").exists())

	def testQuotedCatalogueAndCompanionPathsPreserveCapturedText(self):
		catalogue = self.root / ".auto/catalogue/check"
		catalogue.mkdir(parents=True)
		(catalogue / "sample.md").write_text("Catalogue original")
		file = self.template({"title": "Quoted sources"}, '## Process\n@"./file with spaces.md"\n@auto:catalogue/check/sample\n')
		(file.parent / "file with spaces.md").write_text("Quoted original")
		task = materialize(self.ctx, {"id": "work", "from": str(file)}, "root")
		(catalogue / "sample.md").unlink()
		(file.parent / "file with spaces.md").unlink()
		text = Renderer(self.ctx, task.script()).render()
		self.assertIn("Quoted original", text)
		self.assertIn("Catalogue original", text)

	def testCorruptSourceSnapshotIsRejectedBeforeUse(self):
		file = self.template({"title": "Integrity"}, "## Process\n@./child.md\n")
		(file.parent / "child.md").write_text("Original")
		task = materialize(self.ctx, {"id": "work", "from": str(file)}, "root")
		next(iter(task.meta["_template_sources"].values()))["text"] = "Corrupt"
		with self.assertRaisesRegex(AutoError, "integrity/size check"):
			Renderer(self.ctx, task.script()).render()

	def testCompanionUsesAppliedParametersWhileRuntimeSelectedFilesStayLive(self):
		file = self.template({"title": "Parameter scope", "params": {"label": "Applied"}},
			"## Process\n@./child.md\n@./{{ inputs.which }}.txt\n")
		(file.parent / "child.md").write_text("Child {{ params.label }}")
		(file.parent / "runtime.txt").write_text("Before")
		task = materialize(self.ctx, {"id": "work", "from": str(file)}, "root")
		(file.parent / "runtime.txt").write_text("Current runtime data")
		context = self.ctx.derive(params={"label": "Replacement"}, inputs={"which": "runtime"})
		text = Renderer(context, task.script()).render()
		self.assertIn("Child Applied", text)
		self.assertIn("Current runtime data", text)

	def testFrameworkSettingsContractsAndChecksSurviveSourceDeletion(self):
		order = self.root / "checks-order.txt"
		file = self.template({"title": "Framework", "profile": "./profile.md", "artifact": "./artifact.md"}, "## Process\nProduce output\n")
		(file.parent / "profile.md").write_text(documentText({"model": "Captured model", "memory": "full", "budget": {"tokens": 200}}, "Profile"))
		(file.parent / "artifact.md").write_text(documentText({"output": {"format": "text", "template": "./outline.txt"}},
			"## Post\n@./checks/post.md\n## Evaluation\n@./checks/evaluation.md\n"))
		(file.parent / "outline.txt").write_text("Captured outline")
		checks = file.parent / "checks"
		checks.mkdir()
		(checks / "post.md").write_text("!?printf artifact >> " + str(order) + "\n")
		(checks / "evaluation.md").write_text("!?printf '%s' '{\"score\":0.5,\"feedback\":\"Captured evaluation\"}'\n")
		value = self.elaboration(file, model="Explicit model", sections={"Post": "!?printf task >> " + str(order)})
		ElaborationApply(self.ctx, self.plan, value, "framework-applied")
		for source in file.parent.rglob("*"):
			if source.is_file():
				source.unlink()
		task = effectiveTask(self.ctx, TaskGet(self.ctx, "work"))
		self.assertEqual(task.meta["model"], "Explicit model")
		self.assertEqual(task.meta["memory"], "full")
		self.assertEqual(task.meta["budget"], {"tokens": 200})
		self.assertEqual(prepare(self.ctx, task.script())["output"]["template"], "Captured outline")
		self.assertFalse(order.exists())
		run = TaskStart(self.ctx, "work")
		self.assertEqual(run["status"], "Succeeded")
		self.assertEqual(order.read_text(), "artifacttask")
		self.assertEqual(run["evaluation"], {"score": 0.5, "feedback": "Captured evaluation"})

	def testFrameworkSnapshotsHonorOverridesAndIgnoreUnusedArtifactProcess(self):
		file = self.template({"title": "Override", "profile": "./unused-profile.md", "artifact": "./unused-artifact.md"}, "## Process\nWork\n")
		selected = self.root / "selected-profile.md"
		selected.write_text(documentText({"model": "Selected"}, "Profile"))
		artifact = self.root / "selected-artifact.md"
		artifact.write_text(documentText({"output": {"format": "json", "schema": "./missing-schema.json"}},
			"## Process\n@./missing-process.md\n{{ inputs.unused }}\n## Post\n!?true\n"))
		value = self.elaboration(file, profile=str(selected), artifact=str(artifact), output={"schema": {"type": "string"}})
		ElaborationApply(self.ctx, self.plan, value, "framework-overrides")
		selected.unlink()
		artifact.unlink()
		task = effectiveTask(self.ctx, TaskGet(self.ctx, "work"))
		self.assertEqual(task.meta["model"], "Selected")
		self.assertEqual(prepare(self.ctx, task.script())["output"]["schema"], {"type": "string"})
		self.assertNotIn("unused", ScriptInputs(self.ctx, task.script())["inputs"])

	def testArtifactChecksCompileLoopsAndShellArgumentsAndRetainRuntimeInputs(self):
		marker, order = self.root / "unexpected.txt", self.root / "order.txt"
		label = "safe; touch " + str(marker)
		file = self.template({"title": "Compiled artifact", "artifact": "./artifact.md", "params": {"names": ["one", "two"], "label": label},
			"inputs": {"review": "./review.txt"}, "post": "printf taskmeta >> " + str(order)}, "## Process\nProduce\n## Post\n!?printf taskbody >> " + str(order) + "\n")
		artifact = file.parent / "artifact.md"
		artifact.write_text(documentText({"post": "printf '%s' {{ params.label }} >> " + str(order)},
			"## Post\n{% for name in params.names %}\n@./checks/post.md\n{% endfor %}\n## Evaluation\n@./checks/evaluation.md\n"))
		checks = file.parent / "checks"
		checks.mkdir()
		(checks / "post.md").write_text("!?printf '%s' {{ name }} >> " + str(order) + "\n")
		(checks / "evaluation.md").write_text("Review: {{ inputs.review }}; label: {{ params.label }}")
		review = file.parent / "review.txt"
		review.write_text("Before application")
		ElaborationApply(self.ctx, self.plan, self.elaboration(file), "compiled-artifact")
		self.assertEqual(TaskGet(self.ctx, "work").meta["artifact"], str(artifact))
		self.assertFalse(order.exists())
		self.assertFalse(marker.exists())
		artifact.unlink()
		for part in checks.iterdir():
			part.unlink()
		review.write_text("Runtime review")
		self.engine.outputs = iter(["Produced", {"score": .75, "feedback": "Reviewed"}])
		run = TaskStart(self.ctx, "work")
		self.assertEqual(run["status"], "Succeeded")
		self.assertEqual(run["evaluation"], {"score": .75, "feedback": "Reviewed"})
		expected = label + "taskmetaonetwotaskbody"
		self.assertEqual(order.read_text(), expected)
		self.assertIn("Runtime review", self.engine.calls[-1])
		self.assertFalse(marker.exists())
		self.assertTrue(RunCheck(self.ctx, run, "Post")["pass"])
		self.assertEqual(order.read_text(), expected * 2)

	def testArtifactDeadBranchesAndOverriddenParameterContractsAreNotRequired(self):
		file = self.template({"title": "Selected checks", "artifact": "./artifact.md", "params": {"enabled": False},
			"output": {"format": "json", "schema": {"type": "string"}}}, "## Process\nProduce\n")
		(file.parent / "artifact.md").write_text(documentText({"output": {"schema": "{{ params.missing }}"}},
			"## Post\n{% if params.enabled %}\n@./missing-check.md\n{{ inputs.unused }}\n{% else %}\n!?true\n{% endif %}\n"))
		value = self.elaboration(file)
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		ElaborationApply(self.ctx, self.plan, value, "selected-artifact-checks")
		task = effectiveTask(self.ctx, TaskGet(self.ctx, "work"))
		self.assertNotIn("unused", ScriptInputs(self.ctx, task.script("Post"))["inputs"])
		self.engine.outputs = iter(['"Produced"'])
		self.assertEqual(TaskStart(self.ctx, "work")["status"], "Succeeded")
		self.assertEqual(len(self.engine.calls), 1)

	def testGeneratedArtifactMetadataCannotRedirectTheSelectedContract(self):
		file = self.template({"title": "Bound artifact", "artifact": "./artifact.md"}, "## Process\n@./process.md\n")
		artifact = file.parent / "artifact.md"
		artifact.write_text("## Post\n!?false\n")
		(file.parent / "process.md").write_text("Produce")
		task = materialize(self.ctx, {"id": "work", "from": str(file)}, "root")
		records = task.meta["_template_sources"]
		process = next(ref for ref, record in records.items() if "/source/" in ref and record.get("source", {}).get("ref") == str(file.parent / "process.md"))
		records[str(artifact)]["compiled_artifact"] = process
		with self.assertRaisesRegex(AutoError, "does not match the selected artifact"):
			effectiveTask(self.ctx, task)
		for key in ("_template_sources", "_template_bindings", "_template_scope"):
			with self.subTest(key=key):
				value = self.elaboration(file, **{key: {}})
				self.assertIn("cannot supply generated template metadata", str(ElaborationValidate(self.ctx, self.plan, value)))
		self.assertFalse((self.plan / "tasks/work.md").exists())

	def testOrdinaryTaskArtifactParametersKeepRuntimeOverridePrecedence(self):
		artifact = self.root / "artifact.md"
		artifact.write_text("## Post\n{% if params.enabled %}\n!?false\n{% else %}\n!?true\n{% endif %}\n")
		value = createElaborationFromDict({"task": "root", "tasks": [{"id": "work", "title": "Runtime params", "params": {"enabled": True},
			"artifact": str(artifact), "sections": {"Process": "Produce"}}]})
		ElaborationApply(self.ctx, self.plan, value, "ordinary-artifact")
		self.assertEqual(TaskStart(self.ctx.derive(params={"enabled": False}), "work")["status"], "Succeeded")

	def testSelectedFrameworkOwnerReferencesRemainAvailableAfterOwnerChanges(self):
		text = documentText({"model": "Owner model", "memory": "minimal"}, "Profile")
		calls = []
		def resolve(context, ref):
			calls.append(ref)
			return Content(ref, text.encode(), digest(text.encode()), self.root / "owner/profile.md")
		context = self.ctx.derive(resolvers={"framework:": resolve})
		value = createElaborationFromDict({"task": "root", "tasks": [{"id": "work", "title": "Owner", "profile": "framework:profile", "sections": {"Process": "Work"}}]})
		ElaborationApply(context, self.plan, value, "owner-framework")
		calls.clear()
		text = documentText({"model": "Changed owner model", "memory": "full"}, "Changed profile")
		task = effectiveTask(context, TaskGet(context, "work"))
		self.assertEqual(task.meta["model"], "Owner model")
		self.assertEqual(task.meta["memory"], "minimal")
		self.assertEqual(calls, [])

	def testShellParameterValuesRespectCurrentPlanPolicy(self):
		marker = self.root / "unexpected.txt"
		file = self.template({"title": "Policy", "params": {"label": "!touch " + str(marker)}}, "## Process\n{{ params.label }}\n")
		context = self.ctx.derive(defaults={"policy": {"shell.run": "deny"}})
		value = self.elaboration(file)
		self.assertEqual(ElaborationValidate(context, self.plan, value), [])
		with self.assertRaisesRegex(AutoError, "Shell execution is disabled"):
			ElaborationApply(context, self.plan, value, "elaboration-denied")
		self.assertFalse(marker.exists())
		self.assertFalse((self.plan / "tasks/work.md").exists())

	def testShellDerivedBudgetIsCheckedAfterResolutionAndFrozen(self):
		counter = self.root / "budget-count.txt"
		file = self.template({"title": "Measured allocation", "params": {"tokens": "!printf x >> " + str(counter) + "; printf 12"},
			"budget": {"tokens": "{{ params.tokens | plus: 0 }}"}}, "## Process\nWork\n")
		(self.plan / "plan.md").write_text(documentText({"id": "demo", "goal": "demo", "budget": {"tokens": 20}}, "# Demo"))
		value = self.elaboration(file)
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		self.assertFalse(counter.exists())
		ElaborationApply(self.ctx, self.plan, value, "measured-budget")
		self.assertEqual(TaskGet(self.ctx, "work").meta["budget"]["tokens"], 12)
		ElaborationApply(self.ctx, self.plan, value, "measured-budget")
		self.assertEqual(counter.read_text(), "x")

	def testShellDerivedFrameworkSelectionsAreResolvedAndCapturedAtApplication(self):
		file = self.template({"title": "Selected framework", "params": {"selection": "!printf selected"},
			"profile": "./{{ params.selection }}.profile.md", "artifact": "./{{ params.selection }}.artifact.md"}, "## Process\nWork\n")
		profile, artifact = file.parent / "selected.profile.md", file.parent / "selected.artifact.md"
		profile.write_text(documentText({"model": "Chosen"}, "Profile"))
		artifact.write_text(documentText({"id": "artifact", "output": {"format": "text"}}, "## Post\n!?true\n"))
		value = self.elaboration(file)
		with patch("auto.integration.process.capture", return_value=("selected", "", 0)) as shell:
			self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
			self.assertEqual(shell.call_count, 0)
			ElaborationApply(self.ctx, self.plan, value, "selected-framework")
			self.assertEqual(shell.call_count, 1)
		profile.unlink()
		artifact.unlink()
		task = effectiveTask(self.ctx, TaskGet(self.ctx, "work"))
		self.assertEqual(task.meta["model"], "Chosen")
		self.assertEqual(task.meta["output"]["format"], "text")
		self.assertEqual(TaskStart(self.ctx, "work")["status"], "Succeeded")

	def testKnownInvalidMetadataStillFailsBeforeShellDependentMetadataResolves(self):
		for invalid in ({"budget": {"tokens": "{{ params.value | plus: 0 }}", "cost": -1}},
			{"output": {"format": "unsupported", "template": "{{ params.value }}"}},
			{"effects": ["{{ params.value }}", "invalid effect"]}):
			with self.subTest(invalid=invalid):
				file = self.template({"title": "Static error", "params": {"value": "!printf 12"}, **invalid}, "## Process\nWork\n")
				with patch("auto.integration.process.capture") as shell:
					self.assertTrue(ElaborationValidate(self.ctx, self.plan, self.elaboration(file)))
					with self.assertRaises(AutoError):
						ElaborationApply(self.ctx, self.plan, self.elaboration(file), "invalid-static")
					self.assertEqual(shell.call_count, 0)
				self.assertFalse((self.plan / "tasks/work.md").exists())

	def testResolvedInvalidMetadataCannotBeCommitted(self):
		for meta, result in (({"budget": {"tokens": "{{ params.value | plus: 0 }}"}}, "-1"),
			({"title": "{{ params.value }}"}, ""),
			({"output": {"format": "{{ params.value }}"}}, "unsupported"),
			({"profile": "./{{ params.value }}.md"}, "missing")):
			with self.subTest(meta=meta):
				file = self.template({"title": "Dynamic error", "params": {"value": "!printf value"}, **meta}, "## Process\nWork\n")
				value = self.elaboration(file)
				with patch("auto.integration.process.capture", return_value=(result, "", 0)) as shell:
					self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
					with self.assertRaises(AutoError):
						ElaborationApply(self.ctx, self.plan, value, "invalid-dynamic")
					self.assertEqual(shell.call_count, 1)
				self.assertFalse((self.plan / "tasks/work.md").exists())
				self.assertEqual(journal(self.plan), [])

	def testShellDerivedOutputFormatsArePlainStringsWhileContractValuesStayLiteral(self):
		for owner in ("task", "artifact"):
			with self.subTest(owner=owner):
				meta = {"title": "Chosen format", "params": {"format": "!printf text", "outline": "\\git:status"}}
				contract = {"format": "{{ params.format }}", "template": "{{ params.outline }}"}
				if owner == "task":
					meta["output"] = contract
				else:
					meta["artifact"] = "./artifact.md"
				file = self.template(meta, "## Process\nWork\n")
				if owner == "artifact":
					(file.parent / "artifact.md").write_text(documentText({"output": contract}, "Artifact"))
				value = self.elaboration(file)
				with patch("auto.integration.process.capture", return_value=("text", "", 0)) as shell:
					self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
					self.assertEqual(shell.call_count, 0)
					task = materialize(self.ctx, value.tasks[0], "root")
					resolved = effectiveTask(self.ctx, task)
					self.assertEqual(resolved.meta["output"], {"format": "text", "template": "\\git:status"})
					self.assertEqual(Value(self.ctx, resolved.meta["output"]["template"]), "git:status")
					self.assertEqual(shell.call_count, 1)

	def testResolvedBudgetMustFitThePlanBeforeCommit(self):
		(self.plan / "plan.md").write_text(documentText({"id": "demo", "goal": "demo", "budget": {"tokens": 20}}, "# Demo"))
		file = self.template({"title": "Excess allocation", "params": {"tokens": "!printf 21"},
			"budget": {"tokens": "{{ params.tokens | plus: 0 }}"}}, "## Process\nWork\n")
		value = self.elaboration(file)
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		with self.assertRaisesRegex(AutoError, "budgets exceed remaining budget"):
			ElaborationApply(self.ctx, self.plan, value, "excess-resolved-budget")
		self.assertFalse((self.plan / "tasks/work.md").exists())
		self.assertEqual(journal(self.plan), [])

	def testNativeContractAndInputMappingsKeepNestedStringsAsData(self):
		file = self.template({"title": "Native metadata", "params": {"type": "object", "text": "string", "ref": "\\git:status"},
			"inputs": {"data": {"reference": "{{ params.ref }}"}},
			"output": {"format": "json", "schema": {"type": "{{ params.type }}", "properties": {"name": {"type": "{{ params.text }}"}}}}},
			"## Process\n{{ inputs.data.reference }}\n")
		task = materialize(self.ctx, {"id": "work", "from": str(file)}, "root")
		self.assertEqual(task.meta["output"]["schema"], {"type": "object", "properties": {"name": {"type": "string"}}})
		self.assertEqual(task.meta["inputs"]["data"], {"reference": "git:status"})
		self.assertIn("git:status", Renderer(self.ctx, task.script()).render())

	def testDryMetadataRetainsUnknownAncestorsAndDynamicIndices(self):
		for id, params, expression, result, expected in (
			("length", {"label": "!printf twelve"}, "{{ params.label.size }}", "twelve", 6),
			("indexed", {"key": "!printf choice", "allocations": {"choice": 15}}, "{{ params.allocations[params.key] }}", "choice", 15)):
			with self.subTest(expression=expression):
				file = self.template({"title": "Dependent lookup", "params": params, "budget": {"tokens": expression}}, "## Process\nWork\n")
				value = self.elaboration(file, id=id)
				with patch("auto.integration.process.capture", return_value=(result, "", 0)) as shell:
					self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
					self.assertEqual(shell.call_count, 0)
					ElaborationApply(self.ctx, self.plan, value, "lookup-" + id)
					self.assertEqual(TaskGet(self.ctx, id).meta["budget"]["tokens"], expected)
					self.assertEqual(shell.call_count, 1)

	def testUnknownOverridesDoNotSelectUnusedFrameworkDefaultsDuringPreflight(self):
		file = self.template({"title": "Deferred overrides", "params": {"selection": "!printf selected"},
			"profile": "./{{ params.selection }}.md", "artifact": "./artifact.md",
			"output": {"format": "{% if params.selection == 'selected' %}text{% else %}json{% endif %}",
				"template": "{{ params.selection }}"}}, "## Process\nWork\n")
		(file.parent / "artifact.md").write_text(documentText({"output": {"format": "unsupported", "template": "./missing.md"}}, "Artifact"))
		(file.parent / "selected.md").write_text(documentText({"model": "Selected"}, "Profile"))
		default = file.parent / "default.md"
		default.write_text(documentText({"model": "Default"}, "Profile"))
		(self.plan / "plan.md").write_text(documentText({"id": "demo", "goal": "demo", "profile": str(default)}, "# Demo"))
		value = self.elaboration(file)
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		dry = materialize(self.ctx, value.tasks[0], "root", dry=True)
		self.assertNotIn("profile", dry.meta)
		self.assertNotIn(str(default), dry.meta["_template_sources"])
		ElaborationApply(self.ctx, self.plan, value, "deferred-overrides")
		task = effectiveTask(self.ctx, TaskGet(self.ctx, "work"))
		self.assertEqual(task.meta["model"], "Selected")
		self.assertEqual(task.meta["output"], {"format": "text", "template": "\\selected"})

	def testShellBackedParameterReferencesRespectDryValidationAndPolicy(self):
		file = self.template({"title": "Reference policy", "params": {"label": "git:status"}}, "## Process\n{{ params.label }}\n")
		value = self.elaboration(file)
		with patch("auto.integration.process.capture", return_value=("Measured status", "", 0)) as shell:
			self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
			self.assertEqual(shell.call_count, 0)
			context = self.ctx.derive(defaults={"policy": {"shell.run": "deny"}})
			with self.assertRaisesRegex(AutoError, "Shell execution is disabled"):
				ElaborationApply(context, self.plan, value, "reference-denied")
			self.assertEqual(shell.call_count, 0)
			task = materialize(self.ctx, value.tasks[0], "root")
			self.assertEqual(shell.call_count, 1)
			self.assertIn("Measured status", Renderer(self.ctx, task.script()).render())
			self.assertEqual(shell.call_count, 1)

	def testCompanionShellBackedParametersResolveAtApplicationWithoutRepeatingAtRuntime(self):
		file = self.template({"title": "Companion reference"}, "## Process\n@./child.md\n")
		(file.parent / "child.md").write_text(documentText({"params": {"label": "git:status"}}, "{{ params.label }}"))
		with patch("auto.integration.process.capture", return_value=("Captured status", "", 0)) as shell:
			self.assertEqual(ElaborationValidate(self.ctx, self.plan, self.elaboration(file)), [])
			self.assertEqual(shell.call_count, 0)
			task = materialize(self.ctx, {"id": "work", "from": str(file)}, "root")
			self.assertEqual(shell.call_count, 1)
			self.assertIn("Captured status", Renderer(self.ctx, task.script()).render())
			self.assertEqual(shell.call_count, 1)

	def testNestedShellParametersUseInheritedEnvironmentAndWorkingDirectory(self):
		file = self.template({"title": "Nested effects", "cwd": "./rootcwd", "env": {"MODE": "root"}}, "## Process\n@./nested/child.md\n")
		rootcwd = file.parent / "rootcwd"
		rootcwd.mkdir()
		child = file.parent / "nested/child.md"
		child.parent.mkdir()
		(child.parent / "childcwd").mkdir()
		child.write_text(documentText({"cwd": "./childcwd", "env": {"MODE": "child", "CHILD": "own"},
			"params": {"label": '!printf x >> marker.txt; printf "%s:%s" "$MODE" "$CHILD"'}}, "{{ params.label }}"))
		value = self.elaboration(file)
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		self.assertFalse((rootcwd / "marker.txt").exists())
		ElaborationApply(self.ctx, self.plan, value, "nested-environment")
		self.assertEqual((rootcwd / "marker.txt").read_text(), "x")
		self.assertFalse((child.parent / "childcwd/marker.txt").exists())
		child.unlink()
		self.assertIn("root:own", TaskExpand(self.ctx, "work"))
		ElaborationApply(self.ctx, self.plan, value, "nested-environment")
		self.assertEqual((rootcwd / "marker.txt").read_text(), "x")

	def testNestedShellParametersRespectPolicyEvenWhenTheRootHasNoParameters(self):
		marker = self.root / "unexpected.txt"
		file = self.template({"title": "Nested policy"}, "## Process\n@./child.md\n")
		child = file.parent / "child.md"
		child.write_text(documentText({"params": {"label": "!touch " + str(marker)}}, "{{ params.label }}"))
		context = self.ctx.derive(defaults={"policy": {"shell.run": "deny"}})
		value = self.elaboration(file)
		self.assertEqual(ElaborationValidate(context, self.plan, value), [])
		with self.assertRaisesRegex(AutoError, "Shell execution is disabled"):
			ElaborationApply(context, self.plan, value, "nested-denied")
		self.assertFalse(marker.exists())
		self.assertFalse((self.plan / "tasks/work.md").exists())
		with patch("auto.integration.process.capture", return_value=("Should not execute", "", 0)) as shell:
			child.write_text(documentText({"params": {"label": "git:status"}}, "{{ params.label }}"))
			with self.assertRaisesRegex(AutoError, "Shell execution is disabled"):
				ElaborationApply(context, self.plan, value, "nested-reference-denied")
			self.assertEqual(shell.call_count, 0)

	def testParentParameterOverridesDoNotExecuteUnusedNestedShellDefaults(self):
		marker = self.root / "unexpected.txt"
		file = self.template({"title": "Override", "params": {"label": "Parent"}}, "## Process\n@./child.md\n")
		(file.parent / "child.md").write_text(documentText({"params": {"label": "!touch " + str(marker)}}, "{{ params.label }}"))
		context = self.ctx.derive(defaults={"policy": {"shell.run": "deny"}})
		ElaborationApply(context, self.plan, self.elaboration(file), "unused-nested-default")
		self.assertIn("Parent", TaskExpand(context, "work"))
		self.assertFalse(marker.exists())

	def testEachInclusionResolvesItsNestedParameterOnceAndStoresTheResult(self):
		counter = self.root / "counter.txt"
		file = self.template({"title": "Per inclusion", "params": {"names": ["one", "two"]}},
			"## Process\n{% for name in params.names %}\n@./child.md\n{% endfor %}\n")
		(file.parent / "child.md").write_text(documentText({"params": {"label": "!printf x >> " + str(counter) + "; wc -c < " + str(counter)}}, "{{ name }}={{ params.label }}"))
		value = self.elaboration(file)
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		self.assertFalse(counter.exists())
		ElaborationApply(self.ctx, self.plan, value, "per-inclusion")
		self.assertEqual(counter.read_text(), "xx")
		text = TaskExpand(self.ctx, "work")
		self.assertIn("one=1", text)
		self.assertIn("two=2", text)
		self.assertEqual(counter.read_text(), "xx")

	def testArtifactCompanionShellParametersResolveBeforeAuthoredChecksExecute(self):
		counter = self.root / "parameter-count.txt"
		check = self.root / "check-count.txt"
		file = self.template({"title": "Artifact parameter", "artifact": "./artifact.md"}, "## Process\nProduce\n")
		(file.parent / "artifact.md").write_text("## Post\n@./check.md\n")
		(file.parent / "check.md").write_text(documentText({"params": {"flag": "!printf x >> " + str(counter) + "; printf Ready"}},
			"!?test {{ params.flag }} = Ready\n!?printf checked >> " + str(check) + "\n"))
		value = self.elaboration(file)
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		self.assertFalse(counter.exists())
		ElaborationApply(self.ctx, self.plan, value, "artifact-parameter")
		self.assertEqual(counter.read_text(), "x")
		self.assertFalse(check.exists())
		self.assertEqual(TaskStart(self.ctx, "work")["status"], "Succeeded")
		self.assertEqual(check.read_text(), "checked")
		self.assertEqual(counter.read_text(), "x")

	def testNestedShellParameterOutputStaysLiteralAcrossBothRenders(self):
		marker = self.root / "unexpected.txt"
		payload = "{{ unknown }}\n!?touch " + str(marker)
		file = self.template({"title": "Literal shell result"}, "## Process\n@./child.md\n")
		(file.parent / "child.md").write_text(documentText({"params": {"label": "!printf '%s' " + shlex.quote(payload)}}, "{{ params.label }}"))
		ElaborationApply(self.ctx, self.plan, self.elaboration(file), "nested-literal")
		self.assertIn(payload, TaskExpand(self.ctx, "work"))
		self.assertFalse(marker.exists())

	def testParameterFenceCannotExposeAnAuthoredShellExample(self):
		marker = self.root / "unexpected.txt"
		file = self.template({"title": "Fence", "params": {"fence": "```"}},
			"## Process\n{{ params.fence }}\n```sh\n!touch " + str(marker) + "\n```\n")
		task = materialize(self.ctx, {"id": "work", "from": str(file)}, "root")
		text = Renderer(self.ctx, task.script()).render()
		self.assertIn("!touch", text)
		self.assertFalse(marker.exists())

	def testPinnedTemplateChangeFailsBeforeMaterializationEffects(self):
		file = self.template({"title": "Pin"}, "## Process\nWork\n")
		value = self.elaboration(file)
		value.tasks[0]["from"] = {"ref": str(file), "digest": "sha256:outdated"}
		violations = ElaborationValidate(self.ctx, self.plan, value)
		self.assertIn("pinned revision", str(violations))
		self.assertFalse((self.plan / "tasks/work.md").exists())

	def testInvalidParameterMetadataReturnsValidationViolations(self):
		for expression in ("{{ params.missing }}", "{{ params.label | missing_filter }}", "{{ params.label"):
			with self.subTest(expression=expression):
				file = self.template({"title": expression, "params": {"label": "Known"}}, "## Process\nWork\n")
				violations = ElaborationValidate(self.ctx, self.plan, self.elaboration(file))
				self.assertIn("Template parameter expansion failed", str(violations))
				self.assertFalse((self.plan / "tasks/work.md").exists())

	def testWhitespaceControlsApplyOnceAcrossBothStages(self):
		params = {"word": " x ", "flag": True, "names": ["a ", " b"]}
		for source in ("A{{ params.word }}{%- if inputs.show -%}B{% endif %}",
			"A {% if params.flag %}{% endif %}{{- inputs.value }}",
			"A {% if inputs.show %} B {{- inputs.value -}} C {% endif %} D",
			"{% for name in params.names -%}{{ name }}{%- if inputs.show -%}!{% endif %}{%- endfor %}"):
			for show in (True, False):
				with self.subTest(source=source, show=show):
					context = self.ctx.derive(params=params, inputs={"show": show, "value": " Value "})
					expected = Renderer(context, Script(source, self.plan / "source.md")).render()
					compiler = ParameterTemplate(params)
					compiled = compiler.expand(source)
					actual = Renderer(context, Script(compiled, self.plan / "compiled.md", {"_template_bindings": compiler.bindings})).render()
					self.assertEqual(actual, expected)

	def testParameterEffectsReleasePlanWriterAndRejectConcurrentRevision(self):
		parameter = "!printf label"
		file = self.template({"title": "Concurrency", "params": {"label": parameter}}, "## Process\n{{ params.label }}\n")
		value, calls = self.elaboration(file), []
		def resolve(context, data, *args, **kwargs):
			if data == parameter:
				with (self.plan / ".lock").open("a+") as lock:
					fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
				calls.append(data)
				PlanRevise(self.ctx, self.plan, [{"op": "add", "task": {"id": "concurrent", "title": "Other work", "sections": {"Process": "Work"}}}], "Concurrent revision")
			return Value(context, data, *args, **kwargs)
		with patch("auto.features.elaboration.materialize.Value", side_effect=resolve):
			with self.assertRaisesRegex(AutoError, "Plan changed"):
				ElaborationApply(self.ctx, self.plan, value, "elaboration-concurrent")
		self.assertEqual(len(calls), 1)
		self.assertFalse((self.plan / "tasks/work.md").exists())
		self.assertTrue((self.plan / "tasks/concurrent.md").exists())

	def testConcurrentApplicationOfSameRunDoesNotRepeatParameterEffects(self):
		counter = self.root / "once.txt"
		file = self.template({"title": "Once", "params": {"label": "!printf x >> " + str(counter) + "; sleep .05; printf label"}}, "## Process\n{{ params.label }}\n")
		value = self.elaboration(file)
		with ThreadPoolExecutor(max_workers=2) as pool:
			futures = [pool.submit(ElaborationApply, self.ctx, self.plan, value, "same-originating-run") for _ in range(2)]
			revisions = [future.result(timeout=10) for future in futures]
		self.assertEqual(revisions[0]["id"], revisions[1]["id"])
		self.assertEqual(counter.read_text(), "x")
