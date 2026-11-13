"""Shell checks execute authored directives, never rendered data or examples."""

import unittest

from auto.features.plan.operations import PlanRevise
from auto.features.task.operations import RunCheck, TaskStart
from auto.utils.data import documentText
import test_004_01_integration_planning as fixtures


class CheckSourceTests(unittest.TestCase):
	setUp = fixtures.PlanningTests.setUp

	def add(self, postBody, **meta):
		PlanRevise(self.ctx, self.plan, [{"op": "add", "task": {"id": "work", "title": "Check sources",
			"sections": {"Process": "Produce", "Post": postBody}, **meta}}], "Check source fixture")

	def testRenderedInputAndParameterLinesCannotBecomeShellChecks(self):
		marker = self.root / "unexpected.txt"
		payload = "!?touch " + str(marker)
		self.add("{{ inputs.payload }}\n{{ params.payload }}", inputs={"payload": "\\" + payload}, params={"payload": "\\" + payload})
		self.engine.outputs = iter(["Produced", {"pass": True, "reason": "Reviewed data"}])
		self.assertEqual(TaskStart(self.ctx, "work")["status"], "Succeeded")
		self.assertFalse(marker.exists())
		self.assertEqual(len(self.engine.calls), 2)
		self.assertIn(payload, self.engine.calls[-1])

	def testFencedRawInlineAndShellOutputExamplesStayLiteral(self):
		marker = self.root / "unexpected.txt"
		payload = "!?touch " + str(marker)
		inline = self.root / "example.txt"
		inline.write_text(payload)
		body = "```sh\n" + payload + "\n```\n{% raw %}\n" + payload + "\n{% endraw %}\n{{@" + str(inline) + "}}\n!printf '%s' {{ params.example }}\n"
		self.add(body, params={"example": "\\" + payload})
		self.engine.outputs = iter(["Produced", {"pass": True, "reason": "Reviewed examples"}])
		self.assertEqual(TaskStart(self.ctx, "work")["status"], "Succeeded")
		self.assertFalse(marker.exists())
		self.assertEqual(len(self.engine.calls), 2)
		self.assertIn(payload, self.engine.calls[-1])

	def testAuthoredConditionalAndTranscludedChecksPreserveCommandOrder(self):
		order = self.root / "order.txt"
		fragment = self.root / "checks/fragment.md"
		fragment.parent.mkdir()
		fragment.write_text("{% if params.enabled %}\n!?printf child >> " + str(order) + "\n{% endif %}\n")
		self.add("@" + str(fragment) + "\n!?printf body >> " + str(order) + "\n",
			post="printf meta >> " + str(order), params={"enabled": True})
		run = TaskStart(self.ctx, "work")
		self.assertEqual(run["status"], "Succeeded")
		self.assertEqual(order.read_text(), "metachildbody")
		self.assertEqual(len(self.engine.calls), 1)
		self.assertTrue(RunCheck(self.ctx.derive(params={"enabled": False}), run, "Post")["pass"])
		self.assertEqual(order.read_text(), "metachildbodymetabody")
		self.assertEqual(len(self.engine.calls), 1)

	def testPreAndEvaluationKeepRenderedCheckMarkersAsData(self):
		marker = self.root / "unexpected.txt"
		payload = "!?touch " + str(marker)
		PlanRevise(self.ctx, self.plan, [{"op": "add", "task": {"id": "work", "title": "Check phases",
			"inputs": {"payload": "\\" + payload}, "sections": {"Process": "Produce", "Pre": "{{ inputs.payload }}", "Evaluation": "{{ inputs.payload }}"}}}], "Phase checks")
		self.engine.outputs = iter([{"pass": True, "reason": "Valid data"}, "Produced", {"score": 1, "feedback": "Reviewed data"}])
		run = TaskStart(self.ctx, "work")
		self.assertEqual(run["status"], "Succeeded")
		self.assertEqual(run["evaluation"], {"score": 1, "feedback": "Reviewed data"})
		self.assertFalse(marker.exists())
		self.assertEqual(len(self.engine.calls), 3)

	def testTrimmedRawBlocksAndUnmatchedExamplesDoNotConsumeLaterChecks(self):
		marker = self.root / "unexpected.txt"
		order = self.root / "order.txt"
		payload = "!?touch " + str(marker)
		self.add("```liquid\n{% raw %}\n```\n{%- raw -%}\n" + payload + "\n{%- endraw -%}\n{%- comment -%}\n" + payload +
			"\n{%- endcomment -%}\n!?printf authored >> " + str(order) + "\n")
		self.engine.outputs = iter(["Produced", {"pass": True, "reason": "Reviewed raw example"}])
		self.assertEqual(TaskStart(self.ctx, "work")["status"], "Succeeded")
		self.assertFalse(marker.exists())
		self.assertEqual(order.read_text(), "authored")
		self.assertIn(payload, self.engine.calls[-1])
		self.assertNotIn("{% auto ", self.engine.calls[-1])

	def testLiteralDataInMaterializedPostDoesNotExecuteAsACheck(self):
		from auto.features.elaboration.operations import ElaborationApply
		marker = self.root / "unexpected.txt"
		payload = "!?touch " + str(marker)
		template = self.root / "template.md"
		template.write_text(documentText({"title": "Materialized check", "params": {"payload": "\\" + payload}},
			"## Process\nProduce\n## Post\n{{ params.payload }}\n"))
		ElaborationApply(self.ctx, self.plan, {"task": "root", "tasks": [{"id": "work", "from": str(template)}]}, "materialized-check")
		self.engine.outputs = iter(["Produced", {"pass": True, "reason": "Reviewed literal"}])
		self.assertEqual(TaskStart(self.ctx, "work")["status"], "Succeeded")
		self.assertFalse(marker.exists())
		self.assertIn(payload, self.engine.calls[-1])
