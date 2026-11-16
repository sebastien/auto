"""Module: test_prompt — Incomplete commands are completed interactively (see spec-011)."""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from unittest.mock import patch

try:
	from prompt_toolkit.input import create_pipe_input
	from prompt_toolkit.output.plain_text import PlainTextOutput
except ImportError:
	raise unittest.SkipTest("the interactive extra is not installed") from None

from auto.app.cli import main as cli
from auto.app.cli import prompt
from auto.app.cli.prompt import Prompter
from auto.app.operations import DESCRIPTORS, EXPOSURES, Descriptor
from auto.runtime.model import Event

ESC_ENTER = "\x1b\r"


class PromptTests(unittest.TestCase):
	@contextmanager
	def prompter(self, keys: str):
		with create_pipe_input() as pipe:
			pipe.send_text(keys)
			self.screen = io.StringIO()
			yield Prompter(input=pipe, output=PlainTextOutput(self.screen), history=False)

	@contextmanager
	def session(self, keys: str):
		with self.prompter(keys) as prompter, patch.object(prompt, "session", lambda argv: prompter):
			yield prompter

	def invoke(self, *argv) -> tuple[int, str]:
		out = io.StringIO()
		with redirect_stdout(out):
			status = cli.main(list(argv))
		return status, out.getvalue()

	def testNonInteractiveSessions(self):
		with patch.dict(os.environ, {"AUTO_NO_INPUT": "1"}):
			self.assertFalse(prompt.interactive([]))
		self.assertFalse(prompt.interactive(["--no-input"]))
		self.assertFalse(prompt.interactive(["--format", "json"]))
		self.assertFalse(prompt.interactive(["--format=json"]))
		with patch.object(prompt, "find_spec", lambda name: None):
			self.assertFalse(prompt.interactive([]))

	def testPickerFiltersAndMoves(self):
		options = [("alpha", "first"), ("beta", "second"), ("gamma", "third")]
		with self.prompter("\r") as prompter:
			self.assertEqual(prompter.choose("Pick", options), "alpha")
		with self.prompter("\x1b[B\x1b[B\r") as prompter:
			self.assertEqual(prompter.choose("Pick", options), "gamma")
		with self.prompter("thd\r") as prompter:
			self.assertEqual(prompter.choose("Pick", options), "gamma")
		self.assertIn("Pick › gamma", self.screen.getvalue())
		with self.prompter("\r") as prompter:
			self.assertEqual(prompter.choose("Pick", options, default="beta"), "beta")

	def testPickerIsStrict(self):
		with self.prompter("nope\r\x15bet\r") as prompter:
			self.assertEqual(prompter.choose("Pick", [("alpha", ""), ("beta", "")]), "beta")
		with self.prompter("\x03") as prompter, self.assertRaises(KeyboardInterrupt):
			prompter.choose("Pick", [("alpha", "")])

	def testFuzzyScoring(self):
		self.assertIsNone(prompt.fuzzy("xyz", "status"))
		self.assertEqual(prompt.fuzzy("", "status"), (0, 6))
		self.assertLess(prompt.fuzzy("sta", "status"), prompt.fuzzy("sta", "show tasks"))
		self.assertIsNotNone(prompt.fuzzy("run fail", "build-01 · run Failed"))

	def testMultilineSubmitsWithEscEnter(self):
		with self.prompter("line one\rline two" + ESC_ENTER) as prompter:
			self.assertEqual(prompter.ask("goal", DESCRIPTORS["goal"], None), "line one\nline two")

	def testValidationUsesCoercion(self):
		check = lambda text: cli.coerceArguments(EXPOSURES[0], {"since": text})
		with self.prompter("soon\r\x1512\r") as prompter:
			self.assertEqual(prompter.ask("since", Descriptor(), None, check=check), "12")

	def testOptionalValuesCanBeSkipped(self):
		with self.prompter("\r") as prompter:
			self.assertIsNone(prompter.ask("budget", DESCRIPTORS["budget"], None, optional=True))

	def testBudgetPromptValidatesDomainBeforeAccepting(self):
		exposure = next(item for item in EXPOSURES if item.name == "PlanCreate")
		attempts = []
		def check(text):
			attempts.append(text)
			return cli.coerceArguments(exposure, {"budget": text})
		with self.prompter("oops\r\x15cost: 2\r") as prompter:
			self.assertEqual(prompter.ask("budget", DESCRIPTORS["budget"], None, check=check), "cost: 2")
		self.assertIn("oops", attempts)

	def testNamespacedMissingInputCanBeFilledAndExpanded(self):
		from auto.features.script.operations import ScriptExpand
		from auto.runtime.model import Context
		with tempfile.TemporaryDirectory() as directory:
			root = Path(directory)
			file = root / "script.md"
			file.write_text("Hello {{ inputs.name }}")
			context = Context(project=root)
			self.assertEqual(cli.missingInputs(context, str(file)), ["name"])
			with self.prompter("Ada\r") as prompter:
				name = cli.missingInputs(context, str(file))[0]
				context.inputs[name] = prompter.ask(name, Descriptor("value"), context)
			self.assertTrue(ScriptExpand(context, str(file)).startswith("Hello Ada"))

	def testEndOfInputCancels(self):
		with self.prompter("\x04") as prompter, self.assertRaises(KeyboardInterrupt):
			prompter.ask("rationale", DESCRIPTORS["rationale"], None)

	def testQuestions(self):
		with self.prompter("\r") as prompter:
			self.assertIs(prompter.question({"type": Event.POLICY_REQUEST, "content": "Write files?"}), True)
		with self.prompter("blue\rbecause\r") as prompter:
			event = {"type": Event.SCRIPT_QUESTION, "content": "Choose", "fields": [
				{"id": "colour", "title": "Colour", "options": ["red", "blue"]}, {"id": "why", "type": "text"}]}
			self.assertEqual(prompter.question(event), {"colour": "blue", "why": "because"})

	def testQuestionFieldsHonorKindsConstraintsAndBackendKeys(self):
		fields = [
			{"key": "count", "title": "Count", "type": "integer", "minimum": 2, "maximum": 4},
			{"key": "enabled", "type": "boolean"},
			{"id": "notes", "kind": "multiline", "label": "Review notes"},
			{"id": "optional", "required": False},
			{"key": "hidden", "hidden": True, "default": "kept"},
			{"key": "not-needed", "when": [{"key": "count", "op": "eq", "value": 4}]}]
		with self.prompter("one\r\x153\rfalse\rline one\rline two" + ESC_ENTER + "\r") as prompter:
			result = prompter.question({"content": "Review", "fields": fields})
		self.assertEqual(result, {"count": 3, "enabled": False, "notes": "line one\nline two", "hidden": "kept"})
		self.assertIn("Review notes", self.screen.getvalue())

	def testStructuredAndPathQuestionFieldsUseCommonValidation(self):
		with tempfile.TemporaryDirectory() as directory:
			file = Path(directory) / "answer.md"
			file.write_text("Answer")
			fields = [{"id": "data", "kind": "structured", "schema": {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}},
				{"id": "file", "kind": "path", "suffix": ".md"}]
			with self.prompter("x: no\r\x15x: 2\r" + str(file) + "\r") as prompter:
				result = prompter.question({"content": "Choose", "fields": fields})
			self.assertEqual(result, {"data": {"x": 2}, "file": str(file)})

	def testInvalidQuestionSchemasFailBeforePrompting(self):
		with self.prompter("") as prompter, self.assertRaisesRegex(cli.AutoError, "Invalid question field schema"):
			prompter.question({"fields": [{"id": "count", "type": "integer", "minimum": "not a number"}]})
		with self.prompter("") as prompter, self.assertRaisesRegex(cli.AutoError, "originating interface"):
			prompter.question({"fields": [{"id": "external", "type": "external"}]})

	def testArgumentDescriptorCanOverrideDisplayLabel(self):
		with self.prompter("hello\r") as prompter:
			self.assertEqual(prompter.ask("internal", Descriptor(label="Friendly label"), None), "hello")
		self.assertIn("Friendly label", self.screen.getvalue())

	def testMissingVerbIsChosen(self):
		with self.session("list\r"):
			status, output = self.invoke("x", "catalogue")
		self.assertEqual(status, 0)
		self.assertIn("auto:catalogue/", output)

	def testMissingScriptInputsAreAskedAndEchoed(self):
		with tempfile.TemporaryDirectory() as directory:
			script = Path(directory) / "hello.md"
			script.write_text("Hello {{ name }}")
			with self.session("world\r"):
				status, output = self.invoke("x", "script", "expand", str(script))
		self.assertEqual(status, 0)
		self.assertIn("Hello world", output)
		self.assertIn(f"› auto x script expand {script} --input name=world", self.screen.getvalue())

	def testTransitiveDefaultsAndLoopInputsDoNotPrompt(self):
		from auto.runtime.model import Context
		with tempfile.TemporaryDirectory() as directory:
			root = Path(directory)
			child = root / "child.md"
			child.write_text('---\ninputs:\n  base: ""\n  rev: ""\n---\n{{ base }}{{ inputs.rev }}{{ extra }}')
			script = root / "script.md"
			script.write_text('---\neach:\n  rev: [first]\n---\n@./child.md')
			context = Context(project=root)
			self.assertEqual(cli.missingInputs(context, str(script)), ["extra"])
			context.inputs["extra"] = "given"
			self.assertEqual(cli.missingInputs(context, str(script)), [])
			child.write_text('---\ninputs:\n  base: ""\n  rev: ""\n---\n{{ base }}{{ inputs.rev }}')
			with self.session("") as prompter:
				status, _ = self.invoke("x", "script", "expand", str(script))
			self.assertEqual(status, 0)
			self.assertFalse(prompter.filled)

	def testEffectsAreConfirmed(self):
		with tempfile.TemporaryDirectory() as directory, patch("auto.features.elaboration.operations.PlanCreate") as create:
			cwd = os.getcwd()
			os.chdir(directory)
			try:
				with self.session("Ship it" + ESC_ENTER + "\r" + "n\r"):
					status, _ = self.invoke("x", "plan", "create")
			finally:
				os.chdir(cwd)
		self.assertEqual(status, 130)
		create.assert_not_called()
		self.assertIn("› auto x plan create 'Ship it'", self.screen.getvalue())
		self.assertIn("Create and elaborate a plan without running it", self.screen.getvalue())

	def testEveryRequiredArgumentIsDescribed(self):
		for exposure in EXPOSURES:
			for name in exposure.arguments:
				with self.subTest(exposure=exposure.name, argument=name):
					self.assertIn(name, DESCRIPTORS)

	def testCommandLine(self):
		args = cli.parser().parse_args(["x", "gap", "decide", "g1", "yes", "--rationale", "it is fine", "--plan", "p"])
		self.assertEqual(cli.commandLine(args, args.exposure), "auto x gap decide g1 yes --rationale 'it is fine' --plan p")
		args = cli.parser().parse_args(["log", "--follow", "--plan", "p"])
		command = cli.commandLine(args, args.exposure)
		self.assertEqual(command, "auto log --follow --plan p")


if __name__ == "__main__":
	unittest.main()

# EOF
