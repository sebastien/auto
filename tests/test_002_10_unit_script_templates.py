"""Module: test_script_templates — Scripts inheriting templates with `from`, and recorded runs."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from auto.features.script.model import Script
from auto.features.script.operations import ScriptExpand, ScriptInputs, ScriptStart, loadScript
from auto.features.script.runs import ScriptList, ScriptOutput, ScriptRun
from auto.runtime.config import validateConfig
from auto.runtime.model import Context
from auto.runtime.operations import Resolve
from auto.utils.data import AutoError

TEMPLATE = """---
kind: task
identity: auto:catalogue/task/review
title: Review
access: read
artifact: ../artifact/report.md
inputs:
  facet: ""
  scope: []
  criteria: ""
  brief: ""
  baseline: ""
params:
  depth: shallow
---
## Process

Facet: {{ facet }}
{% for item in scope %}- scope: {{ item }}
{% endfor %}Criteria: {{ criteria }}
Brief: {{ brief }}
Baseline: {{ baseline }}
Depth: {{ params.depth }}

@./review.extra.md

## Post

Ignored by scripts.
"""

ARTIFACT = """---
kind: artifact
output:
  format: markdown
  template: ./report.template.md
---
# Report

## Post

!?grep -q PASS {{ run.output }}
"""


class ScriptTemplateTests(unittest.TestCase):
	def setUp(self):
		self.temp = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp.cleanup)
		self.root = Path(self.temp.name)
		catalogue = self.root / ".auto/catalogue"
		(catalogue / "task").mkdir(parents=True)
		(catalogue / "artifact").mkdir()
		(catalogue / "task/review.md").write_text(TEMPLATE)
		(catalogue / "task/review.extra.md").write_text("Extra from the template folder.")
		(catalogue / "artifact/report.md").write_text(ARTIFACT)
		(catalogue / "artifact/report.template.md").write_text("## Verdict\n")
		self.outputs, self.prompts = [], []
		class Fake:
			def start(inner, context, request):
				self.prompts.append(request)
				yield {"type": "ScriptMessage", "content": "working"}
				yield {"type": "ScriptEnded", "status": "Succeeded", "output": self.outputs.pop(0) if self.outputs else "PASS"}
		self.ctx = Context(project=self.root, engines={"fake": Fake()}, config={"engine": "fake"})

	def write(self, text, name="audit.md"):
		path = self.root / name
		path.parent.mkdir(parents=True, exist_ok=True)
		path.write_text(text)
		return str(path)

	def testSectionsFillInputsAndTheRestFillsBrief(self):
		(self.root / "notes.txt").write_text("child data")
		path = self.write("---\nfrom: task/review\ninputs: {facet: docs}\nparams: {depth: deep}\n---\nKeep docs clean.\n\n"
			"## Scope\n\n- docs/\n- README\n  .md\n\n## Criteria\n\n- DOC-01: !not a shell value\n\n## Notes\n\nMore.\n")
		loaded = loadScript(self.ctx, path)
		self.assertEqual(loaded.template["ref"], "auto:catalogue/task/review")
		self.assertEqual(loaded.meta["access"], "read")
		self.assertNotIn("identity", loaded.meta)
		self.assertTrue(loaded.meta["output"]["template"].endswith("artifact/report.template.md"))
		prompt = ScriptExpand(self.ctx, path)
		self.assertIn("Facet: docs", prompt)
		self.assertIn("- scope: docs/\n- scope: README .md", prompt)
		self.assertIn("Criteria: - DOC-01: !not a shell value", prompt)
		self.assertIn("Brief: Keep docs clean.\n\n#### Notes\n\nMore.", prompt)
		self.assertIn("Depth: deep", prompt)
		self.assertIn("Extra from the template folder.", prompt)
		self.assertIn("## Verdict", prompt)
		self.assertNotIn("Ignored by scripts", prompt)

	def testSectionAndFrontmatterCannotGiveTheSameInput(self):
		path = self.write("---\nfrom: task/review\ninputs: {criteria: x}\n---\n## Criteria\n\ny\n")
		with self.assertRaisesRegex(AutoError, "both"):
			loadScript(self.ctx, path)

	def testTemplatesWithoutBriefAppendTheRestAndNest(self):
		(self.root / "shared").mkdir()
		(self.root / "shared/base.md").write_text("---\ninputs: {topic: ''}\n---\nBase on {{ topic }}.\n")
		(self.root / "shared/middle.md").write_text("---\nfrom: ./base.md\nmodel: middle\n---\n## Topic\n\nnesting\n")
		(self.root / "local.txt").write_text("Local file")
		path = self.write("---\nfrom: ./shared/middle.md\n---\n## Extra\n\n@./local.txt\n")
		loaded = loadScript(self.ctx, path)
		self.assertEqual(loaded.meta["model"], "middle")
		self.assertEqual(loaded.template["templates"], [str(self.root / "shared/base.md")])
		prompt = ScriptExpand(self.ctx, path)
		self.assertIn("Base on nesting.", prompt)
		self.assertIn("## Extra", prompt)
		# Relative references of the specialising script keep their origin.
		self.assertIn("Local file", prompt)

	def testNestedTemplateContentResolvesFromItsAuthor(self):
		(self.root / "base").mkdir()
		(self.root / "base/part.txt").write_text("Base part")
		(self.root / "base/base.md").write_text("Base\n@./part.txt\n")
		(self.root / "mid").mkdir()
		(self.root / "mid/middle.md").write_text("---\nfrom: ../base/base.md\n---\n")
		self.assertIn("Base part", ScriptExpand(self.ctx, self.write("---\nfrom: ./mid/middle.md\n---\n")))

	def testRecordedRunsAreReferenced(self):
		final = list(ScriptRun(self.ctx, Script("Hi", self.root / "s.md", {})))[-1]
		ref = "auto:runs/" + final["run"]
		self.assertEqual(Resolve(self.ctx, ref).text, "PASS")
		self.assertEqual(json.loads(Resolve(self.ctx, ref + "/receipt").text)["status"], "Succeeded")
		self.assertIn("ScriptEnded", Resolve(self.ctx, ref + "/journal").text)
		self.assertIn("Report: PASS", ScriptExpand(self.ctx.derive(inputs={"report": ref}), Script("Report: {{ report }}", self.root / "t.md", {})))
		for bad in ("auto:runs/missing", ref + "/other"):
			with self.subTest(ref=bad), self.assertRaises(AutoError):
				Resolve(self.ctx, bad)

	def testRecursiveTemplatesFail(self):
		path = self.write("---\nfrom: ./audit.md\n---\nLoop\n")
		with self.assertRaisesRegex(AutoError, "Recursive"):
			loadScript(self.ctx, path)

	def testRequiredInputsAreReportedAndChecked(self):
		script = Script("Scope: {{ scope }}", self.root / "s.md", {"inputs": {"scope": None}})
		self.assertEqual(ScriptInputs(self.ctx, script, missingOnly=True)["inputs"], ["scope"])
		with self.assertRaisesRegex(AutoError, "Missing required input: scope"):
			ScriptExpand(self.ctx, script)
		given = self.ctx.derive(inputs={"scope": "src/"})
		self.assertEqual(ScriptInputs(given, script, missingOnly=True)["inputs"], [])
		self.assertIn("Scope: src/", ScriptExpand(given, script))

	def testAccessIsValidatedAndWarnsWhenNotEnforced(self):
		with self.assertRaisesRegex(AutoError, "access"):
			validateConfig({"access": "none"})
		events = list(ScriptStart(self.ctx, Script("Hi", self.root / "s.md", {"access": "read"})))
		self.assertEqual([event["value"] for event in events if event.get("name") == "warning"],
			["The fake engine cannot enforce access: read; the run may change files"])
		self.assertEqual(self.prompts[-1]["config"]["access"], "read")

	def testRunsAreRecordedWithTheirOutput(self):
		path = self.write("---\nfrom: task/review\n---\n## Scope\n\n- docs/\n")
		events = list(ScriptRun(self.ctx, path))
		final = events[-1]
		self.assertEqual(final["status"], "Succeeded", final)
		directory = self.root / ".auto/runs" / final["run"]
		self.assertEqual(final["outputPath"], str(directory / "output.md"))
		self.assertEqual(ScriptOutput(self.ctx, final["run"]), "PASS")
		receipt = json.loads((directory / "receipt.json").read_text())
		self.assertEqual(receipt["from"]["ref"], "auto:catalogue/task/review")
		self.assertEqual(receipt["script"], path)
		self.assertEqual(receipt["match"], {"from": "auto:catalogue/task/review", "facet": "", "scope": ["docs/"]})
		self.assertFalse((directory / "active.json").exists())
		journal = [json.loads(line) for line in (directory / "journal.jsonl").read_text().splitlines()]
		self.assertEqual(journal[-1]["run"], final["run"])
		self.assertEqual([run["id"] for run in ScriptList(self.ctx)], [final["run"]])
		with self.assertRaisesRegex(AutoError, "not found"):
			ScriptOutput(self.ctx, "run-missing")

	def testArtifactShellChecksRunOnTheStoredOutput(self):
		path = self.write("---\nfrom: task/review\n---\n")
		self.outputs = ["FAIL"]
		final = list(ScriptRun(self.ctx, path))[-1]
		self.assertEqual(final["status"], "Failed")
		self.assertEqual(final["failure"]["kind"], "output")
		self.assertEqual(final["output"], "FAIL")
		# A failed run keeps its output for inspection, but is never a baseline.
		self.assertEqual(ScriptOutput(self.ctx, final["run"]), "FAIL")

	def testBaselineComesFromTheLatestSucceededRunOfTheSameScope(self):
		docs = self.write("---\nfrom: task/review\n---\n## Scope\n\n- docs/\n")
		src = self.write("---\nfrom: task/review\n---\n## Scope\n\n- src/\n", "src-audit.md")
		self.outputs = ["PASS one", "FAIL two", "PASS three", "PASS four"]
		first = list(ScriptRun(self.ctx, docs))[-1]
		list(ScriptRun(self.ctx, docs))
		self.assertIn("Baseline: PASS one", self.prompts[1]["prompt"])
		list(ScriptRun(self.ctx, src))
		self.assertIn("Baseline: \n", self.prompts[2]["prompt"])
		events = list(ScriptRun(self.ctx, docs))
		self.assertIn("Baseline: PASS one", self.prompts[3]["prompt"])
		self.assertIn({"type": "ScriptMeta", "name": "baseline", "value": first["run"]}, events)
		explicit = list(ScriptRun(self.ctx.derive(inputs={"baseline": "given"}), docs))
		self.assertIn("Baseline: given", self.prompts[4]["prompt"])
		self.assertNotIn("baseline", json.loads((self.root / ".auto/runs" / explicit[-1]["run"] / "receipt.json").read_text()))

	def testReadOnlyRunsReportChangedFiles(self):
		subprocess.run(["git", "init", "-q", str(self.root)], check=True)
		(self.root / "kept.txt").write_text("before")
		class Writer:
			def start(inner, context, request):
				(self.root / "kept.txt").write_text("after")
				yield {"type": "ScriptEnded", "status": "Succeeded", "output": "PASS"}
		context = self.ctx.derive(engines={"fake": Writer()})
		events = list(ScriptRun(context, Script("Hi", self.root / "s.md", {"access": "read"})))
		self.assertIn("This read-only run changed files: kept.txt", [event.get("value") for event in events])
		self.assertEqual(events[-1]["status"], "Succeeded")
		receipt = json.loads((self.root / ".auto/runs" / events[-1]["run"] / "receipt.json").read_text())
		self.assertEqual(receipt["changes"], [{"path": "kept.txt", "change": "modified"}])

	def testClosingTheStreamRecordsACancelledRun(self):
		stream = ScriptRun(self.ctx, Script("Hi", self.root / "s.md", {}))
		self.assertEqual(next(stream)["type"], "ScriptMessage")
		stream.close()
		[directory] = list((self.root / ".auto/runs").iterdir())
		receipt = json.loads((directory / "receipt.json").read_text())
		self.assertEqual(receipt["status"], "Cancelled")
		self.assertFalse((directory / "active.json").exists())


if __name__ == "__main__":
	unittest.main()
