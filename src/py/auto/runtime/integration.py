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

# EOF
