"""Module: frontend — Human-facing execution output and terminal progress."""

from __future__ import annotations

import json
import math
import os
import re
import shlex
import sys
import time
import unicodedata

from auto.app.operations import render
from auto.features.guided.operations import failureText
from auto.runtime.config import RUNTIME_DEFAULTS
from auto.runtime.model import Event, RunStatus, budgetValue
from auto.utils.layout import planNumber

# -----------------------------------------------------------------------------
#
# UTILITIES
#
# -----------------------------------------------------------------------------


def safeText(value) -> str:
	"""Make external diagnostic text inert and redact labelled credentials."""
	text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", str(value))
	text = re.sub(r"(?i)((?:password|secret|token|api[_-]?key|authorization|credential)[\w.-]*[\"']?\s*[:=]\s*)[^\n]+", r"\1[redacted]", text)
	text = re.sub(r"(?i)\bBearer\s+[^\s]+", "Bearer [redacted]", text)
	return "".join(char if char.isprintable() else " " for char in text)


def width(text: str) -> int:
	"""Return the terminal display width of `text`."""
	# ponytail: codepoint widths overestimate some emoji; use grapheme widths if exact alignment is needed.
	return sum(0 if unicodedata.combining(char) or char == "\u200d" else 2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)


def clip(text: str, columns: int) -> str:
	"""Truncate `text` to `columns`, appending an ellipsis."""
	if columns <= 0:
		return ""
	if width(text) <= columns:
		return text
	result = ""
	for char in text:
		if width(result + char) > columns - 1:
			break
		result += char
	return result + "…"


def costText(cost, budget=None) -> str:
	"""Format a cost and optional budget for terminal output."""
	def amount(value):
		if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
			return "—"
		return "<$0.0001" if 0 < value < .0001 else f"${value:.4f}"
	return amount(cost) + (" / " + amount(budget) if budget is not None else "")


MARKERS = {RunStatus.SUCCEEDED: ("✓", "32"), RunStatus.RUNNING: ("●", "36"), RunStatus.PENDING: ("●", "36"), RunStatus.WAITING: ("?", "33"),
	RunStatus.FAILED: ("✗", "31"), RunStatus.TIMED_OUT: ("✗", "31"), RunStatus.CANCELLED: ("⊘", "2"), RunStatus.SKIPPED: ("–", "2"),
	RunStatus.BLOCKED: ("○", "2"), "Ready": ("○", "")}
STOPS = {"waiting": "Waiting for your answers", "failed": "Stopped: a task failed", "budget": "Stopped: the budget is spent",
	"held": "Paused", "blocked": "Stopped: nothing can run", "cancelled": "Interrupted", "declined": "Not run", "unplanned": "Planning failed"}
REPLAN = 'auto change "…" to plan it again, saying what to do differently'
CHANGES = {"added": ("+", "32"), "modified": ("~", "33"), "deleted": ("-", "31")}
# Files listed in a summary before the rest are counted.
CHANGES_SHOWN = 12
# One tip ends each plan that succeeds, by plan number, so that every new plan introduces one more command.
TIPS = (
	"auto show TASK prints a task's full output",
	"auto log --follow watches a plan from another terminal",
	'auto change "…" revises a plan; --task TASK revises one task',
	"auto plans lists your plans, and auto use PLAN switches to one",
	"auto \"GOAL\" --budget 'cost: 1' caps what a plan may spend",
	"auto ./script.md runs a Markdown prompt with inputs, without a plan",
	"auto x usage shows what runs cost, by engine and model",
	"memory agents learn from your plans and decisions: see auto learned",
	"auto x --help lists the internals: runs, questions, policy, catalogue",
)


def paint(text: str, code: str, color: bool) -> str:
	"""Wrap `text` in an ANSI style when `color` is enabled."""
	return f"\033[{code}m{text}\033[0m" if color and code else text


def colorFor(stream) -> bool:
	"""Tell whether styled text may be written to `stream`."""
	return stream.isatty() and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb"


def durationText(seconds) -> str:
	"""Format a duration in seconds as `4m12s`."""
	seconds = max(0, round(seconds or 0))
	minutes, seconds = divmod(seconds, 60)
	return f"{minutes // 60}h{minutes % 60:02d}m" if minutes >= 60 else f"{minutes}m{seconds:02d}s" if minutes else f"{seconds}s"


def moneyText(cost) -> str:
	"""Format a cost briefly, or nothing when unknown."""
	if type(cost) not in (int, float) or not math.isfinite(cost) or cost <= 0:
		return ""
	return "<$0.01" if cost < .01 else f"${cost:.2f}"


def countText(count) -> str:
	"""Format a token count briefly: `850`, `12.3k`, `1,234k`, `1.4M`, `1,250M`."""
	count = int(count)
	if count < 1000:
		return str(count)
	value, unit = (count / 1_000_000, "M") if count >= 1_000_000 else (count / 1000, "k")
	return (f"{value:,.1f}".removesuffix(".0") if value < 100 else f"{round(value):,}") + unit


def spendingText(stages: list) -> str:
	"""Format what each stage of a plan spent: tokens, model turns and cost, marked `≈` when estimated."""
	parts = []
	for stage in stages:
		facts = [stage["stage"]]
		if stage.get("tokens") is not None:
			facts.append(countText(stage["tokens"]) + " tokens")
		if stage.get("turns"):
			facts.append(f"{stage['turns']} turn" + ("s" if stage["turns"] != 1 else ""))
		cost = stage.get("cost_estimate")
		if cost:
			facts.append(("≈" if not stage.get("cost") else "") + (moneyText(cost) or "$0"))
		parts.append(" ".join(facts))
	return " · ".join(parts)


