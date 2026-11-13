"""Windowed housekeeping usage and atomic capacity reservations."""

from __future__ import annotations

import fcntl
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

from auto.features.memory.config import validateAllowance
from auto.features.plan.references import readReceipt
from auto.features.plan.store import locked
from auto.runtime.model import RunStatus, TERMINAL, budgetValue
from auto.utils.data import AutoError, atomicWrite, identifier, jsonText, merge, readJson, timestamp


class AllowanceExceeded(AutoError):
	def __init__(self, report):
		super().__init__("Consolidation allowance exceeded: " + jsonText(report), "budget",
			recovery="Wait for capacity or explicitly use --overrideAllowance for project allowances; user restrictions and per-run budgets still apply.")
		self.report = report


def windowKey(per, at):
	date = datetime.fromisoformat(at.replace("Z", "+00:00"))
	if date.utcoffset() is None:
		raise AutoError("Housekeeping timestamps must include a timezone", "system")
	date = date.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
	if per == "week":
		date -= timedelta(days=date.weekday())
	elif per == "month":
		date = date.replace(day=1)
	return per + ":" + date.date().isoformat()


def constraintsFor(context, name):
	"""Retain earlier configuration caps even when later layers loosen or retime them."""
	layers = [*context.configurationLayers, (str(context.project / ".auto/effective"), context.defaults)]
	result, config = {}, {}
	for source, values in layers:
		config = merge(config, values)
		settings = config.get("housekeeping", {})
		if not isinstance(settings, dict):
			raise AutoError("housekeeping must be a mapping")
		memory = config.get("memory", {})
		if not isinstance(memory, dict) or not isinstance(memory.get("agents", {}), dict):
			raise AutoError("memory.agents must be a mapping")
		agent = memory.get("agents", {}).get(name, {})
		if not isinstance(agent, dict):
			raise AutoError(f"Agent {name} configuration must be a mapping")
		for scope, value in (("project", settings.get("allowance")), ("agent", agent.get("allowance"))):
			if value is None:
				continue
			allowance = validateAllowance(value)
			key = scope + ":" + jsonText(allowance)
			overridable = Path(source).resolve().is_relative_to((context.project / ".auto").resolve())
			previous = result.get(key)
			result[key] = {"scope": scope, "allowance": allowance, "source": previous["source"] if previous and not previous["overridable"] else source,
				"overridable": overridable and (previous is None or previous["overridable"])}
	return list(result.values())


def ledgerRoot(context):
	root = context.project / ".auto/housekeeping"
	root.mkdir(parents=True, exist_ok=True)
	return root


def readLedger(root):
	value = readJson(root / "usage.json", {"runs": {}, "windows": {}})
	try:
		if not isinstance(value, dict) or not isinstance(value.get("runs"), dict) or not isinstance(value.get("windows"), dict):
			raise ValueError("Expected run and window accounts")
		known, totals = {}, {per: {} for per in ("day", "week", "month")}
		for ref, run in value["runs"].items():
			name = identifier(run["agent"])
			if not ref.startswith(f"auto:agent/{name}/run/") or type(run["reserved"]) is not bool or not isinstance(run["status"], str):
				raise ValueError("Invalid run account")
			identifier(ref.removeprefix(f"auto:agent/{name}/run/"))
			budgetValue(run["budget"])
			for dimension, amount in budgetValue(run["usage"]).items():
				known[name, dimension] = known.get((name, dimension), 0) + amount
		for key, bucket in value["windows"].items():
			per, _, date = key.partition(":")
			if per not in totals or windowKey(per, date + "T00:00:00+00:00") != key or not isinstance(bucket["agents"], dict):
				raise ValueError("Invalid window account")
			project, agents = budgetValue(bucket["project"]), {}
			for name, usage in bucket["agents"].items():
				identifier(name)
				for dimension, amount in budgetValue(usage).items():
					agents[dimension] = agents.get(dimension, 0) + amount
					totals[per][name, dimension] = totals[per].get((name, dimension), 0) + amount
			if any(not math.isclose(project.get(dimension, 0), agents.get(dimension, 0), rel_tol=1e-9, abs_tol=1e-9) for dimension in project.keys() | agents.keys()):
				raise ValueError("Project and agent totals differ")
		for total in totals.values():
			if any(not math.isclose(known.get(key, 0), total.get(key, 0), rel_tol=1e-9, abs_tol=1e-9) for key in known.keys() | total.keys()):
				raise ValueError("Run and window totals differ")
	except (AutoError, ValueError, KeyError, TypeError, AttributeError) as error:
		raise AutoError(f"Invalid housekeeping ledger; refusing to reset accounting: {error}", "system") from error
	return value


