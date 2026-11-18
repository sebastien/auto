"""Module: model — Audit report fields, parsed from their markdown form."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from auto.utils.data import document, sections

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------


VERDICTS = ("compliant", "partial", "non-compliant")
STATUSES = ("pass", "partial", "fail", "n/a", "unverifiable")
SEVERITIES = ("critical", "major", "minor", "info")


@dataclass(slots=True)
class AuditCriterion:
	"""The status of one criterion, with the findings it lists."""

	id: str
	status: str
	text: str
	findings: list = field(default_factory=list)


@dataclass(slots=True)
class AuditFinding:
	"""One distinct problem against one criterion."""

	id: str
	title: str
	criterion: str = ""
	severity: str = ""
	locations: list = field(default_factory=list)


@dataclass(slots=True)
class Audit:
	"""An audit report: verdict, scope, criteria and findings."""

	meta: dict
	scope: list = field(default_factory=list)
	criteria: list = field(default_factory=list)
	findings: list = field(default_factory=list)
	malformed: list = field(default_factory=list)


# What was done about a finding. `fix` mode fixes; `triage` only proposes (`to-fix`).
RESOLUTION_MODES = ("fix", "triage")
RESOLUTION_STATUSES = ("fixed", "to-fix", "stale", "deferred", "wont-fix", "needs-decision")


@dataclass(slots=True)
class Resolution:
	"""What was done about one finding, with the locations changed."""

	id: str
	title: str
	status: str = ""
	changes: list = field(default_factory=list)


@dataclass(slots=True)
class AuditResolution:
	"""An audit resolution: the selected findings and what was done about each."""

	meta: dict
	items: list = field(default_factory=list)

# -----------------------------------------------------------------------------
#
# UTILITIES
#
# -----------------------------------------------------------------------------


def listItems(text: str) -> list[str]:
	"""Split the top-level items of a markdown list, joining continuation lines."""
	items = []
	for line in text.splitlines():
		if line.startswith("- "):
			items.append(line[2:].strip())
		elif items and line.strip() and line[:1].isspace():
			items[-1] += " " + line.strip()
	return items


def createFinding(key: str, body: str) -> AuditFinding:
	"""Create a finding from its `F1: title` heading and field list."""
	id, _, title = key.partition(":")
	finding, current = AuditFinding(id.strip(), title.strip()), None
	for line in body.splitlines():
		match = re.match(r"^-\s+(criterion|severity|locations)\s*:\s*(.*?)\s*$", line)
		if match:
			current, value = match[1], match[2]
			if current == "locations":
				finding.locations.extend(item.strip() for item in value.split(",") if item.strip())
			else:
				setattr(finding, current, value.strip("`"))
			continue
		item = re.match(r"^\s+-\s+(.+?)\s*$", line)
		if item and current == "locations":
			finding.locations.append(item[1])
		elif line.strip() and not line[:1].isspace():
			current = None
	return finding

# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------


def createAuditFromText(text: str) -> Audit:
	"""Parse an audit report, keeping items it cannot parse as malformed."""
	meta, body = document(text)
	_, parts = sections(body)
	audit = Audit(meta, scope=listItems(parts.get("Scope", "")))
	for item in listItems(parts.get("Criteria", "")):
		match = re.match(r"^`?([A-Za-z][\w.-]*)`?\s*:\s*(n/a|[a-z]+)\b\s*(.*)$", item)
		if not match:
			audit.malformed.append(("criterion", item))
			continue
		audit.criteria.append(AuditCriterion(match[1], match[2], match[3], re.findall(r"\bF\d+\b", match[3])))
	_, findings = sections(parts.get("Findings", ""), 3)
	audit.findings = [createFinding(key, value) for key, value in findings.items()]
	return audit


def createResolutionFromText(text: str) -> AuditResolution:
	"""Parse an audit resolution: its frontmatter and one `### F1: title` section per finding."""
	meta, body = document(text)
	_, parts = sections(body)
	resolution = AuditResolution(meta)
	for key, value in sections(parts.get("Resolutions", ""), 3)[1].items():
		id, _, title = key.partition(":")
		item, current = Resolution(id.strip(), title.strip()), None
		for line in value.splitlines():
			match = re.match(r"^-\s+(status|changes)\s*:\s*(.*?)\s*$", line)
			if match:
				current = match[1]
				if current == "status":
					item.status = match[2].strip("`")
				else:
					item.changes.extend(part.strip() for part in match[2].split(",") if part.strip())
				continue
			nested = re.match(r"^\s+-\s+(.+?)\s*$", line)
			if nested and current == "changes":
				item.changes.append(nested[1])
			elif line.strip() and not line[:1].isspace():
				current = None
		resolution.items.append(item)
	return resolution

# EOF