def usageText(usage: dict, budget: dict | None = None) -> str:
	"""Format usage and any plan limits, keeping individual run summaries short."""
	plan = budget is not None
	budget = budgetValue(budget)
	parts = [durationText(usage.get("time"))] if usage.get("time") else []
	if "time" in budget:
		parts = [(parts[0] if parts else "0s") + " of " + (durationText(budget["time"]) or "0s")]
	if "tokens" in budget:
		parts.append(f"{countText(usage.get('tokens', 0))} / {countText(budget['tokens'])} tokens")
	elif plan and usage.get("tokens"):
		parts.append(f"{countText(usage['tokens'])} tokens")
	cost = moneyText(usage.get("cost"))
	if budget.get("cost") is not None:
		parts.append((cost or "$0") + " of " + (moneyText(budget["cost"]) or "$0"))
	elif cost:
		parts.append(cost)
	return " · ".join(parts)


def marker(state: str, color: bool) -> str:
	symbol, code = MARKERS.get(state, ("·", ""))
	return paint(symbol, code, color)


def nextStep(value: dict) -> str:
	"""Return the command a person most likely wants next, from a plan status."""
	tasks, details = value["tasks"], value["taskDetails"]
	continuation = value.get("continuation") or {}
	if value.get("hold"):
		return "auto resume"
	if value["gaps"]:
		return "auto answer"
	if continuation and not continuation.get("ended") or any((detail["run"] or {}).get("status") in {RunStatus.PENDING, RunStatus.RUNNING}
			for detail in details.values()):
		return "auto log --follow"
	if value["status"] == "Succeeded":
		return 'auto change "…" to refine it, or auto "<goal>" to start another plan'
	if value["status"] == "Abandoned":
		return 'auto "<goal>" to start another plan'
	failed = [id for id, state in tasks.items() if state in {RunStatus.FAILED, RunStatus.TIMED_OUT} and (id != "root" or len(tasks) == 1)]
	if failed:
		return f"auto show {failed[0]} to see why, then auto go --task {failed[0]} to retry"
	blocked = [task["blocked"] for task in value["policy"]["tasks"].values() if task["blocked"]]
	# Running a guided plan approves what it would confirm: only denied effects need the policy.
	if blocked and value["policy"].get("approval") == "plan" and all(rule == "confirm" for rules in blocked for rule in rules.values()):
		return "auto go"
	if blocked:
		return "auto x policy show to see what policy blocks"
	if list(tasks) == ["root"] and value["failures"]:
		# The plan has no tasks because planning failed.
		return REPLAN
	return "auto go"


def taskLines(tasks: dict, details: dict, color: bool, verbose: bool = False) -> list:
	"""Render plan tasks as one line each, with their marker and what they wait on."""
	shown = [id for id in tasks if id != "root"] or ["root"]
	size = max(width(id) for id in shown)
	lines = []
	for id in shown:
		state, detail = tasks[id], details[id]
		text = f"  {marker(state, color)} {id}{' ' * (size - width(id))}  {safeText(firstLine(detail['goal']))}"
		run = detail.get("run") or {}
		if state == RunStatus.BLOCKED:
			pending = [dependency for dependency in detail["dependencies"] if tasks.get(dependency) != RunStatus.SUCCEEDED]
			if pending:
				text += paint("  ← " + ", ".join(pending), "2", color)
		elif run.get("failure") and state in {RunStatus.FAILED, RunStatus.TIMED_OUT}:
			text += paint("  " + safeText(firstLine(run["failure"].get("reason") or run["failure"])), "31", color)
		if verbose and run:
			text += paint(f"  [{run['id']} {run['status']}]", "2", color)
		lines.append(text)
	return lines


def firstLine(text) -> str:
	return str(text or "").strip().partition("\n")[0]


def planView(value: dict, *, color: bool = False, verbose: bool = False, outcome: dict | None = None) -> str:
	"""Render where a plan stands, what needs the person and what to do next, with the `outcome` summary of an ended plan."""
	tasks, details = value["tasks"], value["taskDetails"]
	children = [id for id in tasks if id != "root"]
	done = sum(tasks[id] == RunStatus.SUCCEEDED for id in children)
	lines = [paint(safeText(value["id"]), "1", color) + "  " + safeText(firstLine(value["goal"]))]
	facts = [value["status"], f"{done}/{len(children)} tasks" if children else "no tasks yet"]
	usage = usageText(value["usage"], value["budget"])
	lines.append(paint(" · ".join(facts + ([usage] if usage else [])), "2", color))
	if value.get("hold"):
		hold = value["hold"]
		reason = hold["reason"] if hold["reason"] not in {"Paused", "Stopped"} else ""
		lines.append(paint(("Paused" if hold["mode"] == "Pause" else "Stopped") + (": " + safeText(reason) if reason else ""), "33", color))
	continuation = value.get("continuation") or {}
	if continuation.get("ended") and continuation.get("error"):
		lines.append(paint("Last run stopped: " + safeText(continuation["error"]), "31", color))
	lines += [""] + taskLines(tasks, details, color, verbose)
	if value["gaps"]:
		lines += ["", paint("Needs you:", "1;33", color)]
		lines += [f"  {index}. {safeText(firstLine(gap.get('question', '')))}" for index, gap in enumerate(value["gaps"], 1)]
	outcome = outcomeLines(outcome, color) if outcome else []
	if outcome:
		lines += [""] + outcome
	if verbose:
		lines += ["", "Path: " + safeText(value["path"])]
		if value["failures"]:
			lines.append("Earlier failures: " + ", ".join(safeText(run) for run in value["failures"]) + " (auto x run get RUN)")
		if value["stale"] or value["staleRuns"]:
			lines.append(f"Stale context: {len(value['stale'])} plan inputs, {len(value['staleRuns'])} runs")
	lines += ["", "Next: " + paint(nextStep(value), "36", color)]
	return "\n".join(lines)


