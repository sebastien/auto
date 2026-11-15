"""Winning contracts and source origins across materialised companions."""

import unittest
from unittest.mock import patch

from auto.features.elaboration.materialize import materialize
from auto.features.elaboration.operations import ElaborationApply, ElaborationValidate
from auto.features.script.operations import ScriptInputs, prepare
from auto.features.script.templates import Renderer
from auto.features.task.operations import TaskExpand, TaskGet, effectiveTask
from auto.utils.data import AutoError, documentText
import test_006_04_integration_materialization as fixtures


class CompositionTests(unittest.TestCase):
	setUp = fixtures.MaterializationTests.setUp
	template = fixtures.MaterializationTests.template
	elaboration = fixtures.MaterializationTests.elaboration

	def testParentContractMasksMissingChildAndGrandchildReferences(self):
		file = self.template({"title": "Compose", "output": {"format": "text", "template": "\\Parent"}},
			"## Process\n@./child.md\n")
		(file.parent / "child.md").write_text(documentText({"output": {"template": "./missing-child.txt"}}, "@./grandchild.md\n"))
		(file.parent / "grandchild.md").write_text(documentText({"output": {"template": "./missing-grandchild.txt"}}, "Nested instructions"))
		value = self.elaboration(file)
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		ElaborationApply(self.ctx, self.plan, value, "nested-parent-contract")
		self.assertIn("Nested instructions", TaskExpand(self.ctx, "work"))
		refs = ScriptInputs(self.ctx, TaskGet(self.ctx, "work").script())["references"]
		self.assertFalse(any("missing-" in ref for ref in refs))

	def testEarlierSiblingContractMasksLaterUnusedFile(self):
		file = self.template({"title": "Siblings"}, "## Process\n@./first.md\n@./second.md\n")
		(file.parent / "first.md").write_text(documentText({"output": {"template": "First outline"}}, "First"))
		(file.parent / "second.md").write_text(documentText({"output": {"template": "./missing.txt"}}, "Second"))
		value = self.elaboration(file)
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		ElaborationApply(self.ctx, self.plan, value, "ordered-siblings")
		for path in (file, file.parent / "first.md", file.parent / "second.md"):
			path.unlink()
		self.assertIn("First outline", TaskExpand(self.ctx, "work"))
		self.assertIn("Second", TaskExpand(self.ctx, "work"))

	def testNestedSiblingContributionReachesItsParentsLaterSibling(self):
		file = self.template({"title": "Siblings"}, "## Process\n@./first.md\n@./second.md\n")
		(file.parent / "first.md").write_text("@./grandchild.md\n")
		(file.parent / "grandchild.md").write_text(documentText({"output": {"template": "Nested outline"}}, "First"))
		(file.parent / "second.md").write_text(documentText({"output": {"template": "./missing.txt"}}, "Second"))
		ElaborationApply(self.ctx, self.plan, self.elaboration(file), "nested-ordered-siblings")
		self.assertIn("Nested outline", TaskExpand(self.ctx, "work"))

	def testRuntimeConditionalSiblingDoesNotEraseTheFallbackContract(self):
		file = self.template({"title": "Siblings"}, "## Process\n{% if inputs.first %}\n@./first.md\n{% endif %}\n@./second.md\n")
		(file.parent / "first.md").write_text(documentText({"output": {"template": "First outline"}}, "First"))
		(file.parent / "second.md").write_text(documentText({"output": {"template": "Second outline"}}, "Second"))
		ElaborationApply(self.ctx, self.plan, self.elaboration(file), "conditional-siblings")
		for first, outline in ((True, "First outline"), (False, "Second outline")):
			self.assertIn(outline, TaskExpand(self.ctx.derive(inputs={"first": first}), "work"))

	def testSiblingInputAndParameterDefaultsStayLocal(self):
		file = self.template({"title": "Siblings"}, "## Process\n@./first.md\n@./second.md\n")
		for name in ("first", "second"):
			(file.parent / (name + ".md")).write_text(documentText({"inputs": {"data": name}, "params": {"label": name}}, "{{ data }} {{ params.label }}\n"))
		ElaborationApply(self.ctx, self.plan, self.elaboration(file), "local-sibling-values")
		text = TaskExpand(self.ctx, "work")
		self.assertIn("first first", text)
		self.assertIn("second second", text)

	def testSiblingDefaultsDoNotCrossTaskLifecycleSections(self):
		file = self.template({"title": "Siblings"}, "## Process\n@./first.md\n## Post\n@./post.md\n")
		(file.parent / "first.md").write_text(documentText({"env": {"MARK": "process"}}, "Instructions"))
		(file.parent / "post.md").write_text(documentText({"params": {"mark": "!printf '%s' \"${MARK:-empty}\""}}, "Mark={{ params.mark }}"))
		ElaborationApply(self.ctx, self.plan, self.elaboration(file), "separate-section-defaults")
		self.assertEqual(Renderer(self.ctx, TaskGet(self.ctx, "work").script("Post")).render().strip(), "Mark=empty")

	def testSkippedLifecycleHeadingDoesNotSeparateSelectedSiblings(self):
		file = self.template({"title": "Selected sections", "params": {"post": False}},
			"## Process\n@./first.md\n{% if params.post %}\n## Post\n{% endif %}\n@./second.md\n")
		(file.parent / "first.md").write_text(documentText({"output": {"template": "First outline"}}, "First"))
		(file.parent / "second.md").write_text(documentText({"output": {"template": "./missing.txt"}}, "Second"))
		value = self.elaboration(file)
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		ElaborationApply(self.ctx, self.plan, value, "skipped-heading-siblings")
		self.assertIn("First outline", TaskExpand(self.ctx, "work"))
		self.assertNotIn("Post", TaskGet(self.ctx, "work").sections)

	def testSelectedLifecycleHeadingSeparatesTheSameSiblings(self):
		file = self.template({"title": "Selected sections", "params": {"post": True}},
			"## Process\n@./first.md\n{% if params.post %}\n## Post\n{% endif %}\n@./second.md\n")
		(file.parent / "first.md").write_text(documentText({"output": {"template": "First outline"}}, "First"))
		(file.parent / "second.md").write_text(documentText({"output": {"template": "./missing.txt"}}, "Second"))
		self.assertIn("Cannot resolve", str(ElaborationValidate(self.ctx, self.plan, self.elaboration(file))))
		with self.assertRaisesRegex(AutoError, "Cannot resolve"):
			materialize(self.ctx, {"id": "work", "from": str(file)}, "root")

	def testUnselectedCaseAndUnlessHeadingsKeepTheCurrentSection(self):
		controls = ("{% unless params.process %}\n## Post\n{% endunless %}",
			"{% case params.section %}{% when 'post' %}\n## Post\n{% else %}Continue{% endcase %}")
		for control in controls:
			with self.subTest(control=control):
				file = self.template({"title": "Selected sections", "params": {"process": True, "section": "process"}},
					"## Process\n@./first.md\n" + control + "\n@./second.md\n")
				(file.parent / "first.md").write_text(documentText({"output": {"template": "First outline"}}, "First"))
				(file.parent / "second.md").write_text(documentText({"output": {"template": "./missing.txt"}}, "Second"))
				self.assertEqual(ElaborationValidate(self.ctx, self.plan, self.elaboration(file)), [])
				task = materialize(self.ctx, {"id": "work", "from": str(file)}, "root")
				self.assertEqual(set(task.sections), {"Process"})
				self.assertEqual(prepare(self.ctx, task.script())["output"]["template"], "First outline")

	def testParameterizedHeadingStartsAnIndependentSection(self):
		file = self.template({"title": "Selected sections", "params": {"section": "Post"}},
			"## Process\n@./first.md\n## {{ params.section }}\n@./post.md\n")
		(file.parent / "first.md").write_text(documentText({"env": {"AUTO_SECTION_MARK": "process"}}, "Instructions"))
		(file.parent / "post.md").write_text(documentText({"params": {"mark": "!printf '%s' \"${AUTO_SECTION_MARK:-empty}\""}}, "Mark={{ params.mark }}"))
		task = materialize(self.ctx, {"id": "work", "from": str(file)}, "root")
		self.assertEqual(Renderer(self.ctx, task.script("Post")).render().strip(), "Mark=empty")

	def testCommentAndCaptureHeadingsDoNotChangeTheActiveSection(self):
		file = self.template({"title": "Selected sections"},
			"## Process\n@./first.md\n{% comment %}\n## Post\n{% endcomment %}\n"
			"{% capture ignored %}\n## Evaluation\n{% endcapture %}\n@./second.md\n")
		(file.parent / "first.md").write_text(documentText({"output": {"template": "First outline"}}, "First"))
		(file.parent / "second.md").write_text(documentText({"output": {"template": "./missing.txt"}}, "Second"))
		task = materialize(self.ctx, {"id": "work", "from": str(file)}, "root")
		self.assertEqual(set(task.sections), {"Process"})
		self.assertEqual(prepare(self.ctx, task.script())["output"]["template"], "First outline")

	def testShellSelectedSkippedHeadingPreservesSiblingDefaultsAfterResolution(self):
		file = self.template({"title": "Selected sections", "params": {"mode": "!printf process"}},
			"## Process\n@./first.md\n{% if params.mode == 'post' %}\n## Post\n{% endif %}\n@./second.md\n")
		(file.parent / "first.md").write_text(documentText({"output": {"template": "First outline"}}, "First"))
		(file.parent / "second.md").write_text(documentText({"output": {"template": "./missing.txt"}}, "Second"))
		with patch("auto.integration.process.capture", return_value=("process", "", 0)) as shell:
			self.assertEqual(ElaborationValidate(self.ctx, self.plan, self.elaboration(file)), [])
			self.assertEqual(shell.call_count, 0)
			ElaborationApply(self.ctx, self.plan, self.elaboration(file), "shell-skipped-heading")
			self.assertEqual(shell.call_count, 1)
		self.assertIn("First outline", TaskExpand(self.ctx, "work"))

	def testShellSelectedNewSectionRequiresItsOwnReferencesBeforeCommit(self):
		file = self.template({"title": "Selected sections", "params": {"mode": "!printf post"}},
			"## Process\n@./first.md\n{% if params.mode == 'post' %}\n## Post\n{% endif %}\n@./second.md\n")
		(file.parent / "first.md").write_text(documentText({"output": {"template": "First outline"}}, "First"))
		(file.parent / "second.md").write_text(documentText({"output": {"template": "./missing.txt"}}, "Second"))
		with patch("auto.integration.process.capture", return_value=("post", "", 0)) as shell:
			self.assertEqual(ElaborationValidate(self.ctx, self.plan, self.elaboration(file)), [])
			self.assertEqual(shell.call_count, 0)
			with self.assertRaisesRegex(AutoError, "Cannot resolve"):
				ElaborationApply(self.ctx, self.plan, self.elaboration(file), "shell-selected-new-section")
			self.assertEqual(shell.call_count, 1)
		self.assertFalse((self.plan / "tsk/work.md").exists())

	def testSiblingExecutionDefaultsConfigureLaterParameterValues(self):
		file = self.template({"title": "Siblings"}, "## Process\n@./first.md\n@./second.md\n")
		(file.parent / "first.md").write_text(documentText({"env": {"MARK": "inherited"}}, "First"))
		(file.parent / "second.md").write_text(documentText({"params": {"mark": "!printf '%s' \"$MARK\""}}, "Seen={{ params.mark }}"))
		ElaborationApply(self.ctx, self.plan, self.elaboration(file), "sibling-parameter-settings")
		self.assertIn("Seen=inherited", TaskExpand(self.ctx, "work"))

	def testKnownParameterBranchContributesOnlyItsSelectedSiblings(self):
		file = self.template({"title": "Siblings", "params": {"first": True}},
			"## Process\n{% if params.first %}\n@./first.md\n{% endif %}\n@./second.md\n")
		(file.parent / "first.md").write_text(documentText({"output": {"template": "First outline"}}, "First"))
		(file.parent / "second.md").write_text(documentText({"output": {"template": "./missing.txt"}}, "Second"))
		ElaborationApply(self.ctx, self.plan, self.elaboration(file), "selected-sibling-branch")
		self.assertIn("First outline", TaskExpand(self.ctx, "work"))
		with self.assertRaisesRegex(AutoError, "Cannot resolve"):
			materialize(self.ctx, {"id": "other", "from": str(file), "params": {"first": False}}, "root")

	def testArtifactCheckDefaultsStayWithinTheirLifecycleFragment(self):
		file = self.template({"title": "Siblings", "artifact": "./artifact.md"}, "## Process\nInstructions\n")
		(file.parent / "artifact.md").write_text("## Post\n@./post.md\n## Evaluation\n@./evaluation.md\n")
		(file.parent / "post.md").write_text(documentText({"env": {"MARK": "post"}}, "Post"))
		(file.parent / "evaluation.md").write_text(documentText({"params": {"mark": "!printf '%s' \"${MARK:-empty}\""}}, "Mark={{ params.mark }}"))
		ElaborationApply(self.ctx, self.plan, self.elaboration(file), "separate-artifact-fragments")
		task = effectiveTask(self.ctx, TaskGet(self.ctx, "work"))
		self.assertEqual(Renderer(self.ctx, task.script("Evaluation")).render().strip(), "Mark=empty")

	def testArtifactContractMasksUnusedProcessCompanionDefaults(self):
		file = self.template({"title": "Compose", "artifact": "./artifact.md"}, "## Process\n@./child.md\n")
		(file.parent / "artifact.md").write_text(documentText({"output": {"format": "text", "template": "./outline.txt"}}, "Artifact"))
		(file.parent / "outline.txt").write_text("Artifact outline")
		(file.parent / "child.md").write_text(documentText({"output": {"template": "./missing.txt"}}, "Instructions"))
		value = self.elaboration(file)
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		ElaborationApply(self.ctx, self.plan, value, "artifact-process-contract")
		for path in (file, file.parent / "artifact.md", file.parent / "outline.txt", file.parent / "child.md"):
			path.unlink()
		self.assertIn("Artifact outline", TaskExpand(self.ctx, "work"))

	def testExplicitArtifactSelectionWinsBeforeCompanionCompilation(self):
		file = self.template({"title": "Compose", "artifact": "./missing-artifact.md"}, "## Process\n@./child.md\n")
		(self.root / "artifact.md").write_text(documentText({"output": {"template": "Outline"}}, "Artifact"))
		(file.parent / "child.md").write_text(documentText({"output": {"template": "./missing.txt"}}, "Instructions"))
		value = self.elaboration(file, artifact="./artifact.md")
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		ElaborationApply(self.ctx, self.plan, value, "selected-artifact-process-contract")
		self.assertIn("Outline", TaskExpand(self.ctx, "work"))

	def testArtifactNativeSchemaCompilesUnshadowedFields(self):
		file = self.template({"title": "Compose", "artifact": "./artifact.md", "params": {"kind": "string"},
			"output": {"schema": {"type": "object"}}}, "## Process\n@./child.md\n")
		(file.parent / "artifact.md").write_text(documentText({"output": {"format": "json", "schema": {
			"type": "{{ params.missing }}", "properties": {"name": {"type": "{{ params.kind }}"}}}}}, "Artifact"))
		(file.parent / "child.md").write_text(documentText({"output": {"schema": "./missing.json"}}, "Instructions"))
		value = self.elaboration(file)
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		ElaborationApply(self.ctx, self.plan, value, "artifact-native-schema")
		self.assertIn('"properties":{"name":{"type":"string"}}', TaskExpand(self.ctx, "work"))

	def testDeferredArtifactContractDoesNotReadUnusedDescendantReferences(self):
		file = self.template({"title": "Compose", "artifact": "./artifact.md", "params": {"selection": "!printf ''", "outline": "Outline"}},
			"## Process\n@./child.md\n")
		(file.parent / "artifact.md").write_text(documentText({"output": {"template": "{{ params.selection | default: params.outline }}"}}, "Artifact"))
		(file.parent / "child.md").write_text(documentText({"output": {"template": "./missing.txt"}}, "Instructions"))
		value = self.elaboration(file)
		with patch("auto.integration.process.capture", return_value=("", "", 0)) as shell:
			self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
			self.assertEqual(shell.call_count, 0)
			ElaborationApply(self.ctx, self.plan, value, "deferred-artifact-contract")
			self.assertEqual(shell.call_count, 1)
		self.assertIn("Outline", TaskExpand(self.ctx, "work"))

	def testDeferredArtifactSelectionDefersItsPotentialContract(self):
		file = self.template({"title": "Compose", "artifact": "./{{ params.selection | default: params.name }}.md",
			"params": {"selection": "!printf ''", "name": "artifact"}}, "## Process\n@./child.md\n")
		(file.parent / "artifact.md").write_text(documentText({"output": {"template": "Outline"}}, "Artifact"))
		(file.parent / "child.md").write_text(documentText({"output": {"template": "./missing.txt"}}, "Instructions"))
		value = self.elaboration(file)
		with patch("auto.integration.process.capture", return_value=("", "", 0)) as shell:
			self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
			self.assertEqual(shell.call_count, 0)
			ElaborationApply(self.ctx, self.plan, value, "deferred-artifact-selection")
			self.assertEqual(shell.call_count, 1)
		self.assertIn("Outline", TaskExpand(self.ctx, "work"))

	def testEarlyArtifactContractDoesNotCompileChecksTwice(self):
		file = self.template({"title": "Compose", "artifact": "./artifact.md"}, "## Process\nInstructions\n")
		(file.parent / "artifact.md").write_text(documentText({"output": {"format": "text"}}, "## Post\n@./check.md\n"))
		(file.parent / "check.md").write_text(documentText({"params": {"code": "!printf 0"}}, "!?exit {{ params.code }}\n"))
		with patch("auto.integration.process.capture", return_value=("0", "", 0)) as shell:
			self.assertEqual(ElaborationValidate(self.ctx, self.plan, self.elaboration(file)), [])
			self.assertEqual(shell.call_count, 0)
			ElaborationApply(self.ctx, self.plan, self.elaboration(file), "once-only-artifact-check-parameter")
			self.assertEqual(shell.call_count, 1)

	def testResolvedArtifactWithoutOverrideStillRequiresChildContractFile(self):
		file = self.template({"title": "Compose", "artifact": "./{{ params.selection | default: params.name }}.md",
			"params": {"selection": "!printf ''", "name": "artifact"}}, "## Process\n@./child.md\n")
		(file.parent / "artifact.md").write_text("Artifact without output defaults")
		(file.parent / "child.md").write_text(documentText({"output": {"template": "./missing.txt"}}, "Instructions"))
		value = self.elaboration(file)
		with patch("auto.integration.process.capture", return_value=("", "", 0)) as shell:
			self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
			self.assertEqual(shell.call_count, 0)
			with self.assertRaisesRegex(AutoError, "Cannot resolve"):
				ElaborationApply(self.ctx, self.plan, value, "required-child-contract")
			self.assertEqual(shell.call_count, 1)
		self.assertFalse((self.plan / "tsk/work.md").exists())

	def testExplicitTaskContractMasksNestedTemplateDefaults(self):
		file = self.template({"title": "Compose"}, "## Process\n@./child.md\n")
		(file.parent / "child.md").write_text(documentText({"output": {"schema": "./missing.json"}}, "Instructions"))
		value = self.elaboration(file, output={"format": "json", "schema": {"type": "object"}})
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		ElaborationApply(self.ctx, self.plan, value, "explicit-nested-contract")
		self.assertIn("Instructions", TaskExpand(self.ctx, "work"))

	def testChildContractSurvivesGrandchildAndSourceDeletion(self):
		file = self.template({"title": "Compose"}, "## Process\n@./child/child.md\n")
		child = file.parent / "child"
		child.mkdir()
		(child / "contract.txt").write_text("Frozen child contract")
		(child / "child.md").write_text(documentText({"output": {"template": "./contract.txt"}}, "@./nested/grandchild.md\n"))
		(child / "nested").mkdir()
		(child / "nested/grandchild.md").write_text(documentText({"output": {"template": "./missing.txt"}}, "Grandchild"))
		ElaborationApply(self.ctx, self.plan, self.elaboration(file), "child-origin-contract")
		for path in (file, child / "contract.txt", child / "child.md", child / "nested/grandchild.md"):
			path.unlink()
		self.assertIn("Grandchild", TaskExpand(self.ctx, "work"))
		refs = ScriptInputs(self.ctx, TaskGet(self.ctx, "work").script())["references"]
		self.assertIn("./contract.txt", refs)
		self.assertEqual(prepare(self.ctx, TaskGet(self.ctx, "work").script())["output"]["template"], "Frozen child contract")
		self.assertFalse(any("missing.txt" in ref for ref in refs))

	def testParentInputMasksMissingNestedDefaultsDuringDiscovery(self):
		file = self.template({"title": "Compose", "inputs": {"data": "Parent data"}}, "## Process\n@./child.md\n")
		(file.parent / "child.md").write_text(documentText({"inputs": {"data": "./missing.txt"}}, "{{ data }}"))
		task = materialize(self.ctx, {"id": "work", "from": str(file)}, "root")
		self.assertEqual(ScriptInputs(self.ctx, task.script())["inputs"], ["data"])
		self.assertNotIn("missing.txt", TaskExpand(self.ctx, task))

	def testDeferredContractMasksNestedReferencesWithoutExecutingDuringValidation(self):
		file = self.template({"title": "Compose", "params": {"selection": "!printf ''", "contract": {"format": "text", "template": "Chosen"}},
			"output": "{{ params.selection | default: params.contract }}"}, "## Process\n@./child.md\n")
		(file.parent / "child.md").write_text(documentText({"output": {"template": "./missing.txt"}}, "Instructions"))
		value = self.elaboration(file)
		with patch("auto.integration.process.capture", return_value=("", "", 0)) as shell:
			self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
			self.assertEqual(shell.call_count, 0)
			ElaborationApply(self.ctx, self.plan, value, "deferred-nested-contract")
			self.assertEqual(shell.call_count, 1)
		self.assertEqual(prepare(self.ctx, TaskGet(self.ctx, "work").script())["output"]["template"], "Chosen")

	def testNestedSchemaDefaultsMergeWithParentFields(self):
		file = self.template({"title": "Compose", "output": {"format": "json", "schema": {"type": "object"}}}, "## Process\n@./child.md\n")
		(file.parent / "child.md").write_text(documentText({"output": {"schema": {"properties": {"name": {"type": "string"}}}}}, "Instructions"))
		task = materialize(self.ctx, {"id": "work", "from": str(file)}, "root")
		self.assertEqual(prepare(self.ctx, task.script())["output"]["schema"], {"type": "object", "properties": {"name": {"type": "string"}}})

	def testDeferredChildContractMasksGrandchildDefaults(self):
		file = self.template({"title": "Compose"}, "## Process\n@./child.md\n")
		(file.parent / "child.md").write_text(documentText({"params": {"selection": "!printf ''", "contract": {"template": "Chosen"}},
			"output": "{{ params.selection | default: params.contract }}"}, "@./grandchild.md\n"))
		(file.parent / "grandchild.md").write_text(documentText({"output": {"template": "./missing.txt"}}, "Instructions"))
		value = self.elaboration(file)
		with patch("auto.integration.process.capture", return_value=("", "", 0)) as shell:
			self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
			self.assertEqual(shell.call_count, 0)
			ElaborationApply(self.ctx, self.plan, value, "deferred-child-contract")
			self.assertEqual(shell.call_count, 1)
		self.assertEqual(prepare(self.ctx, TaskGet(self.ctx, "work").script())["output"]["template"], "Chosen")

	def testPartialExplicitOverridePreservesDeferredContractMask(self):
		file = self.template({"title": "Compose", "params": {"selection": "!printf ''", "contract": {"template": "Chosen"}},
			"output": "{{ params.selection | default: params.contract }}"}, "## Process\n@./child.md\n")
		(file.parent / "child.md").write_text(documentText({"output": {"template": "./missing.txt"}}, "Instructions"))
		value = self.elaboration(file, output={"format": "text"})
		with patch("auto.integration.process.capture", return_value=("", "", 0)) as shell:
			self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
			self.assertEqual(shell.call_count, 0)
			ElaborationApply(self.ctx, self.plan, value, "deferred-partial-contract")
			self.assertEqual(shell.call_count, 1)
