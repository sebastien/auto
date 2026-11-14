"""Module: operations — Append engine invocations to, and summarise, the usage journal."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone

from auto.utils.data import AutoError, jsonText

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------


JOURNAL = ".auto/usage.jsonl"
GROUPS = ("day", "month", "project", "plan", "kind", "run", "task", "engine", "provider", "model", "status")
DEFAULT_GROUPS = ("engine", "provider", "model")

# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------


def UsageRecord(context, entry: dict) -> None:
	"""Append one engine invocation to the project usage journal."""
	path = context.project / JOURNAL
	# Ephemeral scripts outside an auto project must not create project state.
	if context.defaults.get("journal", True) is False or not path.parent.is_dir():
		return
	# One O_APPEND write per line keeps concurrent writers from interleaving.
	fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
	try:
		os.write(fd, (jsonText(entry) + "\n").encode())
	finally:
		os.close(fd)


def sinceValue(value: str) -> datetime:
	"""Parse `7d`, `12h`, `2w` or an ISO date/datetime as a UTC lower bound."""
	match = re.fullmatch(r"(\d+)([hdw])", value)
	if match:
		unit = {"h": "hours", "d": "days", "w": "weeks"}[match[2]]
		return datetime.now(timezone.utc) - timedelta(**{unit: int(match[1])})
	try:
		moment = datetime.fromisoformat(value)
	except ValueError as error:
		raise AutoError(f"Invalid since: {value}", recovery="Use a duration like 7d, 12h or 2w, or an ISO date like 2026-11-01.") from error
	return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def UsageEntries(context, since: str | None = None, where: dict | None = None) -> tuple[list[dict], int]:
	"""Return matching journal entries and the count of unreadable lines."""
	path = context.project / JOURNAL
	if not path.is_file():
		return [], 0
	lower = sinceValue(since) if since else None
	unknown = (where or {}).keys() - GROUPS
	if unknown:
		raise AutoError(f"Unknown usage field: {', '.join(sorted(unknown))}", recovery="Filter on: " + ", ".join(GROUPS) + ".")
	result, skipped = [], 0
	for line in path.read_text(encoding="utf-8").splitlines():
		try:
			entry = json.loads(line)
			moment = datetime.fromisoformat(entry["ts"])
		except (ValueError, KeyError, TypeError):
			skipped += 1
			continue
		entry["day"], entry["month"] = entry["ts"][:10], entry["ts"][:7]
		if lower and moment < lower:
			continue
		if any(str(entry.get(key)) != value for key, value in (where or {}).items()):
			continue
		result.append(entry)
	return result, skipped


def UsageSummary(entries: list[dict], by: tuple[str, ...] = DEFAULT_GROUPS) -> list[dict]:
	"""Total runs, failures, duration, tokens and cost per group, in first-seen order."""
	unknown = set(by) - set(GROUPS)
	if unknown:
		raise AutoError(f"Unknown usage grouping: {', '.join(sorted(unknown))}", recovery="Group by: " + ", ".join(GROUPS) + ".")
	groups = {}
	for entry in entries:
		key = tuple(entry.get(name) for name in by)
		row = groups.setdefault(key, {**dict(zip(by, key)), "runs": 0, "failed": 0, "duration_s": 0.0, "tokens": None, "cost": None, "partial": 0})
		row["runs"] += 1
		row["failed"] += entry.get("status") not in ("Succeeded", "Waiting")
		row["duration_s"] += entry.get("duration_s") or 0
		# Totals stay null until some entry reports usage, so "unknown" never reads as zero.
		for name in ("tokens", "cost"):
			if entry.get(name) is not None:
				row[name] = (row[name] or 0) + entry[name]
		# Partial rows under-count: usage was missing or excluded helpers.
		row["partial"] += not entry.get("usage_complete")
	for row in groups.values():
		row["duration_s"] = round(row["duration_s"], 3)
		row["cost"] = None if row["cost"] is None else round(row["cost"], 6)
	return list(groups.values())

# EOF
