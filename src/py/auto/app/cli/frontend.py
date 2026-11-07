"""Module: frontend — Human-facing execution output and terminal progress."""

from __future__ import annotations

import math
import os
import re
import sys
import time
import unicodedata

from auto.app.operations import render
from auto.runtime.model import Event, RunStatus

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
		parts = ["cost " + costText(self.cost, self.budget.get("cost"))]
		if "tokens" in self.budget:
			parts.append(f"{self.tokens if self.tokens is not None else '—'} / {self.budget['tokens']} tokens")
		timer = f"{seconds // 60:02d}:{seconds % 60:02d}"
		if "time" in self.budget:
			limit = int(self.budget["time"])
			timer += f" / {limit // 60:02d}:{limit % 60:02d}"
		parts.append(timer)
		return " · ".join(parts)

	def event(self, event: dict, *, response=False):
		kind = event.get("type")
		if kind == Event.EXECUTION_STARTED:
			self.structured = event.get("output", {}).get("format") == "json" or bool(event.get("transform"))
			self.active = True
			model = event.get("config", {}).get("model") or "engine default"
			if self.terminal:
				self.clear()
				print("  › model: " + safeText(model), file=self.stderr, flush=True)
		elif kind == Event.RUN_STARTED:
			self.active = True
			self.started = time.monotonic()
			self.budget = event.get("budget", {})
			usage = event.get("usage", {})
			self.initialTime = usage.get("time", 0)
			self.cost = usage.get("cost") or None
			self.tokens = usage.get("tokens") or None
			if self.terminal:
				print("  › Task: " + safeText(event.get("task", "")), file=self.stderr, flush=True)
		elif kind == Event.RUN_PHASE:
			self.operation = safeText(event.get("phase", "Running"))
		elif kind == Event.SCRIPT_ACTION:
			name = safeText(event.get("name", "tool"))
			self.operation = name + ": " + safeText(event.get("content", "")) if event.get("lifecycle") else "Use " + name
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
			marker = "✓" if status == RunStatus.SUCCEEDED else "!"
			code = "32" if status == RunStatus.SUCCEEDED else "31"
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
