"""Expand artifact section structure before composing selected check bodies."""

import unittest
from unittest.mock import patch

from auto.features.elaboration.operations import ElaborationApply, ElaborationValidate
from auto.features.task.operations import TaskGet, TaskStart, effectiveTask
from auto.features.script.templates import Renderer
from auto.features.script.operations import ScriptInputs
from auto.runtime.model import Content
from auto.utils.data import AutoError
import test_006_04_integration_materialization as fixtures


class ArtifactStructureTests(unittest.TestCase):
	setUp = fixtures.MaterializationTests.setUp
	template = fixtures.MaterializationTests.template
	elaboration = fixtures.MaterializationTests.elaboration

	def testParameterConditionalCanSpanPostAndEvaluationSections(self):
		file = self.template({"title": "Artifact structure", "artifact": "./artifact.md", "params": {"checks": True}}, "## Process\nProduce\n")
		artifact = file.parent / "artifact.md"
		artifact.write_text("{% if params.checks %}\n## Post\n!?true\n## Evaluation\n"
			"!?printf '%s' '{\"score\":0.75,\"feedback\":\"Checked\"}'\n{% else %}\n## Post\n@./missing.md\n{% endif %}\n")
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, self.elaboration(file)), [])
		ElaborationApply(self.ctx, self.plan, self.elaboration(file), "artifact-cross-section-if")
		artifact.unlink()
		file.unlink()
		run = TaskStart(self.ctx, "work")
		self.assertEqual(run["status"], "Succeeded")
		self.assertEqual(run["evaluation"], {"score": 0.75, "feedback": "Checked"})

	def testUnusedProcessExpressionsAndReferencesRemainIgnored(self):
		file = self.template({"title": "Artifact structure", "artifact": "./artifact.md", "params": {"checks": True}}, "## Process\nProduce\n")
		(file.parent / "artifact.md").write_text("## Process\n@./missing-process.md\n{{ params.missing }}\n"
			"{% assign unused = params.missing %}\n{% capture ignored %}{{ params.missing }}{% endcapture %}\n"
			"{% if params.missing %}\n@./also-missing.md\n{% endif %}\n"
			"{% if params.checks %}\n## Post\n!?true\n{% endif %}\n")
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, self.elaboration(file)), [])
		ElaborationApply(self.ctx, self.plan, self.elaboration(file), "artifact-unused-process")
		self.assertEqual(TaskStart(self.ctx, "work")["status"], "Succeeded")

	def testShellSelectedStructureIsDeferredAndThenValidatedBeforeCommit(self):
		file = self.template({"title": "Artifact structure", "artifact": "./artifact.md", "params": {"mode": "!printf good"}}, "## Process\nProduce\n")
		(file.parent / "artifact.md").write_text("{% case params.mode %}{% when 'good' %}\n## Post\n!?true\n"
			"{% else %}\n## Post\n@./missing.md\n{% endcase %}\n")
		with patch("auto.integration.process.capture", return_value=("bad", "", 0)) as shell:
			self.assertEqual(ElaborationValidate(self.ctx, self.plan, self.elaboration(file)), [])
			self.assertEqual(shell.call_count, 0)
			with self.assertRaisesRegex(AutoError, "Cannot resolve"):
				ElaborationApply(self.ctx, self.plan, self.elaboration(file), "artifact-bad-structure")
			self.assertEqual(shell.call_count, 1)
		self.assertFalse((self.plan / "tasks/work.md").exists())

	def testParameterizedArtifactHeadingSelectsTheCheck(self):
		file = self.template({"title": "Artifact structure", "artifact": "./artifact.md", "params": {"section": "Post"}}, "## Process\nProduce\n")
		(file.parent / "artifact.md").write_text("## {{ params.section }}\n!?true\n")
		ElaborationApply(self.ctx, self.plan, self.elaboration(file), "artifact-heading")
		task = effectiveTask(self.ctx, TaskGet(self.ctx, "work"))
		self.assertIn("!?true", task.sections["Post"])
		self.assertEqual(TaskStart(self.ctx, "work")["status"], "Succeeded")

	def testLoopSelectedSectionsKeepCompanionLocalsAndInlineFiles(self):
		file = self.template({"title": "Artifact structure", "artifact": "./artifact.md", "params": {"sections": ["Post", "Evaluation"]}}, "## Process\nProduce\n")
		(file.parent / "artifact.md").write_text("{% for section in params.sections %}\n## {{ section }}\n@./check.md\n{% endfor %}\n")
		(file.parent / "check.md").write_text("{{ section }}:{{ forloop.index }} {{@./inline.txt}}\n")
		(file.parent / "inline.txt").write_text("Literal {{ ignored }}")
		ElaborationApply(self.ctx, self.plan, self.elaboration(file), "artifact-loop-sections")
		for path in (file.parent / "artifact.md", file.parent / "check.md", file.parent / "inline.txt"):
			path.unlink()
		task = effectiveTask(self.ctx, TaskGet(self.ctx, "work"))
		for name, index in (("Post", 1), ("Evaluation", 2)):
			self.assertEqual(Renderer(self.ctx, task.script(name)).render().strip(), f"{name}:{index} Literal {{{{ ignored }}}}")

	def testAssignedSectionNamesRemainAvailableToCompanions(self):
		file = self.template({"title": "Artifact structure", "artifact": "./artifact.md", "params": {"section": "Post"}}, "## Process\nProduce\n")
		(file.parent / "artifact.md").write_text("{% assign selected = params.section %}\n## {{ selected }}\n@./check.md\n")
		(file.parent / "check.md").write_text("Section={{ selected }}")
		ElaborationApply(self.ctx, self.plan, self.elaboration(file), "artifact-assigned-section")
		task = effectiveTask(self.ctx, TaskGet(self.ctx, "work"))
		self.assertEqual(Renderer(self.ctx, task.script("Post")).render().strip(), "Section=Post")

	def testOnlySelectedDuplicateSectionsAreRejectedBeforeEffects(self):
		file = self.template({"title": "Artifact structure", "artifact": "./artifact.md", "params": {"duplicate": False, "effect": "!printf effect"}}, "## Process\nProduce\n")
		(file.parent / "artifact.md").write_text("## Post\n!?true\n{% if params.duplicate %}\n## Post\n!?false\n{% endif %}\n")
		with patch("auto.integration.process.capture") as shell:
			self.assertEqual(ElaborationValidate(self.ctx, self.plan, self.elaboration(file)), [])
			invalid = self.elaboration(file, params={"duplicate": True})
			self.assertIn("Duplicate section", str(ElaborationValidate(self.ctx, self.plan, invalid)))
			with self.assertRaisesRegex(AutoError, "Duplicate section"):
				ElaborationApply(self.ctx, self.plan, invalid, "artifact-duplicate-section")
			self.assertEqual(shell.call_count, 0)

	def testSuccessfulShellSelectedStructureIsFrozenWithoutRepeatingEffects(self):
		file = self.template({"title": "Artifact structure", "artifact": "./artifact.md", "params": {"mode": "!printf good"}}, "## Process\nProduce\n")
		(file.parent / "artifact.md").write_text("{% if params.mode == 'good' %}\n## Post\n!?true\n{% else %}\n## Post\n@./missing.md\n{% endif %}\n")
		with patch("auto.integration.process.capture", return_value=("good", "", 0)) as shell:
			self.assertEqual(ElaborationValidate(self.ctx, self.plan, self.elaboration(file)), [])
			self.assertEqual(shell.call_count, 0)
			ElaborationApply(self.ctx, self.plan, self.elaboration(file), "artifact-shell-structure")
			self.assertEqual(shell.call_count, 1)
		(file.parent / "artifact.md").unlink()
		self.assertEqual(TaskStart(self.ctx, "work")["status"], "Succeeded")

	def testOwnerRevisionIdentifiersRemainOpaqueDuringStructuralCompilation(self):
		file = self.template({"title": "Artifact structure", "artifact": "framework:artifact", "params": {"checks": True}}, "## Process\nProduce\n")
		text = "{% if params.checks %}\n## Post\n@./check.md\n{% endif %}\n"
		(file.parent / "check.md").write_text("!?true\n")
		context = self.ctx.derive(resolvers={"framework:": lambda context, ref:
			Content(ref, text.encode(), "owner/revision {{ missing }}", file.parent / "artifact.md")})
		ElaborationApply(context, self.plan, self.elaboration(file), "artifact-opaque-revision")
		self.assertEqual(TaskStart(context, "work")["status"], "Succeeded")

	def testRuntimeSelectedCompanionsRetainStructuralLoopLocals(self):
		file = self.template({"title": "Artifact structure", "artifact": "./artifact.md", "params": {"sections": ["Post", "Evaluation"]}}, "## Process\nProduce\n")
		(file.parent / "artifact.md").write_text("{% for section in params.sections %}\n## {{ section }}\n@./{{ inputs.which }}.md\n{% endfor %}\n")
		ElaborationApply(self.ctx, self.plan, self.elaboration(file), "artifact-runtime-companion")
		(file.parent / "artifact.md").unlink()
		(file.parent / "future.md").write_text("{{ section }}:{{ forloop.index }} {{ inputs.data }}")
		context = self.ctx.derive(inputs={"which": "future", "data": "Current"})
		task = effectiveTask(context, TaskGet(context, "work"))
		for name, index in (("Post", 1), ("Evaluation", 2)):
			self.assertEqual(Renderer(context, task.script(name)).render().strip(), f"{name}:{index} Current")
		(file.parent / "other.md").write_text("{{ section }}:{{ forloop.index }} Changed={{ inputs.data }}")
		context = self.ctx.derive(inputs={"which": "other", "data": "Later"})
		self.assertEqual(Renderer(context, task.script("Post")).render().strip(), "Post:1 Changed=Later")

	def testRuntimeSelectedInlineFilesStayVerbatimAndInline(self):
		file = self.template({"title": "Artifact structure", "artifact": "./artifact.md", "params": {"sections": ["Post", "Evaluation"]}}, "## Process\nProduce\n")
		(file.parent / "artifact.md").write_text("{% for section in params.sections %}\n## {{ section }}\nBefore {{@./{{ inputs.which }}.txt}} After\n{% endfor %}\n")
		ElaborationApply(self.ctx, self.plan, self.elaboration(file), "artifact-runtime-inline")
		(file.parent / "future.txt").write_text("{{ untouched }}\n!false")
		context = self.ctx.derive(inputs={"which": "future"})
		task = effectiveTask(context, TaskGet(context, "work"))
		for name in ("Post", "Evaluation"):
			self.assertEqual(Renderer(context, task.script(name)).render().strip(), "Before {{ untouched }}\n!false After")

	def testRuntimeCompanionScopeDoesNotConsumeAnExtraTransclusionDepth(self):
		file = self.template({"title": "Artifact structure", "artifact": "./artifact.md", "params": {"sections": ["Post"]}}, "## Process\nProduce\n")
		(file.parent / "artifact.md").write_text("{% for section in params.sections %}\n## {{ section }}\n@./{{ inputs.which }}.md\n{% endfor %}\n")
		ElaborationApply(self.ctx, self.plan, self.elaboration(file), "artifact-runtime-depth")
		(file.parent / "future.md").write_text("{{ section }}")
		context = self.ctx.derive(inputs={"which": "future"}, maxDepth=2)
		task = effectiveTask(context, TaskGet(context, "work"))
		self.assertEqual(Renderer(context, task.script("Post")).render().strip(), "Post")

	def testDiscoveryReportsRuntimePathInputsWithoutReadingTheSelectedFile(self):
		file = self.template({"title": "Artifact structure", "artifact": "./artifact.md", "params": {"sections": ["Post"]}}, "## Process\nProduce\n")
		(file.parent / "artifact.md").write_text("{% for section in params.sections %}\n## {{ section }}\n@./{{ inputs.which }}.md\n{% endfor %}\n")
		ElaborationApply(self.ctx, self.plan, self.elaboration(file), "artifact-runtime-inputs")
		task = effectiveTask(self.ctx, TaskGet(self.ctx, "work"))
		inputs = ScriptInputs(self.ctx, task.script("Post"))
		self.assertEqual(inputs["inputs"], ["which"])
		self.assertTrue(any("which" in ref for ref in inputs["unresolved"]))

	def testScopeCarriersRejectRecursiveRuntimeSelection(self):
		file = self.template({"title": "Artifact structure", "artifact": "./artifact.md", "params": {"sections": ["Post"]}}, "## Process\nProduce\n")
		(file.parent / "artifact.md").write_text("{% for section in params.sections %}\n## {{ section }}\n@auto:{{ inputs.target }}\n{% endfor %}\n")
		ElaborationApply(self.ctx, self.plan, self.elaboration(file), "artifact-runtime-cycle")
		task = effectiveTask(self.ctx, TaskGet(self.ctx, "work"))
		ref = next(ref for ref, record in task.meta["_template_sources"].items() if record.get("scope_wrapper"))
		context = self.ctx.derive(inputs={"target": ref.removeprefix("auto:")})
		with self.assertRaisesRegex(AutoError, "Recursive captured scope"):
			Renderer(context, task.script("Post")).render()

	def testInlineParameterPathDataDoesNotBecomeAnotherLiquidExpression(self):
		file = self.template({"title": "Artifact structure", "artifact": "./artifact.md", "params": {"name": "{{ inputs.fake }}"}}, "## Process\nProduce\n")
		(file.parent / "artifact.md").write_text("## Post\nBefore {{@./{{ params.name }}.txt}} After\n")
		literal = file.parent / "{{ inputs.fake }}.txt"
		literal.write_text("Captured literal path")
		ElaborationApply(self.ctx, self.plan, self.elaboration(file), "artifact-literal-inline-path")
		literal.unlink()
		task = effectiveTask(self.ctx, TaskGet(self.ctx, "work"))
		self.assertNotIn("fake", ScriptInputs(self.ctx, task.script("Post"))["inputs"])
		self.assertEqual(Renderer(self.ctx, task.script("Post")).render().strip(), "Before Captured literal path After")

	def testSectionStructureDependingOnRuntimeInputsIsRejectedBeforeEffects(self):
		conditional = "{% if inputs.review %}\n## Post\n!?true\n{% endif %}\n"
		for artifact in (False, True):
			with self.subTest(artifact=artifact):
				meta = {"title": "Fixed task sections", "params": {"note": "!printf note"}}
				if artifact:
					meta["artifact"] = "./artifact.md"
				file = self.template(meta, "## Process\nProduce\n" + ("" if artifact else conditional))
				if artifact:
					(file.parent / "artifact.md").write_text(conditional)
				with patch("auto.integration.process.capture") as shell:
					self.assertTrue(ElaborationValidate(self.ctx, self.plan, self.elaboration(file)))
					with self.assertRaises(AutoError):
						ElaborationApply(self.ctx, self.plan, self.elaboration(file), "runtime-section-structure")
					self.assertEqual(shell.call_count, 0)

	def testRuntimeInputsStillSelectBranchesWithinASection(self):
		file = self.template({"title": "Fixed task sections", "artifact": "./artifact.md"}, "## Process\nProduce\n")
		(file.parent / "artifact.md").write_text("## Post\n{% if inputs.review %}Review{% else %}Skip{% endif %}\n")
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, self.elaboration(file)), [])
		ElaborationApply(self.ctx, self.plan, self.elaboration(file), "runtime-check-branch")
		task = effectiveTask(self.ctx, TaskGet(self.ctx, "work"))
		for review, expected in ((True, "Review"), (False, "Skip")):
			self.assertEqual(Renderer(self.ctx.derive(inputs={"review": review}), task.script("Post")).render().strip(), expected)
