"""Resolve template execution settings before parameter shell effects."""

import unittest
from unittest.mock import patch

from auto.features.elaboration.operations import ElaborationApply, ElaborationValidate
from auto.features.plan.store import journal
from auto.features.task.operations import TaskExpand, TaskGet
from auto.runtime.model import Content
from auto.runtime.operations import Shell
from auto.utils.data import AutoError, digest, documentText
import test_006_04_integration_materialization as fixtures


class ParameterConfigurationTests(unittest.TestCase):
	setUp = fixtures.MaterializationTests.setUp
	template = fixtures.MaterializationTests.template
	elaboration = fixtures.MaterializationTests.elaboration

	def testPureParametersConfigureShellValuesRegardlessOfDeclarationOrder(self):
		file = self.template({"title": "Configured values", "cwd": "{{ params.directory }}", "env": {"MODE": "{{ params.mode }}"},
			"params": {"label": '!printf x >> count.txt; printf "%s" "$MODE"', "directory": "target", "mode": "./mode.txt"}},
			"## Process\nCaptured={{ params.label }}\n!printf 'Runtime=%s:%s' \"$MODE\" \"$PWD\"\n")
		(file.parent / "mode.txt").write_text("Chosen")
		target = file.parent / "target"
		target.mkdir()
		value = self.elaboration(file)
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		self.assertFalse((target / "count.txt").exists())
		ElaborationApply(self.ctx, self.plan, value, "configured-values")
		task = TaskGet(self.ctx, "work")
		self.assertEqual(task.meta["cwd"], str(target))
		self.assertEqual(task.meta["env"]["MODE"], "Chosen")
		self.assertEqual((target / "count.txt").read_text(), "x")
		text = TaskExpand(self.ctx, "work")
		self.assertIn("Captured=Chosen", text)
		self.assertIn("Runtime=Chosen:" + str(target), text)
		ElaborationApply(self.ctx, self.plan, value, "configured-values")
		self.assertEqual((target / "count.txt").read_text(), "x")

	def testNativeTypedSettingsExpandBeforeConfigurationValidation(self):
		file = self.template({"title": "Typed configuration", "temperature": "{{ params.temperature }}", "isolated": "{{ params.isolated }}",
			"env": "{{ params.environment }}", "params": {"temperature": .25, "isolated": True, "environment": {"MODE": "typed"}}},
			"## Process\n!printf '%s' \"$MODE\"\n")
		value = self.elaboration(file)
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		ElaborationApply(self.ctx, self.plan, value, "typed-configuration")
		task = TaskGet(self.ctx, "work")
		self.assertEqual(task.meta["temperature"], .25)
		self.assertIs(task.meta["isolated"], True)
		self.assertEqual(task.meta["env"], {"MODE": "typed"})
		self.assertIn("typed", TaskExpand(self.ctx, "work"))

	def testNestedParameterConfigurationRetainsParentPrecedenceAndSourceOrigins(self):
		file = self.template({"title": "Inherited settings", "cwd": "./{{ params.rootdir }}", "env": {"MODE": "{{ params.rootmode }}"},
			"params": {"rootdir": "rootcwd", "rootmode": "parent"}}, "## Process\n@./nested/child.md\n")
		target = file.parent / "rootcwd"
		target.mkdir()
		child = file.parent / "nested/child.md"
		child.parent.mkdir()
		child.write_text(documentText({"cwd": "./{{ params.childdir }}", "env": {"MODE": "{{ params.childmode }}", "CHILD": "{{ params.childmode }}"},
			"params": {"label": '!printf x >> child-count.txt; printf "%s:%s" "$MODE" "$CHILD"',
				"childdir": "absent", "childmode": "own"}}, "Child={{ params.label }}\n@./grandchild.md\n"))
		(child.parent / "grandchild.md").write_text(documentText({"params": {"grandchild": '!printf "%s:%s:%s" "$MODE" "$CHILD" "$PWD"'}},
			"Grandchild={{ params.grandchild }}\n"))
		value = self.elaboration(file)
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		ElaborationApply(self.ctx, self.plan, value, "nested-settings")
		self.assertEqual((target / "child-count.txt").read_text(), "x")
		child.unlink()
		(child.parent / "grandchild.md").unlink()
		text = TaskExpand(self.ctx, "work")
		self.assertIn("Child=parent:own", text)
		self.assertIn("Grandchild=parent:own:" + str(target), text)

	def testExplicitTaskAndContextSettingsOverrideTemplateParameterConfiguration(self):
		file = self.template({"title": "Overrides", "cwd": "./{{ params.directory }}", "env": {"MODE": "{{ params.mode }}"},
			"params": {"directory": "unused", "mode": "template", "label": '!printf x >> override-count.txt; printf "%s" "$MODE"'}},
			"## Process\n{{ params.label }}\n")
		for id, contextMode in (("explicit", None), ("context", "context")):
			with self.subTest(id=id):
				target = self.root / id
				target.mkdir()
				declared = id if contextMode is None else "unused-context"
				context = self.ctx if contextMode is None else self.ctx.derive(config={**self.ctx.config, "cwd": str(target), "env": {"MODE": contextMode}})
				value = self.elaboration(file, id=id, cwd=declared, env={"MODE": "explicit"})
				self.assertEqual(ElaborationValidate(context, self.plan, value), [])
				ElaborationApply(context, self.plan, value, "override-" + id)
				self.assertEqual(TaskGet(context, id).meta["cwd"], str(self.root / declared))
				self.assertIn(contextMode or "explicit", TaskExpand(context, id))
				self.assertEqual((target / "override-count.txt").read_text(), "x")

	def testCircularShellConfigurationFailsWithoutExecutingCommands(self):
		for settings in ({"cwd": "{{ params.value }}"}, {"env": {"MODE": "{{ params.value }}"}}):
			with self.subTest(settings=settings):
				file = self.template({"title": "Cycle", "params": {"value": "!printf value"}, **settings}, "## Process\nWork\n")
				value = self.elaboration(file)
				with patch("auto.integration.process.capture") as shell:
					self.assertIn("Circular parameter configuration", str(ElaborationValidate(self.ctx, self.plan, value)))
					with self.assertRaisesRegex(AutoError, "Circular parameter configuration"):
						ElaborationApply(self.ctx, self.plan, value, "configuration-cycle")
					self.assertEqual(shell.call_count, 0)
				self.assertFalse((self.plan / "tasks/work.md").exists())

	def testWinningContextSettingRemovesAnUnusedConfigurationCycle(self):
		file = self.template({"title": "Masked cycle", "env": {"MODE": "{{ params.label }}"},
			"params": {"label": '!printf "%s" "$MODE"'}}, "## Process\n{{ params.label }}\n")
		context = self.ctx.derive(config={**self.ctx.config, "env": {"MODE": "winning"}})
		value = self.elaboration(file)
		self.assertEqual(ElaborationValidate(context, self.plan, value), [])
		ElaborationApply(context, self.plan, value, "masked-cycle")
		self.assertIn("winning", TaskExpand(context, "work"))

	def testKnownInvalidSettingsFailBeforeEffectsAndResolvedInvalidSettingsCannotCommit(self):
		for setting in ({"env": {"MODE": 42}}, {"isolated": 1}, {"temperature": "invalid"},
			{"inputs": "invalid"}, {"output": "invalid"}):
			with self.subTest(setting=setting):
				file = self.template({"title": "Invalid settings", "params": {"label": "!printf value"}, **setting}, "## Process\nWork\n")
				with patch("auto.integration.process.capture") as shell:
					self.assertTrue(ElaborationValidate(self.ctx, self.plan, self.elaboration(file)))
					with self.assertRaises(AutoError):
						ElaborationApply(self.ctx, self.plan, self.elaboration(file), "known-invalid-settings")
					self.assertEqual(shell.call_count, 0)
		file = self.template({"title": "Resolved settings", "temperature": "{{ params.label }}", "params": {"label": "!printf invalid"}}, "## Process\nWork\n")
		value = self.elaboration(file)
		with patch("auto.integration.process.capture", return_value=("invalid", "", 0)) as shell:
			self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
			with self.assertRaisesRegex(AutoError, "temperature must be a finite number"):
				ElaborationApply(self.ctx, self.plan, value, "resolved-invalid-settings")
			self.assertEqual(shell.call_count, 1)
		self.assertFalse((self.plan / "tasks/work.md").exists())
		self.assertEqual(journal(self.plan), [])

	def testShellBackedReferenceSettingsResolveBeforeConfiguredShellParameters(self):
		file = self.template({"title": "Reference settings", "cwd": "./{{ params.directory }}",
			"params": {"label": "!printf result", "directory": "git:status"}}, "## Process\n{{ params.label }}\n")
		target = file.parent / "target"
		target.mkdir()
		value = self.elaboration(file)
		def captured(options, args, config, stdin=""):
			return ("target", "", 0) if args[-1].startswith("git ") else ("Frozen", "", 0)
		with patch("auto.integration.process.capture", side_effect=captured) as shell:
			self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
			self.assertEqual(shell.call_count, 0)
			ElaborationApply(self.ctx, self.plan, value, "reference-settings")
			# Recording the revision may reread git provenance for PlanStatus.
			configured = [call for call in shell.call_args_list if call.args[1][-1] == "printf result"]
			self.assertEqual(len(configured), 1)
			self.assertEqual(configured[0].args[2]["cwd"], str(target))
		self.assertIn("Frozen", TaskExpand(self.ctx, "work"))

	def testNativeEnvironmentMappingsMergePerKeyWithCallerSettings(self):
		file = self.template({"title": "Native environment overrides", "env": "{{ params.environment }}",
			"params": {"environment": {"MODE": "local", "EXTRA": "kept"}, "label": '!printf "%s:%s" "$MODE" "$EXTRA"'}},
			"## Process\n{{ params.label }}\n@./child.md\n")
		(file.parent / "child.md").write_text(documentText({"env": "{{ params.childEnvironment }}",
			"params": {"childEnvironment": {"MODE": "unused", "CHILD": "own"},
				"child": '!printf "%s:%s:%s" "$MODE" "$EXTRA" "$CHILD"'}}, "{{ params.child }}"))
		context = self.ctx.derive(config={**self.ctx.config, "env": {"MODE": "caller"}})
		value = self.elaboration(file)
		self.assertEqual(ElaborationValidate(context, self.plan, value), [])
		ElaborationApply(context, self.plan, value, "native-env-overrides")
		text = TaskExpand(context, "work")
		self.assertIn("caller:kept", text)
		self.assertIn("caller:kept:own", text)

	def testInheritedEnvironmentValuesAreDataAcrossFurtherCompilation(self):
		marker = self.root / "unexpected.txt"
		payload = "{{ params.missing }}; touch " + str(marker)
		file = self.template({"title": "Literal environment", "env": {"MODE": "{{ params.mode }}"},
			"params": {"mode": "\\" + payload, "label": '!printf "%s" "$MODE"'}}, "## Process\n{{ params.label }}\n@./child.md\n")
		(file.parent / "child.md").write_text(documentText({"params": {"nested": '!printf "%s" "$MODE"'}}, "{{ params.nested }}"))
		value = self.elaboration(file)
		self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
		ElaborationApply(self.ctx, self.plan, value, "literal-configuration")
		self.assertEqual(TaskExpand(self.ctx, "work").count(payload), 2)
		self.assertFalse(marker.exists())

	def testWholeMetadataMappingsKeepParameterStringsInertAtValueBoundaries(self):
		marker = self.root / "unexpected.txt"
		payload = "!touch " + str(marker)
		outline = "{{ params.missing }}\n" + payload
		file = self.template({"title": "Native metadata mappings", "inputs": "{{ params.bindings }}", "output": "{{ params.contract }}",
			"params": {"bindings": {"danger": payload, "object": {"note": "git:status"}},
				"contract": {"format": "text", "template": outline, "schema": {"type": "string"}}}},
			"## Process\n{{ inputs.danger }}|{{ inputs.object.note }}\n")
		value = self.elaboration(file)
		with patch("auto.integration.process.capture", side_effect=AssertionError("Parameter data became executable")):
			self.assertEqual(ElaborationValidate(self.ctx, self.plan, value), [])
			ElaborationApply(self.ctx, self.plan, value, "native-metadata-mappings")
			task = TaskGet(self.ctx, "work")
			self.assertEqual(task.meta["inputs"]["danger"], "\\" + payload)
			self.assertEqual(task.meta["inputs"]["object"], {"note": "git:status"})
			self.assertEqual(task.meta["output"]["template"], "\\" + outline)
			self.assertIn(payload + "|git:status", TaskExpand(self.ctx, "work"))
		self.assertFalse(marker.exists())

	def testNestedSettingsCanComeFromShellBackedOwnersWithoutBypassingPolicy(self):
		ref = "auto:catalogue/data/directory"
		def resolve(context, target):
			data = Shell(context, "printf target")[0].encode()
			return Content(target, data, digest(data), self.root / "directory.txt")
		context = self.ctx.derive(resolvers={ref: resolve})
		file = self.template({"title": "Owned configuration"}, "## Process\n@./child.md\n")
		(file.parent / "child.md").write_text(documentText({"cwd": "./{{ params.directory }}",
			"params": {"directory": ref, "label": "!printf result"}}, "{{ params.label }}"))
		target = file.parent / "target"
		target.mkdir()
		value = self.elaboration(file)
		def captured(options, args, config, stdin=""):
			return ("target", "", 0) if args[-1] == "printf target" else ("Frozen", "", 0)
		with patch("auto.integration.process.capture", side_effect=captured) as shell:
			self.assertEqual(ElaborationValidate(context, self.plan, value), [])
			self.assertEqual(shell.call_count, 0)
			denied = context.derive(defaults={"policy": {"shell.run": "deny"}})
			with self.assertRaisesRegex(AutoError, "Shell execution is disabled"):
				ElaborationApply(denied, self.plan, value, "owned-config-denied")
			self.assertEqual(shell.call_count, 0)
			ElaborationApply(context, self.plan, value, "owned-config-allowed")
			configured = [call for call in shell.call_args_list if call.args[1][-1] == "printf result"]
			self.assertEqual(len(configured), 1)
			self.assertEqual(configured[0].args[2]["cwd"], str(target))
		self.assertIn("Frozen", TaskExpand(context, "work"))