def foldUsage(ledger, name, run):
	ref = f"auto:agent/{name}/run/{run['id']}"
	previous = ledger["runs"].get(ref, {})
	usage = budgetValue(run.get("usage", {}))
	old = previous.get("usage", {})
	if any(usage.get(key, 0) < amount for key, amount in old.items()):
		raise AutoError("Housekeeping usage cannot decrease", "system")
	at = run.get("usage_at", run.get("ended") or run["started"])
	for per in ("day", "week", "month"):
		bucket = ledger["windows"].setdefault(windowKey(per, at), {"project": {}, "agents": {}})
		for account in (bucket["project"], bucket["agents"].setdefault(name, {})):
			for key, amount in usage.items():
				account[key] = account.get(key, 0) + amount - old.get(key, 0)
	ledger["runs"][ref] = {"agent": name, "usage": usage, "budget": budgetValue(run.get("budget", {})), "status": run["status"],
		"reserved": run.get("housekeeping", {}).get("reserved", run["status"] == RunStatus.RUNNING) and run["status"] not in TERMINAL}


def synchronize(context, ledger):
	"""Recover known usage from receipts; only a free kernel lock releases a dead reservation."""
	for file in sorted((context.project / ".auto/agents").glob("*/runs/*/active.json")):
		run = readReceipt(file.parent) or readReceipt(file.parent)
		if run is None:
			raise AutoError("Agent run disappeared during housekeeping recovery; refusing to release capacity", "system")
		foldUsage(ledger, file.parents[2].name, run)
	for file in sorted((context.project / ".auto/agents").glob("*/runs/*/receipt.json")):
		foldUsage(ledger, file.parents[2].name, readReceipt(file.parent))
	for entry in ledger["runs"].values():
		if not entry["reserved"]:
			continue
		lockFile = context.project / ".auto/agents" / entry["agent"] / ".lock"
		with lockFile.open("a+") as lock:
			try:
				fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
			except BlockingIOError:
				continue
			entry["reserved"] = False
			entry["interrupted"] = True


def reportFor(context, name, ledger, at=None, exclude=None):
	report = []
	for constraint in constraintsFor(context, name):
		allowance = constraint["allowance"]
		key = windowKey(allowance["per"], at or timestamp())
		bucket = ledger["windows"].get(key, {})
		spent = bucket.get("project", {}) if constraint["scope"] == "project" else bucket.get("agents", {}).get(name, {})
		reserved = {}
		for ref, run in ledger["runs"].items():
			if ref == exclude or not run["reserved"] or constraint["scope"] == "agent" and run["agent"] != name:
				continue
			for dimension in allowance.keys() - {"per"}:
				amount = max(0, run["budget"].get(dimension, float("inf")) - run["usage"].get(dimension, 0))
				reserved[dimension] = reserved.get(dimension, 0) + amount
		remaining = {dimension: max(0, maximum - spent.get(dimension, 0) - reserved.get(dimension, 0)) for dimension, maximum in allowance.items() if dimension != "per"}
		report.append({**constraint, "window": key, "used": spent, "reserved": {key: value if value != float("inf") else "unbounded" for key, value in reserved.items()}, "remaining": remaining})
	return report


def recordUsage(context, name, run):
	root = ledgerRoot(context)
	with locked(root):
		ledger = readLedger(root)
		foldUsage(ledger, name, run)
		atomicWrite(root / "usage.json", jsonText(ledger))


def reserve(context, name, path, run, override=False):
	"""Publish a bounded reservation before releasing the project writer lock."""
	if override and context.author != "human":
		raise AutoError("Only an explicit human request may override an allowance")
	root = ledgerRoot(context)
	with locked(root):
		ledger = readLedger(root)
		synchronize(context, ledger)
		ref = f"auto:agent/{name}/run/{run['id']}"
		report = reportFor(context, name, ledger, exclude=ref)
		budget = dict(run["budget"])
		blocked = []
		for item in report:
			if override and item["overridable"]:
				continue
			for dimension, remaining in item["remaining"].items():
				if remaining <= 0 or budget.get(dimension, remaining) > remaining:
					blocked.append(item)
				budget[dimension] = min(budget.get(dimension, remaining), remaining)
		if blocked:
			atomicWrite(root / "usage.json", jsonText(ledger))
			raise AllowanceExceeded(blocked)
		run["budget"] = budget
		run["housekeeping"] = {"reserved": True, "override": override, "allowances": report}
		# Receipt-first ordering lets a later invocation recover a reservation even
		# if the process dies before its ledger write. No model has started yet.
		atomicWrite(path / "active.json", jsonText(run))
		foldUsage(ledger, name, run)
		atomicWrite(root / "usage.json", jsonText(ledger))


def allowanceStatus(context, name):
	root = ledgerRoot(context)
	with locked(root):
		ledger = readLedger(root)
		synchronize(context, ledger)
		atomicWrite(root / "usage.json", jsonText(ledger))
		return reportFor(context, name, ledger)