def proposalView(value: dict, *, color: bool = False) -> str:
	"""Render a plan proposed for confirmation."""
	lines = [paint("Plan " + safeText(value["id"]), "1", color) + "  " + safeText(firstLine(value["goal"]))]
	tasks = value["tasks"]
	size = max([width(task["id"]) for task in tasks] or [0])
	for index, task in enumerate(tasks, 1):
		after = paint("  ← " + ", ".join(task["dependencies"]), "2", color) if task["dependencies"] else ""
		lines.append(f"  {index:>2} {task['id']}{' ' * (size - width(task['id']))}  {safeText(firstLine(task['goal']))}{after}")
	if not tasks:
		effects = ", ".join(value.get("effects") or [])
		lines.append("  Runs directly, as one task" + (f" ({effects})" if effects else ""))
	if value.get("reason"):
		lines.append(paint("  " + safeText(value["reason"]), "2", color))
	if value["budget"]:
		lines.append(paint("Budget: " + ", ".join(f"{key} {amount}" for key, amount in value["budget"].items()), "2", color))
	if value["agents"]:
		lines.append(paint("Learning: " + ", ".join(value["agents"]), "2", color))
	return "\n".join(lines)


def summaryView(value: dict, *, color: bool = False) -> str:
	"""Render the end of a guided run: outcome, cost, results and the next step."""
	stop, status = value["stop"], value["status"]
	if stop == "ended" or status in {"Succeeded", "Failed", "Abandoned"}:
		headline = {"Succeeded": paint("Done", "1;32", color), "Failed": paint("Failed", "1;31", color)}.get(status, status)
	else:
		headline = paint(STOPS.get(stop, "Stopped"), "1;33", color)
	facts = [] if stop == "unplanned" else [f"{value['done']}/{value['total']} tasks"]
	usage = usageText(value["usage"], value["budget"])
	if usage:
		facts.append(usage)
	if value["decisions"]:
		facts.append(f"{value['decisions']} decision" + ("s" if value["decisions"] != 1 else ""))
	lines = [" · ".join([headline, *facts])]
	if value.get("reason"):
		lines.append("  " + paint(safeText(value["reason"]), "31", color))
	lines += resultLines(value, color)
	lines += decisionLines(value.get("decided") or [], color)
	lines += usageLines(value.get("models") or {}, value.get("spending") or [], color)
	if value["questions"]:
		lines.append(paint("Needs you:", "1;33", color))
		lines += [f"  {index}. {safeText(firstLine(question))}" for index, question in enumerate(value["questions"], 1)]
	if value["agents"] and stop == "ended":
		lines.append(paint("Learning: " + ", ".join(value["agents"]) + " (auto learned)", "2", color))
	if stop != "declined":
		lines.append(paint("Details: " + detailsHint(value), "2", color))
	lines.append("Next: " + paint(summaryNext(value), "36", color))
	if value["status"] == "Succeeded":
		lines.append(paint("Tip: " + tipFor(value["id"]), "2", color))
	return "\n".join(lines)


def resultLines(value: dict, color: bool) -> list:
	"""Render what a plan produced: each task's result, the failed tasks, then the files changed, or else those its runs wrote."""
	lines = []
	if value["results"]:
		lines.append("Results:")
		size = max(width(result["task"]) for result in value["results"])
		for result in value["results"]:
			lines.append(f"  {marker(RunStatus.SUCCEEDED, color)} {result['task']}{' ' * (size - width(result['task']))}  "
				+ safeText(result["output"] or firstLine(result["goal"])))
	for id in value["failed"]:
		lines.append(f"  {marker(RunStatus.FAILED, color)} {id}")
	if value.get("changed") is not None:
		return lines + changeLines(value["changed"], color)
	written = value.get("written") or []
	if written:
		lines.append(f"Wrote {len(written)} file" + ("s:" if len(written) != 1 else ":"))
		lines += [f"  {paint('~', '33', color)} {safeText(name)}" for name in written[:CHANGES_SHOWN]]
		if len(written) > CHANGES_SHOWN:
			lines.append(paint(f"  … and {len(written) - CHANGES_SHOWN} more", "2", color))
	return lines


def outcomeLines(value: dict, color: bool) -> list:
	"""Render the outcome of an ended plan for its status: results, files, usage and where to read more."""
	lines = resultLines(value, color) + usageLines(value.get("models") or {}, value.get("spending") or [], color)
	return lines + [paint("Details: " + detailsHint(value), "2", color)] if lines else []


