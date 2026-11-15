"""Methodology conformance with known and deferred template metadata."""

import unittest
from unittest.mock import patch

from auto.features.elaboration.model import createElaborationFromDict
from auto.features.elaboration.operations import ElaborationApply, ElaborationValidate
from auto.features.plan.gaps import GapList
from auto.features.plan.store import journal
from auto.features.task.operations import TaskGet, TaskStart
from auto.runtime.model import Content
from auto.runtime.operations import Shell
from auto.utils.data import AutoError, digest, documentText
import test_004_01_integration_planning as fixtures


class MethodologyTests(unittest.TestCase):
	setUp = fixtures.PlanningTests.setUp

	def artifact(self, name):
		path = self.root / "types" / (name + ".md")
		path.parent.mkdir(exist_ok=True)
		path.write_text(documentText({"id": name, "title": name}, "Artifact"))
		return str(path)

	def task(self, id, **meta):
		path = self.root / "templates" / (id + ".md")
		path.parent.mkdir(exist_ok=True)
		path.write_text(documentText({"title": id, **meta}, "## Process\nWork\n"))
		return {"id": id, "from": str(path)}

	def proposal(self, tasks, phases):
		path = self.root / "methods" / "method.md"
		path.parent.mkdir(exist_ok=True)
		path.write_text(documentText({"kind": "methodology", "phases": phases}, "Method guidance"))
		return createElaborationFromDict({"task": "root", "methodology": str(path), "tasks": tasks})

	def testDeferredPhaseArtifactAndGateAreValidatedAfterResolutionAndPinned(self):
		artifact = self.artifact("spec")
		value = self.proposal([self.task("work", phase="{{ params.phase }}", artifact="../types/{{ params.kind }}.md",
			hints={"gate": "{{ params.gate }}"}, params={"phase": "!printf spec", "kind": "!printf spec", "gate": "!printf approval"})],
			[{"id": "spec", "artifacts": [artifact], "gate": "approval"}])
		method = self.root / "methods/method.md"
		def captured(options, args, config, stdin=""):
			return ("approval", "", 0) if args[-1] == "printf approval" else ("spec", "", 0)
		with patch("auto.integration.process.capture", side_effect=captured) as shell:
			self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
			self.assertEqual(shell.call_count, 0)
			revision = ElaborationApply(self.ctx, self.plan, value, "methodology-fields")
			self.assertEqual(shell.call_count, 3)
		task = TaskGet(self.ctx, "work")
		self.assertEqual(task.meta["phase"], "spec")
		self.assertEqual(task.meta["artifact"], artifact)
		self.assertEqual(task.meta["hints"]["gate"], "approval")
		self.assertIn({"ref": str(method), "digest": digest(method.read_bytes())}, revision["context"])
		self.assertEqual(TaskStart(self.ctx, "work")["status"], "Succeeded")
		self.assertEqual(len(GapList(self.ctx)), 1)

	def testKnownPhaseCanHaveDeferredApprovalGate(self):
		value = self.proposal([self.task("work", phase="spec", hints={"gate": "{{ params.gate }}"}, params={"gate": "!printf approval"})],
			[{"id": "spec", "gate": "approval"}])
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		ElaborationApply(self.ctx, self.plan, value, "deferred-gate")
		self.assertEqual(TaskGet(self.ctx, "work").meta["hints"]["gate"], "approval")

	def testKnownPhaseCanHaveDeferredArtifact(self):
		artifact = self.artifact("spec")
		value = self.proposal([self.task("work", phase="spec", artifact="../types/{{ params.kind }}.md", params={"kind": "!printf spec"})],
			[{"id": "spec", "artifacts": [artifact]}])
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		ElaborationApply(self.ctx, self.plan, value, "deferred-artifact")
		self.assertEqual(TaskGet(self.ctx, "work").meta["artifact"], artifact)

	def testKnownViolationsRemainVisibleDespiteUnrelatedShellParameters(self):
		artifact, other = self.artifact("spec"), self.artifact("other")
		for meta, phases, message in (
			({"phase": "other"}, [{"id": "spec"}, {"id": "other"}], "Missing methodology phase"),
			({"phase": "spec", "artifact": other}, [{"id": "spec", "artifacts": [artifact]}], "Missing phase artifact"),
			({"phase": "spec"}, [{"id": "spec", "gate": "approval"}], "Missing phase approval gate"),
			({"phase": "alien"}, [{"id": "spec"}], "Unknown methodology phase")):
			with self.subTest(message=message):
				value = self.proposal([self.task("work", params={"note": "!printf note"}, **meta)], phases)
				with patch("auto.integration.process.capture") as shell:
					self.assertIn(message, str(ElaborationValidate(self.ctx, self.plan, value)))
					with self.assertRaises(AutoError):
						ElaborationApply(self.ctx, self.plan, value, "known-method-error")
					self.assertEqual(shell.call_count, 0)

	def testKnownLaterPhaseDependencyFailsBeforeParameterEffects(self):
		value = self.proposal([self.task("work", phase="spec", inputs={"later": "task:later"}, params={"note": "!printf note"}),
			self.task("later", phase="release")], [{"id": "spec"}, {"id": "release"}])
		with patch("auto.integration.process.capture") as shell:
			self.assertIn("depends on a later phase", str(ElaborationValidate(self.ctx, self.plan, value)))
			with self.assertRaisesRegex(AutoError, "depends on a later phase"):
				ElaborationApply(self.ctx, self.plan, value, "known-order-error")
			self.assertEqual(shell.call_count, 0)

	def testResolvedMethodologyViolationsCannotBeCommitted(self):
		artifact = self.artifact("spec")
		self.artifact("other")
		for meta, result, phases, message in (
			({"phase": "{{ params.value }}"}, "alien", [{"id": "spec"}], "Unknown methodology phase"),
			({"phase": "{{ params.value }}"}, "", [{"id": "spec"}], "Missing methodology phase"),
			({"phase": "spec", "hints": {"gate": "{{ params.value }}"}}, "skip", [{"id": "spec", "gate": "approval"}], "Missing phase approval gate"),
			({"phase": "spec", "artifact": "../types/{{ params.value }}.md"}, "other", [{"id": "spec", "artifacts": [artifact]}], "Missing phase artifact"),
			({"phase": "{{ params.value }}", "inputs": {"later": "task:later"}}, "spec", [{"id": "spec"}, {"id": "release"}], "depends on a later phase")):
			with self.subTest(message=message):
				tasks = [self.task("work", params={"value": "!printf value"}, **meta)]
				if "inputs" in meta:
					tasks.append(self.task("later", phase="release"))
				value = self.proposal(tasks, phases)
				with patch("auto.integration.process.capture", return_value=(result, "", 0)) as shell:
					self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
					with self.assertRaises(AutoError) as failure:
						ElaborationApply(self.ctx, self.plan, value, "resolved-method-error")
					self.assertIn(message, str(failure.exception))
					self.assertEqual(shell.call_count, 1)
				self.assertFalse((self.plan / "tsk/work.md").exists())
				self.assertEqual(journal(self.plan), [])

	def testExplicitMetadataOverridesResolveDeferredTemplateFields(self):
		artifact = self.artifact("spec")
		task = self.task("work", phase="{{ params.value }}", artifact="{{ params.value }}", hints="{{ params.value }}",
			params={"value": "!printf invalid"})
		task.update(phase="spec", artifact=artifact, hints={"gate": "approval"})
		value = self.proposal([task], [{"id": "spec", "artifacts": [artifact], "gate": "approval"}])
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		ElaborationApply(self.ctx, self.plan, value, "explicit-method-fields")
		stored = TaskGet(self.ctx, "work")
		self.assertEqual(stored.meta["phase"], "spec")
		self.assertEqual(stored.meta["artifact"], artifact)
		self.assertEqual(stored.meta["hints"], {"gate": "approval"})

	def testInvalidExplicitOverridesRemainKnownDespiteDeferredTemplateFields(self):
		artifact, other = self.artifact("spec"), self.artifact("other")
		for override, message in (({"phase": "alien"}, "Unknown methodology phase"),
			({"artifact": other}, "Missing phase artifact"), ({"hints": {"gate": "skip"}}, "Missing phase approval gate")):
			with self.subTest(override=override):
				task = self.task("work", phase="{{ params.value }}", artifact="{{ params.value }}", hints="{{ params.value }}",
					params={"value": "!printf invalid"})
				task.update(phase="spec", artifact=artifact, hints={"gate": "approval"})
				task.update(override)
				value = self.proposal([task], [{"id": "spec", "artifacts": [artifact], "gate": "approval"}])
				with patch("auto.integration.process.capture") as shell:
					self.assertIn(message, str(ElaborationValidate(self.ctx, self.plan, value)))
					with self.assertRaises(AutoError):
						ElaborationApply(self.ctx, self.plan, value, "invalid-explicit-method-fields")
					self.assertEqual(shell.call_count, 0)

	def testFalseNumericPhaseIsNotTreatedAsAnUnassignedTask(self):
		value = self.proposal([self.task("valid", phase="spec"), self.task("invalid", phase=0, params={"note": "!printf note"})], [{"id": "spec"}])
		with patch("auto.integration.process.capture") as shell:
			self.assertIn("Unknown methodology phase", str(ElaborationValidate(self.ctx, self.plan, value)))
			with self.assertRaises(AutoError):
				ElaborationApply(self.ctx, self.plan, value, "numeric-phase")
			self.assertEqual(shell.call_count, 0)

	def testWholeDeferredHintsRetainNestedExplicitOverrides(self):
		task = self.task("work", phase="spec", hints="{{ params.value | default: params.defaults }}",
			params={"value": "!printf ''", "defaults": {"gate": "approval"}})
		task["hints"] = {"refine": "fine"}
		value = self.proposal([task], [{"id": "spec", "gate": "approval"}])
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		ElaborationApply(self.ctx, self.plan, value, "whole-hints")
		self.assertEqual(TaskGet(self.ctx, "work").meta["hints"], {"gate": "approval", "refine": "fine"})

	def testResolvedNonMappingHintsAreRejectedAsViolations(self):
		value = self.proposal([self.task("work", phase="spec", hints="{{ params.value }}", params={"value": "!printf invalid"})], [{"id": "spec"}])
		with patch("auto.integration.process.capture", return_value=("invalid", "", 0)) as shell:
			self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
			with self.assertRaisesRegex(AutoError, "hints must be a mapping"):
				ElaborationApply(self.ctx, self.plan, value, "resolved-invalid-hints")
			self.assertEqual(shell.call_count, 1)
		self.assertFalse((self.plan / "tsk/work.md").exists())
		self.assertEqual(journal(self.plan), [])

	def testKnownExplicitOverrideCannotHideBehindADeferredAncestor(self):
		task = self.task("work", phase="spec", hints="{{ params.value | default: params.defaults }}",
			params={"value": "!printf ''", "defaults": {"gate": "approval"}})
		task["hints"] = {"gate": "skip"}
		value = self.proposal([task], [{"id": "spec", "gate": "approval"}])
		with patch("auto.integration.process.capture") as shell:
			self.assertIn("Missing phase approval gate", str(ElaborationValidate(self.ctx, self.plan, value)))
			with self.assertRaises(AutoError):
				ElaborationApply(self.ctx, self.plan, value, "overridden-gate")
			self.assertEqual(shell.call_count, 0)

	def testDeferredTasksNeedEnoughDistinctProducersForMethodologyRequirements(self):
		artifact, other = self.artifact("spec"), self.artifact("other")
		for meta, phases in (
			({"phase": "{{ params.value }}"}, [{"id": "spec"}, {"id": "release"}]),
			({"phase": "spec", "artifact": "../types/{{ params.value }}.md"}, [{"id": "spec", "artifacts": [artifact, other]}])):
			with self.subTest(phases=phases):
				value = self.proposal([self.task("work", params={"value": "!printf spec"}, **meta)], phases)
				with patch("auto.integration.process.capture") as shell:
					self.assertIn("distinct", str(ElaborationValidate(self.ctx, self.plan, value)))
					with self.assertRaises(AutoError):
						ElaborationApply(self.ctx, self.plan, value, "insufficient-producers")
					self.assertEqual(shell.call_count, 0)

	def testDeferredProducersCanCoverDistinctPhaseAndArtifactRequirements(self):
		artifact, other = self.artifact("spec"), self.artifact("other")
		value = self.proposal([self.task("spec", phase="{{ params.phase }}", artifact="../types/{{ params.kind }}.md",
			params={"phase": "!printf spec", "kind": "!printf spec"}),
			self.task("release", phase="{{ params.phase }}", artifact="../types/{{ params.kind }}.md",
				params={"phase": "!printf release", "kind": "!printf other"})],
			[{"id": "spec", "artifacts": [artifact]}, {"id": "release", "artifacts": [other]}])
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		ElaborationApply(self.ctx, self.plan, value, "distinct-producers")
		self.assertEqual(TaskGet(self.ctx, "spec").meta["phase"], "spec")
		self.assertEqual(TaskGet(self.ctx, "release").meta["phase"], "release")

	def testArtifactReferencesKeepTheirOwnOriginsAndUseExistingSnapshots(self):
		artifact = self.artifact("spec")
		value = self.proposal([self.task("work", phase="spec", artifact="../types/spec.md")],
			[{"id": "spec", "artifacts": ["../types/spec.md"]}])
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		ElaborationApply(self.ctx, self.plan, value, "relative-method-artifact")
		(self.root / "types/spec.md").unlink()
		value.tasks = []
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		self.assertEqual(TaskGet(self.ctx, "work").meta["artifact"], artifact)

	def testCatalogueArtifactSatisfiesItsLocalFileAliasAfterDeletion(self):
		artifact = self.root / ".auto/catalogue/artifact/spec.md"
		artifact.parent.mkdir(parents=True)
		artifact.write_text(documentText({"title": "Specification"}, "Artifact"))
		value = self.proposal([self.task("work", phase="spec", artifact="auto:catalogue/artifact/spec")],
			[{"id": "spec", "artifacts": ["../.auto/catalogue/artifact/spec.md"]}])
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		ElaborationApply(self.ctx, self.plan, value, "catalogue-file-method-alias")
		artifact.unlink()
		value.tasks = []
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])

	def testLocalArtifactSatisfiesItsCatalogueAliasAfterDeletion(self):
		artifact = self.root / ".auto/catalogue/artifact/spec.md"
		artifact.parent.mkdir(parents=True)
		artifact.write_text(documentText({"title": "Specification"}, "Artifact"))
		value = self.proposal([self.task("work", phase="spec", artifact=str(artifact))],
			[{"id": "spec", "artifacts": ["auto:catalogue/artifact/spec"]}])
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		ElaborationApply(self.ctx, self.plan, value, "file-catalogue-method-alias")
		artifact.unlink()
		value.tasks = []
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])

	def testCatalogueAliasesCountAsOneDeferredArtifactRequirement(self):
		artifact = self.root / ".auto/catalogue/artifact/spec.md"
		artifact.parent.mkdir(parents=True)
		artifact.write_text("Artifact")
		value = self.proposal([self.task("work", phase="spec", artifact="{{ params.selected }}", params={"selected": "!printf selected"})],
			[{"id": "spec", "artifacts": ["auto:catalogue/artifact/spec", str(artifact)]}])
		with patch("auto.integration.process.capture", return_value=("auto:catalogue/artifact/spec", "", 0)) as shell:
			self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
			self.assertEqual(shell.call_count, 0)
			ElaborationApply(self.ctx, self.plan, value, "deduplicated-method-aliases")
			self.assertEqual(shell.call_count, 1)

	def testUnselectedCatalogueLayerDoesNotSatisfyTheSelectedAlias(self):
		project = self.root / ".auto/catalogue"
		user = self.root / "other-catalogue"
		for root in (project, user):
			(root / "artifact").mkdir(parents=True)
			(root / "artifact/spec.md").write_text("Identical bytes")
		value = self.proposal([self.task("work", phase="spec", artifact=str(user / "artifact/spec.md"))],
			[{"id": "spec", "artifacts": ["auto:catalogue/artifact/spec"]}])
		with patch("auto.features.elaboration.catalogue.layers", return_value=[("project", project), ("user", user)]):
			self.assertIn("Missing phase artifact", str(ElaborationValidate(self.ctx, self.plan, value)))

	def testDifferentOwnerReferencesSharingAnOriginRemainDifferentTypes(self):
		def resolve(context, ref):
			data = b"Identical bytes"
			return Content(ref, data, digest(data), self.root / "owner/artifact.md")
		context = self.ctx.derive(resolvers={"owner:": resolve})
		value = self.proposal([self.task("work", phase="spec", artifact="owner:first", params={"note": "!printf note"})],
			[{"id": "spec", "artifacts": ["owner:other"]}])
		with patch("auto.integration.process.capture") as shell:
			self.assertIn("Missing phase artifact", str(ElaborationValidate(context, self.plan, value)))
			self.assertEqual(shell.call_count, 0)

	def testDifferentFilesWithIdenticalBytesAreNotAliases(self):
		artifact = self.root / ".auto/catalogue/artifact/spec.md"
		artifact.parent.mkdir(parents=True)
		artifact.write_text("Identical bytes")
		other = self.root / "different-artifact.md"
		other.write_bytes(artifact.read_bytes())
		value = self.proposal([self.task("work", phase="spec", artifact="auto:catalogue/artifact/spec")],
			[{"id": "spec", "artifacts": [str(other)]}])
		self.assertIn("Missing phase artifact", str(ElaborationValidate(self.ctx, self.plan, value)))

	def testDifferentSelectorsOnTheSameArtifactAreNotAliases(self):
		artifact = self.root / ".auto/catalogue/artifact/spec.md"
		artifact.parent.mkdir(parents=True)
		artifact.write_text("## Post\nPost content\n## Evaluation\nEvaluation content\n")
		value = self.proposal([self.task("work", phase="spec", artifact=str(artifact) + "#Evaluation")],
			[{"id": "spec", "artifacts": ["auto:catalogue/artifact/spec#Post"]}])
		self.assertIn("Missing phase artifact", str(ElaborationValidate(self.ctx, self.plan, value)))

	def testRetainedCatalogueIdentitySurvivesANewLayerWithoutAliasingItsFile(self):
		project = self.root / ".auto/catalogue"
		user = self.root / "other-catalogue"
		(user / "artifact").mkdir(parents=True)
		(user / "artifact/spec.md").write_text("User artifact")
		value = self.proposal([self.task("work", phase="spec", artifact="auto:catalogue/artifact/spec")],
			[{"id": "spec", "artifacts": ["auto:catalogue/artifact/spec"]}])
		with patch("auto.features.elaboration.catalogue.layers", return_value=[("project", project), ("user", user)]):
			ElaborationApply(self.ctx, self.plan, value, "retained-catalogue-type")
			(project / "artifact").mkdir(parents=True)
			(project / "artifact/spec.md").write_text("Project override")
			value.tasks = []
			self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
			value = self.proposal([], [{"id": "spec", "artifacts": [str(project / "artifact/spec.md")]}])
			self.assertIn("Missing phase artifact", str(ElaborationValidate(self.ctx, self.plan, value)))
	def testMalformedMethodologyDefinitionsFailBeforeEffects(self):
		for phases in ("spec", [None], [{"id": "spec"}, {"id": "spec"}],
			[{"id": "spec", "artifacts": "wrong"}], [{"id": "spec", "gate": "automatic"}]):
			with self.subTest(phases=phases):
				value = self.proposal([self.task("work", phase="spec", params={"note": "!printf note"})], phases)
				with patch("auto.integration.process.capture") as shell:
					self.assertTrue(ElaborationValidate(self.ctx, self.plan, value))
					with self.assertRaises(AutoError):
						ElaborationApply(self.ctx, self.plan, value, "invalid-methodology")
					self.assertEqual(shell.call_count, 0)

	def testMethodologyMustBeAReferenceString(self):
		for ref in ({}, [], 42, "unqualified"):
			with self.subTest(ref=ref):
				value = self.proposal([self.task("work", params={"note": "!printf note"})], [])
				value.methodology = ref
				with patch("auto.integration.process.capture") as shell:
					self.assertIn("Methodology must be a reference string", str(ElaborationValidate(self.ctx, self.plan, value)))
					with self.assertRaisesRegex(AutoError, "Methodology must be a reference string"):
						ElaborationApply(self.ctx, self.plan, value, "invalid-method-reference")
					self.assertEqual(shell.call_count, 0)

	def testMethodologySourceInspectionCannotExecuteShell(self):
		def resolve(context, ref):
			data = Shell(context, "printf method")[0].encode()
			return Content(ref, data, digest(data), self.root / "owned-method.md")
		context = self.ctx.derive(resolvers={"owner:method": resolve})
		value = self.proposal([self.task("work", phase="spec", params={"note": "!printf note"})], [{"id": "spec"}])
		value.methodology = "owner:method"
		with patch("auto.integration.process.capture", return_value=(documentText({"phases": [{"id": "spec"}]}, "Method"), "", 0)) as shell:
			self.assertIn("Shell execution is disabled", str(ElaborationValidate(context, self.plan, value)))
			with self.assertRaisesRegex(AutoError, "Shell execution is disabled"):
				ElaborationApply(context, self.plan, value, "effectful-method-reference")
			self.assertEqual(shell.call_count, 0)

	def testMethodologyChangesDuringParameterEffectsAbortTheRevision(self):
		value = self.proposal([self.task("work", phase="spec", params={"note": "!printf note"})], [{"id": "spec"}])
		method = self.root / "methods/method.md"
		def changeMethod(*args, **kwargs):
			method.write_text(documentText({"phases": [{"id": "spec"}]}, "Changed guidance"))
			return "note", "", 0
		with patch("auto.integration.process.capture", side_effect=changeMethod) as shell:
			with self.assertRaisesRegex(AutoError, "input changed while applying"):
				ElaborationApply(self.ctx, self.plan, value, "changed-methodology")
			self.assertEqual(shell.call_count, 1)
		self.assertFalse((self.plan / "tsk/work.md").exists())
		self.assertEqual(journal(self.plan), [])

	def testApplicationUsesPinnedMethodologyCriteriaAfterParameterEffects(self):
		value = self.proposal([self.task("work", phase="spec", hints={"gate": "{{ params.gate }}"}, params={"gate": "!printf skip"})],
			[{"id": "spec", "gate": "approval"}])
		method = self.root / "methods/method.md"
		def changeMethod(*args, **kwargs):
			method.write_text(documentText({"phases": [{"id": "spec"}]}, "Gate removed"))
			return "skip", "", 0
		with patch("auto.integration.process.capture", side_effect=changeMethod) as shell:
			self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
			with self.assertRaisesRegex(AutoError, "Missing phase approval gate"):
				ElaborationApply(self.ctx, self.plan, value, "pinned-methodology")
			self.assertEqual(shell.call_count, 1)
		self.assertFalse((self.plan / "tsk/work.md").exists())
