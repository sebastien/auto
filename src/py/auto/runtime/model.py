"""Module: model — Execution context, runtime composition and accounting."""

from __future__ import annotations

import math
import os
import re
import time
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Callable

from auto.runtime.integration import EngineIntegration, KnowledgeIntegration
from auto.utils.data import AutoError

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------


class RunStatus(StrEnum):
	"""Terminal and in-flight states of a run."""

	PENDING = "Pending"
	RUNNING = "Running"
	WAITING = "Waiting"
	SUCCEEDED = "Succeeded"
	FAILED = "Failed"
	TIMED_OUT = "TimedOut"
	CANCELLED = "Cancelled"
	BLOCKED = "Blocked"
	SKIPPED = "Skipped"


class FailureKind(StrEnum):
	"""Stable failure categories shared by runtime, features and interfaces."""

	INPUT = "input"
	SYSTEM = "system"
	TIMEOUT = "timeout"
	BUDGET = "budget"
	CANCELLED = "cancelled"
	OUTPUT = "output"


TERMINAL = frozenset({RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.TIMED_OUT, RunStatus.CANCELLED, RunStatus.BLOCKED, RunStatus.SKIPPED})
IN_FLIGHT = frozenset({RunStatus.PENDING, RunStatus.RUNNING, RunStatus.WAITING})


@dataclass(slots=True)
class Content:
	"""Resolved bytes with the owner's revision and relative-reference origin."""

	ref: str
	data: bytes
	digest: str
	path: Path

	@property
	def text(self) -> str:
		try:
			return self.data.decode("utf-8")
		except UnicodeError as error:
			raise AutoError(f"Reference is not UTF-8 text: {self.ref}") from error


@dataclass(slots=True)
class Limits:
	"""One shared account for a process and all of its checks."""

	budget: dict = field(default_factory=dict)
	usage: dict = field(default_factory=lambda: {"tokens": 0, "cost": 0.0, "time": 0.0})
	started: float = field(default_factory=time.monotonic)
	initialTime: float = 0.0

	def check(self) -> None:
		self.usage["time"] = self.initialTime + time.monotonic() - self.started
		for name, maximum in self.budget.items():
			if self.usage.get(name, 0) >= maximum:
				raise AutoError(f"{name} budget exhausted", FailureKind.TIMEOUT if name == "time" else FailureKind.BUDGET)

	def remaining(self) -> float:
		self.check()
		return self.budget.get("time", 3600) - self.usage["time"]


@dataclass(slots=True)
class ProcessOptions:
	"""The subset of context needed to launch and bound a subprocess."""

	project: Path
	environ: dict
	maxBytes: int
	limits: Limits | None = None
	cancelled: Callable = field(default_factory=lambda: (lambda: False))
	tick: Callable | None = None


@dataclass(slots=True)
class Context:
	"""Caller configuration and the runtime used to reach integrations."""

	project: Path = field(default_factory=lambda: Path.cwd())
	plan: Path | None = None
	config: dict = field(default_factory=dict)
	defaults: dict = field(default_factory=dict)
	inputs: dict = field(default_factory=dict)
	resolvedInputs: dict = field(default_factory=dict)
	params: dict = field(default_factory=dict)
	environ: dict = field(default_factory=lambda: dict(os.environ))
	engines: dict = field(default_factory=dict)
	resolvers: dict = field(default_factory=dict)
	knowledge: object | None = None
	limits: Limits | None = None
	evidence: list = field(default_factory=list)
	variables: dict = field(default_factory=dict)
	question: Callable | None = None
	observe: Callable | None = None
	tick: Callable | None = None
	cancelled: Callable = field(default_factory=lambda: (lambda: False))
	maxBytes: int = 4 * 1024 * 1024
	maxDepth: int = 32
	allowShell: bool = True
	author: str = "human"

	@property
	def runtime(self) -> Runtime:
		return Runtime(self)

	@property
	def process(self) -> ProcessOptions:
		return ProcessOptions(self.project, self.environ, self.maxBytes, self.limits, self.cancelled, self.tick)

	def derive(self, **changes) -> Context:
		return replace(self, **changes)


@dataclass(slots=True)
class Runtime:
	"""Gateway from feature operations to integrations and effect boundaries."""

	context: Context

	def engine(self, name: str) -> EngineIntegration:
		if name in self.context.engines:
			return self.context.engines[name]
		if name == "opencode":
			from auto.integration.opencode import OpenCode
			return OpenCode()
		if name == "pi":
			from auto.integration.pi import Pi
			return Pi()
		if name == "llm":
			from auto.integration.llm import Llm
			return Llm()
		raise AutoError(f"Unknown engine: {name}")

	def knowledgeFor(self) -> KnowledgeIntegration:
		if self.context.knowledge is not None:
			return self.context.knowledge
		from auto.integration.memo import MemoKnowledge
		return MemoKnowledge(self.context.project)

	def resolve(self, target: str) -> Content:
		if not self.context.resolvers:
			from auto.wiring import installResolvers
			installResolvers(self.context)
		for prefix, resolver in self.context.resolvers.items():
			if target.startswith(prefix):
				return resolver(self.context, target)
		raise AutoError(f"No resolver for reference: {target}")


# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------


def budgetValue(value: dict | None) -> dict:
	"""Validate and normalise a `{tokens,cost,time}` budget mapping."""
	if value is not None and not isinstance(value, dict):
		raise AutoError("Budget must be a mapping")
	result = {}
	for key, amount in (value or {}).items():
		if key not in {"tokens", "cost", "time"}:
			raise AutoError(f"Unknown budget dimension: {key}")
		if key == "time" and isinstance(amount, str):
			match = re.fullmatch(r"(\d+(?:\.\d+)?)(ms|s|m|h)", amount)
			if not match:
				raise AutoError(f"Invalid duration: {amount}")
			amount = float(match[1]) * {"ms": .001, "s": 1, "m": 60, "h": 3600}[match[2]]
		if type(amount) not in (int, float) or not math.isfinite(amount) or amount < 0:
			raise AutoError(f"Invalid {key} budget")
		result[key] = amount
	return result


def failureFor(kind: str) -> FailureKind:
	"""Map a timeout to the enforced budget category."""
	return FailureKind.BUDGET if kind == FailureKind.TIMEOUT else FailureKind(kind)


def statusFor(kind: str) -> RunStatus:
	"""Map a failure kind to the terminal run status it produces."""
	return {"timeout": RunStatus.TIMED_OUT, "cancelled": RunStatus.CANCELLED}.get(str(kind), RunStatus.FAILED)


# EOF