def answerText(answer) -> str:
	"""Format a decision's answer on one line: text as is, yes/no, a grant by its effect, anything else as JSON."""
	if isinstance(answer, str):
		return firstLine(answer)
	if isinstance(answer, bool):
		return "yes" if answer else "no"
	if isinstance(answer, dict) and set(answer) == {"grant", "scope"}:
		return f"grant {answer['grant']}"
	return json.dumps(answer, ensure_ascii=False, default=str)


def decisionLines(decided: list, color: bool) -> list:
	"""Render the questions a run had answered, one per line with their answer."""
	if not decided:
		return []
	lines = [f"Decided {len(decided)} question" + ("s:" if len(decided) != 1 else ":")]
	for item in decided:
		question = clip(safeText(firstLine(item["question"])) or "(decision)", 60)
		lines.append(f"  {paint('✓', '32', color)} {question}  {paint('→', '2', color)} {clip(safeText(answerText(item['answer'])), 40)}")
	return lines


def modelLabel(row: dict) -> str:
	"""Name a usage row's model: its engine, then provider/model, or `default` when the engine chose."""
	model = "/".join(str(row[key]) for key in ("provider", "model") if row.get(key))
	return " ".join(filter(None, [str(row.get("engine") or ""), model or "default"]))


def usageLines(models: dict, stages: list, color: bool) -> list:
	"""Render tokens and cost per model and in total, `≈` marking estimates, then what each stage spent."""
	rows = models.get("models") or []
	if not rows:
		return []
	items = []
	for label, row in [(safeText(modelLabel(row)), row) for row in rows] + [("total", models["total"])]:
		tokens = countText(row["tokens"]) + " tokens" if row.get("tokens") is not None else "— tokens"
		cost = row.get("cost_estimate")
		# Unknown costs stay blank rather than reading as free.
		money = "" if cost is None else ("≈" if not row.get("cost") and cost else "") + (moneyText(cost) or "$0")
		items.append((label, tokens, money))
	size, room = max(width(label) for label, _, _ in items), max(width(tokens) for _, tokens, _ in items)
	lines = ["Usage:"]
	for label, tokens, money in items:
		text = f"  {label}{' ' * (size - width(label))}  {tokens:>{room}}  {money}".rstrip()
		lines.append(paint(text, "1", color) if label == "total" and len(rows) > 1 else text)
	spending = spendingText(stages)
	if spending:
		lines.append(paint("  by stage: " + spending, "2", color))
	return lines


def detailsHint(value: dict) -> str:
	"""Name the commands that show a run in detail: a task's output, the plan's log and its usage."""
	# A failure explains itself; otherwise the last result is the plan's conclusion.
	task = (value["failed"] or [result["task"] for result in value["results"]][-1:] or ["TASK"])[0]
	return " · ".join([f"auto show {task}", "auto log --level debug",
		f"auto x usage --where plan={value['id']} --by kind,model"])


def changeLines(changed: list, color: bool) -> list:
	"""Render the files a guided run changed, one per line, then how many more."""
	if not changed:
		return []
	lines = [f"Changed {len(changed)} file" + ("s:" if len(changed) != 1 else ":")]
	for item in changed[:CHANGES_SHOWN]:
		symbol, code = CHANGES[item["change"]]
		lines.append(f"  {paint(symbol, code, color)} {safeText(item['path'])}")
	if len(changed) > CHANGES_SHOWN:
		lines.append(paint(f"  … and {len(changed) - CHANGES_SHOWN} more (git status)", "2", color))
	return lines


def tipFor(plan: str) -> str:
	"""Return the tip ending a plan: the first plan of a project gets the first tip, and so on."""
	number = planNumber(plan)
	return TIPS[int(number, 16) % len(TIPS) if number else 0]


def summaryNext(value: dict) -> str:
	stop = value["stop"]
	if value["questions"]:
		return "auto answer, then auto go"
	if stop == "declined":
		return 'auto go to run it, or auto change "…" to revise it'
	if stop == "unplanned":
		return REPLAN
	if stop == "held":
		return "auto resume"
	if value["failed"]:
		return f"auto show {value['failed'][0]} to see why, then auto go --task {value['failed'][0]} to retry"
	if stop == "failed" and value.get("reason"):
		return 'auto go to retry, or auto change "…" to revise the plan'
	if stop == "budget":
		return "auto status to review the budget, then auto go"
	if value["status"] == "Succeeded":
		return 'auto change "…" to refine it, or auto log to review it'
	return "auto status"


def filesText(files: list, shown: int = 4) -> str:
	"""Name up to `shown` files, then count the rest: `a, b (+3 more)`, or `5 files` when none are shown."""
	if not shown:
		return f"{len(files)} file" + ("s" if len(files) != 1 else "")
	rest = len(files) - shown
	return safeText(", ".join(files[:shown])) + (f" (+{rest} more)" if rest > 0 else "")


def reasonText(reason, color: bool = False) -> str:
	"""Format a check or repair reason: one line, or one `element: message` line per violation."""
	if not reason:
		return ""
	try:
		items = json.loads(reason) if isinstance(reason, str) else reason
	except ValueError:
		items = None
	if items == []:
		return ""
	if isinstance(items, list) and all(isinstance(item, dict) for item in items):
		return "".join("\n" + paint("  - " + safeText(": ".join(str(item[key]) for key in ("element", "message") if item.get(key))), "2", color)
			for item in items)
	return paint("  " + safeText(firstLine(reason if isinstance(reason, str) else render(reason, "json"))), "2", color)


