"""Markdown parsing accepts CRLF without rewriting resolved source bytes."""

import unittest

from auto.features.script.operations import ScriptExpand, ScriptInputs
from auto.runtime.model import Content
from auto.runtime.operations import Resolve
from auto.utils.data import AutoError, digest, document, section
import test_002_01_unit_script as fixtures


class LineEndingTests(unittest.TestCase):
	setUp = fixtures.ScriptTests.setUp

	def testDocumentReadsEitherDelimiterAndPreservesBodyLineEndings(self):
		for opening in ("\n", "\r\n", "  \n", "\t\r\n"):
			for closing in ("\n", "\r\n"):
				with self.subTest(opening=opening, closing=closing):
					body = "First\r\nSecond\nThird\r\n"
					self.assertEqual(document("---" + opening + "title: Windows\r\n--- \t" + closing + body),
						({"title": "Windows"}, body))

	def testCrLfFrontmatterRetainsValidation(self):
		for text, error in (("---\r\ntitle: Missing close\r\n", "Unclosed"),
			("---\r\n- list\r\n---\r\n", "mapping"),
			("---\r\nx: 1\r\nx: 2\r\n---\r\n", "Invalid YAML")):
			with self.subTest(text=text), self.assertRaisesRegex(AutoError, error):
				document(text)

	def testFalseLikeFrontmatterStillRequiresAMapping(self):
		for newline in ("\n", "\r\n"):
			for value in ("[]", "false", "0", "''"):
				with self.subTest(newline=newline, value=value), self.assertRaisesRegex(AutoError, "mapping"):
					document("---" + newline + value + newline + "---" + newline)
			self.assertEqual(document("---" + newline + "---" + newline + "Body"), ({}, "Body"))

	def testCrLfSelectorsKeepFrontmatterAndIgnoreHeadingsInsideYaml(self):
		front = '---\r\ninputs: {name: Windows}\r\ndescription: "\r\n## Process\r\n"\r\n---\r\n'
		text = front + "# Intro\r\nIgnored\r\n## Process\r\nHello {{ name }}\r\n### Detail\r\nKept\r\n## Post\r\nDropped\r\n"
		file = self.root / "windows.md"
		file.write_bytes(text.encode())
		content = Resolve(self.ctx, str(file) + "#Process")
		self.assertEqual(content.text, front + "Hello {{ name }}\r\n### Detail\r\nKept\r\n")
		self.assertEqual(content.digest, digest(text.encode()))
		self.assertEqual(content.path, file)
		self.assertEqual(self.ctx.evidence, [{"ref": str(file), "digest": digest(text.encode())}])
		self.assertEqual(ScriptInputs(self.ctx, str(file) + "#Process")["inputs"], ["name"])
		self.assertIn("Hello Windows", ScriptExpand(self.ctx, str(file) + "#Process"))
		self.assertEqual(file.read_bytes(), text.encode())

	def testCrLfOwnedAndLocalSourcesHaveTheSameDefaults(self):
		text = '---\r\ninputs: {name: "./name.txt"}\r\nparams: {style: compact}\r\n---\r\nHello {{ name }} {{ params.style }}\r\n'
		folder = self.root / "sources"
		folder.mkdir()
		file = folder / "windows.md"
		file.write_bytes(text.encode())
		(folder / "name.txt").write_text("Source relative")
		context = self.ctx.derive(resolvers={"owner:": lambda context, ref: Content(ref, text.encode(), digest(text.encode()), file)})
		for ref in (str(file), "owner:windows"):
			with self.subTest(ref=ref):
				self.assertEqual(ScriptInputs(context, ref)["inputs"], ["name"])
				self.assertIn("Hello Source relative compact", ScriptExpand(context, ref))

	def testSelectorsRejectUnclosedFrontmatterInsteadOfSearchingItsHeadings(self):
		for newline in ("\n", "\r\n"):
			with self.subTest(newline=newline), self.assertRaisesRegex(AutoError, "Unclosed"):
				section("---" + newline + 'description: "' + newline + "## Process" + newline + '"' + newline, "Process")
