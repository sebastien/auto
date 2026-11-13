"""Module: integration — Contracts implemented by engine and knowledge adapters."""

from __future__ import annotations

from typing import Iterator, Protocol, runtime_checkable

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------


@runtime_checkable
class EngineIntegration(Protocol):
	"""Engine backend. Optional `enforceableEffects` declares exact effect names."""

	def start(self, context, request: dict) -> Iterator[dict]:
		"""Stream events; a PolicyRequest accepts a boolean, also in request['answer'] on resume."""
		...


@runtime_checkable
class CancellableEngine(Protocol):
	"""Engine that can interrupt a running server-side session."""

	def cancel(self, context, request: dict) -> None:
		"""Cancel the session described by `request`."""
		...


@runtime_checkable
class KnowledgeIntegration(Protocol):
	"""Minimal contract of a knowledge and provenance backend."""

	def list(self, plan: str, kind: str) -> list[dict]:
		"""List records of `kind` belonging to `plan`."""
		...

	def get(self, ref: str) -> dict:
		"""Read one record by canonical identity."""
		...

	def write(self, kind: str, value: dict, expected: str = "") -> None:
		"""Write a record, honouring the expected revision."""
		...

	def readReference(self, ref: str):
		"""Resolve a record reference to content."""
		...

@runtime_checkable
class AgentMemoryIntegration(Protocol):
	"""Optional knowledge capability for a read-only, consistent agent snapshot."""

	def memorySnapshot(self):
		"""Return an object with wake, minimums and model operations over fixed material."""
		...


@runtime_checkable
class ConsolidationIntegration(Protocol):
	"""Optional Memo maintenance capability, including restart-safe application."""

	def consolidation(self, agent: str):
		"""Return due, compact, validate and apply operations for one Memo agent."""
		...


class RunScopeIntegration(Protocol):
	"""Storage, permission and final application hooks for a prepared task run."""

	def directory(self, context, run):
		"""Locate one run within this scope."""
		...

	def variables(self, context, path, run) -> dict:
		"""Supply scope variables without resolving task inputs."""
		...

	def save(self, context, path, run, state=None):
		"""Durably persist active state or a terminal receipt."""
		...

	def policy(self, context, run, state):
		"""Return the effective task and execution policy."""
		...

	def authorizeRequest(self, context, effect) -> bool:
		"""Authorize an engine tool effect without creating a plan gap."""
		...

	def finish(self, context, path, run, state):
		"""Validate and apply output before the lifecycle publishes success."""
		...

	def check(self, context, path, run, state):
		"""Run mandatory scope validation after Post and before Evaluation."""
		...

# EOF