class EventText:
	"""Render plan events as one readable line each, remembering which run belongs to which task."""

	def __init__(self, *, color: bool = False, times: bool = False, level: str = "info"):
		self.color = color
		self.times = times
		self.level = level
		self.tasks = {}

	def task(self, run: str) -> str:
		task = self.tasks.get(run, "")
		return "planning" if task == "elaborator" else task or "a task"

	def journal(self, item: dict) -> str | None:
		"""Render one run journal event, indented under the run it belongs to."""
		event, color = item["event"], self.color
		kind, name = event.get("type"), safeText(event.get("name", ""))
		if kind == Event.SCRIPT_ACTION:
			content = safeText(event.get("content", ""))
			subject = safeText(", ".join(event.get("files") or []) or event.get("detail") or "")
			if event.get("lifecycle"):
				text = f"$ {name}: {content}"
			elif content in {"error", "failed"}:
				text = paint(f"✗ {name} {subject}".rstrip() + (": " + safeText(firstLine(event["error"])) if event.get("error") else " failed"), "31", color)
			else:
				icon = "✎" if event.get("access") == "write" else "·"
				text = f"{icon} {name}" + (f" {subject}" if subject else "") + (paint(f"  {content}", "2", color) if content not in {"completed", ""} else "")
		elif kind == Event.SCRIPT_META:
			value = event.get("value")
			if name == "warning":
				text = paint("! " + safeText(value), "33", color)
			elif name == "runtime" and isinstance(value, dict):
				text = "· runtime " + " · ".join(f"{key}: {safeText(item)}" for key, item in value.items())
			elif name == "usage" and isinstance(value, dict):
				text = paint(" · ".join(filter(None, ["usage", countText(value.get("tokens") or 0) + " tokens", moneyText(value.get("cost"))])), "2", color)
			elif name == "tokens" and isinstance(value, dict):
				text = paint("tokens " + " · ".join(f"{key} {countText(item)}" for key, item in value.items() if item), "2", color)
			else:
				text = paint(f"{name}: {safeText(value)}", "2", color)
		elif kind == Event.SCRIPT_MESSAGE:
			lines = str(event.get("content", "")).splitlines()
			text = "\n".join(paint("│ ", "34", color) + safeText(line) for line in lines) or None
		elif kind == Event.SCRIPT_ENDED:
			status, output = event.get("status", ""), event.get("output")
			text = f"{marker(status, color)} script {safeText(status).lower()}"
			if event.get("failure"):
				text += paint("  " + safeText(failureText(event["failure"])), "31", color)
			if output is not None:
				body = render(output, "text").rstrip()
				if self.level == "trace":
					text += "\n" + "\n".join(paint("│ ", "34", color) + safeText(line) for line in body.splitlines())
				else:
					count = len(body.splitlines())
					text += paint(f"  output: {clip(safeText(firstLine(body)), 60)}" + (f" (+{count - 1} lines)" if count > 1 else ""), "2", color)
		elif kind == Event.TASK_CHECKED:
			verdict = event.get("verdict") or {}
			passed = verdict.get("pass", verdict.get("passed"))
			text = paint(("✓" if passed else "✗") + f" check {safeText(event.get('check', ''))}", "32" if passed else "31", color)
			text += reasonText(verdict.get("reason"), color)
		elif kind == Event.TASK_REPAIRING:
			text = paint(f"↻ repairing (attempt {event.get('attempt')})", "33", color) + reasonText(event.get("reason"), color)
		elif kind == Event.TASK_EVALUATED:
			text = "· evaluated"
		elif kind == Event.TASK_EVALUATION_FAILED:
			text = paint("✗ evaluation failed  " + safeText(failureText(event.get("failure") or {})), "31", color)
		elif kind == Event.POLICY_DENIED:
			text = paint(f"⊘ denied {safeText(event.get('effect', ''))}: {safeText(event.get('action', ''))}", "33", color)
		elif kind == Event.POLICY_REQUESTED:
			text = paint(f"? asks to {safeText(event.get('effect', ''))}: {safeText(event.get('action', ''))}", "33", color)
		elif kind == Event.GAP_RAISED:
			text = paint(f"? question {safeText(event.get('gap', ''))}", "33", color)
		elif kind == Event.GAP_RESOLVED:
			text = paint(f"✓ question {safeText(event.get('gap', ''))} answered", "2", color)
		else:
			text = paint(safeText(kind or "event") + " " + safeText(render({key: value for key, value in event.items() if key != "type"}, "json")), "2", color)
		if not text:
			return None
		# Journal events have no time of their own: they line up under their run's line.
		pad = " " * (13 if self.times else 0) + "    "
		self.tasks.setdefault(item["run"], item.get("task", ""))
		label = paint(self.task(item["run"]) + " ", "2", color)
		return "\n".join(pad + label + line for line in text.split("\n"))

	def line(self, event: dict) -> str | None:
		if "event" in event and "run" in event:
			return self.journal(event)
		kind, data, color = event.get("kind"), event.get("data", {}), self.color
		if kind == "RunStarted":
			self.tasks[data["run"]] = data.get("task", "")
			text = f"{marker(RunStatus.RUNNING, color)} {self.task(data['run'])}"
		elif kind == "RunEnded":
			status = data.get("status", "")
			text = f"{marker(status, color)} {self.task(data['run'])}"
			usage = usageText(data.get("usage") or {})
			if usage:
				text += paint("  " + usage, "2", color)
			written = (data.get("files") or {}).get("written") or []
			if written:
				text += paint("  wrote " + filesText(written, 4 if self.level != "info" else 0), "36", color)
			if data.get("failure"):
				text += paint("  " + safeText(failureText(data["failure"])), "31", color)
			elif status not in {RunStatus.SUCCEEDED, ""}:
				text += paint("  " + status, "2", color)
		elif kind == "RunWaiting":
			text = f"{marker(RunStatus.WAITING, color)} {self.task(data['run'])} needs an answer"
		elif kind == "GapRaised":
			text = f"{marker(RunStatus.WAITING, color)} new question for " + ", ".join(data.get("needed_by", [])) if self.times else None
		elif kind == "GapResolved":
			text = paint("✓ question answered", "2", color) if self.times else None
		elif kind == "GapDismissed":
			text = paint("– question dismissed", "2", color) if self.times else None
		elif kind == "DecisionRecorded":
			text = paint("✓ decision recorded", "2", color)
		elif kind == "PlanRevised":
			ops = [op for op in data.get("ops", []) if op in {"add", "remove", "edit", "replace", "update"}]
			text = paint("↻ plan revised (" + ", ".join(ops) + ")", "2", color) if ops else None
		elif kind == "PlanHeld":
			reason = data.get("reason", "")
			text = paint(("⏸ paused" if data.get("mode") == "Pause" else "■ stopped")
				+ (": " + safeText(reason) if reason not in {"", "Paused", "Stopped"} else ""), "33", color)
		elif kind == "PlanReleased":
			text = paint("▶ resumed", "2", color)
		elif kind == "ContinuationStarted":
			text = paint("▶ continuing", "2", color) if self.times else None
		elif kind == "ContinuationStopped":
			text = paint("■ " + STOPS.get(data.get("stop"), "stopped").lower(), "2", color) if self.times and data.get("stop") != "ended" else None
		elif kind == "PlanEnded":
			text = paint(f"■ plan {str(data.get('status', 'ended')).lower()}", "1", color)
		else:
			return None
		if text is None:
			return None
		if self.level != "info":
			# Name what the event belongs to, and its sequence number for --since.
			ids = ([f"#{event['seq']}"] if event.get("seq") else []) + [safeText(data[key]) for key in ("run", "gap", "revision", "continuation") if data.get(key)]
			text += paint("  " + " ".join(ids), "2", color) if ids else ""
		if self.times and event.get("date"):
			text = paint(str(event["date"])[5:16].replace("T", " "), "2", color) + "  " + text
		return text


