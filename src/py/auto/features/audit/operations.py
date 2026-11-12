"""Module: operations — Validate audit reports against their artifact contract."""

from __future__ import annotations

from collections import Counter

from auto.features.audit.model import SEVERITIES, STATUSES, VERDICTS, Audit, createAuditFromText
from auto.utils.data import AutoError

# -----------------------------------------------------------------------------
#
# UTILITIES
#
# -----------------------------------------------------------------------------


def expectedVerdict(audit: Audit) -> str:
	"""Derive the verdict from criterion statuses and finding severities."""
	statuses = {criterion.status for criterion in audit.criteria}
	if "fail" in statuses or any(finding.severity == "critical" for finding in audit.findings):
		return "non-compliant"
	return "compliant" if statuses <= {"pass", "n/a"} else "partial"

# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------


def AuditValidate(context, audit) -> list:
	"""Return the violations of an audit report, each with its element and message."""
	violations = []
	def violation(element, message):
		violations.append({"element": element, "message": message})
	try:
		audit = audit if isinstance(audit, Audit) else createAuditFromText(audit)
	except AutoError as error:
		return [{"element": "audit", "message": str(error)}]
	if audit.meta.get("verdict") not in VERDICTS:
		violation("audit", f"Verdict must be one of {', '.join(VERDICTS)}")
	if not audit.scope:
		violation("audit", "Scope lists no audited item")
	if not audit.criteria:
		violation("audit", "Audit lists no criterion")
	for kind, item in audit.malformed:
		violation("audit", f"Malformed {kind}, expected `- ID: status ...`: {item}")
	findings = {}
	for finding in audit.findings:
		element = "finding:" + finding.id
		if finding.id in findings:
			violation(element, "Duplicate finding")
		findings[finding.id] = finding
		if finding.severity not in SEVERITIES:
			violation(element, f"Severity must be one of {', '.join(SEVERITIES)}")
		if not finding.locations:
			violation(element, "Finding lists no location")
	criteria = {}
	for criterion in audit.criteria:
		element = "criterion:" + criterion.id
		if criterion.id in criteria:
			violation(element, "Duplicate criterion")
		criteria[criterion.id] = criterion
		if criterion.status not in STATUSES:
			violation(element, f"Status must be one of {', '.join(STATUSES)}")
		if criterion.status in {"partial", "fail"} and not criterion.findings:
			violation(element, f"A {criterion.status} criterion lists its findings")
		for id in criterion.findings:
			if id not in findings:
				violation(element, f"Unknown finding: {id}")
	for finding in audit.findings:
		if finding.criterion not in criteria:
			violation("finding:" + finding.id, f"Unknown criterion: {finding.criterion or '(none)'}")
	counts = audit.meta.get("findings")
	actual = Counter(finding.severity for finding in audit.findings)
	if not isinstance(counts, dict):
		violation("audit", "Frontmatter `findings` gives the count per severity")
	else:
		for severity in SEVERITIES:
			if (counts.get(severity) or 0) != actual[severity]:
				violation("audit", f"Frontmatter counts {counts.get(severity) or 0} {severity} findings, found {actual[severity]}")
	if audit.criteria and audit.meta.get("verdict") in VERDICTS and audit.meta["verdict"] != expectedVerdict(audit):
		violation("audit", f"Verdict {audit.meta['verdict']} does not match the statuses and severities: expected {expectedVerdict(audit)}")
	return violations

# EOF
