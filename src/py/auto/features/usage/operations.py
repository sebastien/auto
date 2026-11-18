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
GROUPS = ("day", "month", "project", "plan", "kind", "phase", "attempt", "run", "task", "engine", "provider", "model", "status")
DEFAULT_GROUPS = ("engine", "provider", "model")
# Token kinds summed by summaries, when engines report them (see runtime.model.DETAIL).
TOKEN_KINDS = ("turns", "input", "output", "reasoning", "cache_read", "cache_write")
# Dollars per million tokens, used to estimate what a run would cost when its engine reports no cost
# (eg. a subscription). Keyed by model, or provider/model; `[prices]` in the configuration overrides them.
PRICES = {
	"gpt-6-luna": {"input": 0.10, "output": 0.50, "cache_read": 0.01},
	"claude-haiku-5-5": {"input": 0.10, "output": 0.50, "cache_read": 0.01},
}

# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------


def priceFor(context, provider: str | None, model: str | None) -> dict | None:
	"""Return the per-million-token prices of a model, configured first, else built in."""
	if not model:
		return None
	configured = context.defaults.get("prices", {})
	for table in (configured, PRICES):
		for key in (f"{provider}/{model}", model):
			if isinstance(table.get(key), dict):
				return table[key]
	return None


def estimate(price: dict, detail: dict) -> float:
	"""Estimate a cost in dollars from a token breakdown; unpriced cache writes cost as input."""
	rates = {"input": price.get("input", 0), "output": price.get("output", 0), "reasoning": price.get("output", 0),
		"cache_read": price.get("cache_read", price.get("input", 0)), "cache_write": price.get("cache_write", price.get("input", 0))}
	return round(sum((detail.get(key) or 0) * rate for key, rate in rates.items()) / 1_000_000, 6)


def UsageRecord(context, entry: dict) -> None:
	"""Append one engine invocation to the project usage journal, estimating its cost when the engine reported none."""
	if not entry.get("cost") and entry.get("input") is not None:
		price = priceFor(context, entry.get("provider"), entry.get("model"))
		if price:
			entry["cost_estimate"] = estimate(price, entry)
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


def stageOf(entry: dict) -> str:
	"""Name what an engine invocation was for: planning, tasks, checks, repairs or memory."""
	kind, phase = entry.get("kind"), entry.get("phase")
	if kind == "memory":
		return "memory"
	if entry.get("attempt"):
		return "repairs"
	if kind == "triage" or kind == "elaboration" and phase in (None, "Process"):
		return "planning"
	if kind == "assessment" or phase not in (None, "Process"):
		return "checks"
	return "tasks"


STAGES = ("planning", "tasks", "checks", "repairs", "memory")


def UsageStages(context, plan: str) -> list[dict]:
	"""Total a plan's engine invocations by stage: runs, duration, turns, tokens and cost, estimated where unreported."""
	entries, _ = UsageEntries(context, where={"plan": plan})
	stages = {}
	for entry in entries:
		stages.setdefault(stageOf(entry), []).append(entry)
	return [{"stage": stage, **UsageSummary(stages[stage], ())[0]} for stage in STAGES if stage in stages]


def UsageModels(context, plan: str) -> dict:
	"""Total a plan's engine invocations per engine, provider and model, and overall."""
	entries, _ = UsageEntries(context, where={"plan": plan})
	if not entries:
		return {"models": [], "total": None}
	return {"models": UsageSummary(entries), "total": UsageSummary(entries, ())[0]}


def UsageSummary(entries: list[dict], by: tuple[str, ...] = DEFAULT_GROUPS) -> list[dict]:
	"""Total runs, failures, duration, tokens and cost per group, in first-seen order."""
	unknown = set(by) - set(GROUPS)
	if unknown:
		raise AutoError(f"Unknown usage grouping: {', '.join(sorted(unknown))}", recovery="Group by: " + ", ".join(GROUPS) + ".")
	groups = {}
	for entry in entries:
		key = tuple(entry.get(name) for name in by)
		row = groups.setdefault(key, {**dict(zip(by, key)), "runs": 0, "failed": 0, "duration_s": 0.0, "tokens": None, "cost": None,
			"cost_estimate": None, **dict.fromkeys(TOKEN_KINDS), "partial": 0})
		row["runs"] += 1
		row["failed"] += entry.get("status") not in ("Succeeded", "Waiting")
		row["duration_s"] += entry.get("duration_s") or 0
		# Totals stay null until some entry reports usage, so "unknown" never reads as zero.
		for name in ("tokens", "cost", *TOKEN_KINDS):
			if entry.get(name) is not None:
				row[name] = (row[name] or 0) + entry[name]
		# The estimate stands in for runs whose engine reported no cost.
		spent = entry.get("cost") or entry.get("cost_estimate")
		if spent is not None:
			row["cost_estimate"] = (row["cost_estimate"] or 0) + spent
		# Partial rows under-count: usage was missing or excluded helpers.
		row["partial"] += not entry.get("usage_complete")
	for row in groups.values():
		row["duration_s"] = round(row["duration_s"], 3)
		for name in ("cost", "cost_estimate"):
			row[name] = None if row[name] is None else round(row[name], 6)
	return list(groups.values())

# EOF