def plansView(plans: list, current: str | None = None, *, color: bool = False) -> str:
	"""Render the project's plans, marking the current one."""
	if not plans:
		return 'No plans yet. Start one with: auto "<goal>"'
	lines = []
	for plan in plans:
		mark = paint("*", "36", color) if plan.get("path") == current else " "
		# Plan summaries count the root task, which aggregates the others.
		total = plan.get("tasks", 0) - 1 if plan.get("tasks", 0) > 1 else plan.get("tasks", 0)
		progress = f"{min(plan.get('done', 0), total)}/{total}" if plan.get("status") != "Unreadable" else "-"
		needs = paint(f"  {plan['gaps']} question" + ("s" if plan["gaps"] != 1 else ""), "33", color) if plan.get("gaps") else ""
		lines.append(f"{mark} {plan['id']}  {paint(plan['status'], '2', color)} {progress}  {safeText(firstLine(plan.get('goal') or plan.get('error', '')))}{needs}")
	return "\n".join(lines) + "\n\nSwitch with: auto use PLAN"


def taskView(value: dict, *, color: bool = False) -> str:
	"""Render one task, its latest attempt and output."""
	lines = [f"{marker(value['state'], color)} {paint(value['id'], '1', color)}  {safeText(firstLine(value['goal']))}",
		paint(" · ".join(filter(None, [value["state"], f"{value['attempts']} attempt" + ("s" if value["attempts"] != 1 else ""),
			usageText((value["latest"] or {}).get("usage") or {})])), "2", color)]
	if value["dependencies"]:
		lines.append("After: " + ", ".join(value["dependencies"]))
	run = value["latest"] or {}
	if run.get("failure"):
		lines.append(paint("Failure: " + safeText(run["failure"].get("reason") or run["failure"]), "31", color))
	for question in value["questions"]:
		lines.append(paint("Needs you: " + safeText(firstLine(question)), "33", color))
	files = run.get("files") or {}
	for label, key, code in (("Wrote", "written", "36"), ("Read", "read", "2")):
		if files.get(key):
			lines.append(paint(f"{label}: " + filesText(files[key], 12), code, color))
	if value["output"] is not None:
		lines += ["", render(value["output"], "text").rstrip()]
	elif value["description"]:
		lines += ["", safeText(value["description"]).rstrip()]
	if run.get("id"):
		lines += ["", paint(f"Run {run['id']}: auto x run journal {shlex.quote(run['id'])}", "2", color)]
	return "\n".join(lines)


