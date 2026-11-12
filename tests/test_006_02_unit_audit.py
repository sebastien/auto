"""Module: test_audit — Audit reports are checked against their artifact contract."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from auto.features.audit.operations import AuditValidate
from auto.features.script.model import Script
from auto.features.script.operations import ScriptExpand
from auto.runtime.model import Context

REPORT = """---
facet: symbol names
verdict: partial
summary: Mostly consistent
coverage: full, 2 of 2 items
findings: {critical: 0, major: 0, minor: 1, info: 0}
---
## Scope

- src/a.py
- src/b.py

## Criteria

- SYM-01: pass — Casing is consistent.
- SYM-02: partial — Domain vocabulary,
  findings: F1
- SYM-03: n/a — Booleans read as predicates.

## Findings

### F1: Two names for one concept

- criterion: SYM-02
- severity: minor
- locations:
  - src/a.py:3
  - src/b.py:9

Evidence.
"""


class AuditTests(unittest.TestCase):
	def messages(self, text):
		return [item["message"] for item in AuditValidate(Context(), text)]

	def testValidReport(self):
		self.assertEqual(self.messages(REPORT), [])

	def testStatusesAndReferences(self):
		text = REPORT.replace("SYM-01: pass", "SYM-01: fine").replace("criterion: SYM-02", "criterion: SYM-09")
		messages = self.messages(text.replace("findings: F1", "findings: F2"))
		self.assertTrue(any("Status must be" in item for item in messages))
		self.assertIn("Unknown criterion: SYM-09", messages)
		self.assertIn("Unknown finding: F2", messages)

	def testCountsAndVerdict(self):
		messages = self.messages(REPORT.replace("minor: 1", "minor: 2").replace("verdict: partial", "verdict: compliant"))
		self.assertTrue(any("counts 2 minor" in item for item in messages))
		self.assertTrue(any("expected partial" in item for item in messages))
		critical = REPORT.replace("severity: minor", "severity: critical").replace("critical: 0, major: 0, minor: 1", "critical: 1, major: 0, minor: 0")
		self.assertTrue(any("expected non-compliant" in item for item in self.messages(critical)))

	def testMissingScopeCriteriaAndLocations(self):
		text = REPORT.replace("- src/a.py\n- src/b.py\n", "").replace("  - src/a.py:3\n  - src/b.py:9\n", "")
		messages = self.messages(text)
		self.assertIn("Scope lists no audited item", messages)
		self.assertIn("Finding lists no location", messages)
		self.assertIn("Audit lists no criterion", self.messages("---\nverdict: partial\n---\n## Scope\n\n- a\n"))

	def testShippedTemplateRendersFromSection(self):
		with tempfile.TemporaryDirectory() as directory:
			root = Path(directory)
			ctx = Context(project=root)
			body = "## Process\n\n### Criteria\n\n- X-01: Something.\n\n@auto:catalogue/task/audit#Process\n"
			result = ScriptExpand(ctx, Script(body, root / "audit-x.md", {"inputs": {"facet": "things"}}))
			self.assertIn("perspective of\n*things*", result)
			self.assertIn("### Method", result)
			self.assertEqual(result.count("## Process"), 1)

# EOF