def learningView(agents: list, *, color: bool = False) -> str:
	"""Render memory agents' consolidation state."""
	if not agents:
		return ("No memory agents are enabled for this project.\n"
			"Configure one under [memory.agents] and list it in [memory] default_agents to learn from plans.")
	lines = []
	for agent in agents:
		if agent.get("error"):
			lines.append(f"{paint(agent['agent'], '1', color)}  {paint(safeText(agent['error']), '31', color)}")
			continue
		facts = [("last consolidated " + str(agent["consolidated"])[:16].replace("T", " ")) if agent["consolidated"] else "not consolidated yet"]
		facts += [f"{agent[key]} {label}" for key, label in (("due", "observations due"), ("proposals", "proposals"), ("questions", "questions")) if agent[key]]
		if agent["pending"]:
			facts.append("consolidation pending")
		lines.append(f"{paint(agent['agent'], '1', color)}  " + " · ".join(facts))
	return "\n".join(lines) + "\n\nConsolidate now: auto x consolidation run AGENT"

# -----------------------------------------------------------------------------
#
# API
#
# -----------------------------------------------------------------------------


class Frontend:
	"""Render execution events without coupling the runtime to terminal output."""

	def __init__(self, format: str, *, stdout=None, stderr=None):
		self.format = format
		self.stdout = stdout or sys.stdout
		self.stderr = stderr or sys.stderr
		self.terminal = format == "text" and self.stderr.isatty() and os.environ.get("TERM") != "dumb"
		self.color = self.terminal and "NO_COLOR" not in os.environ
		self.started = time.monotonic()
		self.initialTime = 0.0
		self.operation = "Thinking"
		self.cost = None
		self.tokens = None
		self.budget = {}
		self.planUsage = None
		self.planBudget = {}
		self.last = ""
		self.lastWidth = 0
		self.lineStart = True
		self.lastOutput = self.started
		self.streamed = False
		self.structured = False
		self.active = False

	def clear(self):
		if self.lastWidth:
			self.stderr.write("\r" + " " * self.lastWidth + "\r")
			self.stderr.flush()
			self.lastWidth = 0
		self.last = ""

	def tick(self):
		if not self.terminal or not self.active:
			return
		if not self.lineStart and time.monotonic() - self.lastOutput >= 1:
			self.stdout.write("\n")
			self.stdout.flush()
			self.lineStart = True
		if not self.lineStart:
			return
		try:
			columns = os.get_terminal_size(self.stderr.fileno()).columns
		except (OSError, ValueError, AttributeError):
			columns = 80
		seconds = max(0, int(self.initialTime + time.monotonic() - self.started))
		metrics = self.metrics(seconds)
		available = max(0, columns - 1)
		label = "  › " + self.operation
		if width(metrics) + 4 >= available:
			line = clip(metrics, available)
		else:
			label = clip(label, available - width(metrics) - 1)
			line = label + " " * max(1, available - width(label) - width(metrics)) + metrics
		if line == self.last:
			return
		padding = " " * max(0, min(self.lastWidth, available) - width(line))
		shown = line.replace("›", "\033[36m›\033[0m", 1) if self.color else line
		self.stderr.write("\r" + shown + padding)
		self.stderr.flush()
		self.last, self.lastWidth = line, width(line)

	def metrics(self, seconds: int) -> str:
		if self.planUsage is None:
			return self.usageMetrics(seconds, self.cost, self.tokens, self.budget)
		return "Plan · " + self.usageMetrics(seconds + int(self.planUsage["time"]), self.total("cost", self.cost),
			self.total("tokens", self.tokens), self.planBudget)

	def total(self, name, value):
		previous = self.planUsage[name]
		return previous + (value or 0) if previous or value is not None else None

	def usageMetrics(self, seconds, cost, tokens, budget):
		parts = ["cost " + costText(cost, budget.get("cost"))]
		if tokens is not None or "tokens" in budget:
			parts.append((countText(tokens) if tokens is not None else "—")
				+ (f" / {countText(budget['tokens'])}" if "tokens" in budget else "") + " tokens")
		timer = f"{seconds // 60:02d}:{seconds % 60:02d}"
		if "time" in budget:
			limit = int(budget["time"])
			timer += f" / {limit // 60:02d}:{limit % 60:02d}"
		parts.append(timer)
		return " · ".join(parts)

	def event(self, event: dict, *, response=False):
		kind = event.get("type")
		if kind == Event.EXECUTION_STARTED:
			self.structured = event.get("output", {}).get("format") == "json" or bool(event.get("transform"))
			self.active = True
			if self.terminal:
				self.clear()
				config = event.get("config", {})
				selection = " · ".join(f"{key}: {safeText(config.get(key) or RUNTIME_DEFAULTS.get(key, 'engine default'))}"
					for key in ("engine", "provider", "model", "agent"))
				# Name what runs: several executions (triage, elaboration, checks) can share one plan run.
				stage = ": ".join(safeText(event[key]) for key in ("kind", "check") if event.get(key) and event[key] != "task")
				subject = safeText(event.get("task") or "") + (f" ({stage})" if stage else "")
				print("  › " + (f"task: {subject.strip()} · " if subject.strip() else "") + selection, file=self.stderr, flush=True)
		elif kind == Event.RUN_STARTED:
			self.clear()
			self.active = True
			self.started = time.monotonic()
			self.operation = "Thinking"
			self.budget = event.get("budget", {})
			self.planUsage = event.get("planUsage")
			self.planBudget = event.get("planBudget", {})
			usage = event.get("usage", {})
			self.initialTime = usage.get("time", 0)
			self.cost = usage.get("cost") or None
			self.tokens = usage.get("tokens") or None
			if self.terminal:
				task = event.get("task", "")
				resuming = event.get("resuming")
				label = ("Resuming planning" if resuming else "Planning") if task == "elaborator" else ("Resuming task: " if resuming else "Task: ") + safeText(task)
				print("  › " + label, file=self.stderr, flush=True)
				if self.planUsage is not None and self.budget:
					print("    Run · " + self.usageMetrics(int(self.initialTime), self.cost, self.tokens, self.budget), file=self.stderr, flush=True)
		elif kind == Event.RUN_PHASE:
			self.operation = safeText(event.get("phase", "Running"))
		elif kind == Event.SCRIPT_ACTION:
			name = safeText(event.get("name", "tool"))
			subject = safeText(", ".join(event.get("files") or []) or event.get("detail") or "")
			self.operation = name + ": " + safeText(event.get("content", "")) if event.get("lifecycle") else "Use " + name + (" " + subject if subject else "")
		elif kind == Event.SCRIPT_META and event.get("name") == "usage":
			self.cost = event.get("value", {}).get("cost")
			self.tokens = event.get("value", {}).get("tokens")
		elif kind == Event.SCRIPT_META and event.get("name") == "warning":
			self.clear()
			marker = "\033[33m!\033[0m" if self.color else "!"
			print("  " + marker + " " + safeText(event.get("value", "")), file=self.stderr, flush=True)
		elif kind == Event.SCRIPT_MESSAGE and response and not self.structured:
			self.clear()
			text = event.get("content", "")
			self.lastOutput = time.monotonic()
			self.streamed |= bool(text)
			if self.stdout.isatty() and os.environ.get("TERM") != "dumb":
				for part in text.splitlines(keepends=True):
					if self.lineStart:
						prefix = "  \033[34m│\033[0m " if self.color else "  │ "
						self.stdout.write(prefix)
					self.stdout.write(part)
					self.lineStart = part.endswith("\n")
			else:
				self.stdout.write(text)
			self.stdout.flush()
		elif kind == Event.RUN_FINISHED:
			self.finish(event["run"])
		elif kind == Event.SCRIPT_ITEM_STARTED:
			self.active, self.started = True, time.monotonic()
			if self.terminal:
				name, value = safeText(event.get("name", "item")), safeText(event.get("value", ""))
				progress = f"({event.get('index', 0) + 1}/{event.get('count', 1)})"
				print(f"  › {name}: {value} {progress}", file=self.stderr, flush=True)
		elif kind == Event.SCRIPT_ITEM_ENDED:
			self.finish(event)
			if self.terminal and event.get("failure"):
				print("    " + safeText(event["failure"].get("reason", "")), file=self.stderr, flush=True)
		self.tick()

	def finish(self, result: dict):
		self.clear()
		if self.terminal and not self.lineStart:
			self.stdout.write("\n")
			self.stdout.flush()
			self.lineStart = True
		if self.terminal and self.active:
			status = result.get("status", RunStatus.FAILED)
			usage = result.get("usage", {})
			if "cost" in usage and (self.cost is not None or usage["cost"]):
				self.cost = usage["cost"]
			if "tokens" in usage and (self.tokens is not None or usage["tokens"]):
				self.tokens = usage["tokens"]
			marker, code = {RunStatus.SUCCEEDED: ("✓", "32"), RunStatus.WAITING: ("?", "33")}.get(status, ("!", "31"))
			if self.color:
				marker = f"\033[{code}m{marker}\033[0m"
			seconds = max(0, int(self.initialTime + time.monotonic() - self.started))
			print(f"  {marker} {safeText(status)} · {self.metrics(seconds)}", file=self.stderr, flush=True)
		self.active = False

	def scriptEvent(self, event: dict):
		self.event(event, response=True)
		if event.get("type") == Event.SCRIPT_ENDED:
			self.finish(event)
			if event.get("failure"):
				self.error(event["failure"].get("reason", "Execution failed"))
			elif not self.streamed and event.get("output") is not None:
				print(render(event["output"], self.format), file=self.stdout)
			if event.get("run"):
				# A recorded run says where its output is kept.
				if event.get("outputPath"):
					print(f"Output: {safeText(event['outputPath'])}\nNext: auto x script output {safeText(event['run'])}", file=self.stderr, flush=True)
				else:
					print(f"Recorded as {safeText(event['run'])}; see auto x script list", file=self.stderr, flush=True)

	def result(self, value):
		"""Show structured results as readable fields on terminals, without dropping data."""
		if not self.stdout.isatty() or os.environ.get("TERM") == "dumb" or not isinstance(value, (dict, list)):
			print(render(value, self.format), file=self.stdout)
			return
		def lines(item, indent=0):
			pad = "  " * indent
			if isinstance(item, dict) and item:
				for key, child in item.items():
					if isinstance(child, (dict, list)) and child:
						yield pad + str(key) + ":"
						yield from lines(child, indent + 1)
					else:
						yield pad + str(key) + ": " + render(child, "text")
			elif isinstance(item, list) and item:
				for child in item:
					if isinstance(child, (dict, list)) and child:
						yield pad + "-"
						yield from lines(child, indent + 1)
					else:
						yield pad + "- " + render(child, "text")
			else:
				yield pad + render(item, "text")
		print("\n".join(lines(value)), file=self.stdout)

	def close(self):
		self.clear()

	def error(self, message):
		self.clear()
		prefix = "\033[31mauto: error:\033[0m" if self.color else "auto: error:"
		print(prefix + " " + safeText(message), file=self.stderr)


# EOF
