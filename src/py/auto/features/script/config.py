"""Module: config — Validate executable metadata at the authoring boundary."""

from __future__ import annotations

from auto.runtime.config import validateConfig as validateConfig

# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------


def commands(value) -> list[str]:
	"""Normalise a lifecycle key (`pre`, `post`, `apply`, `eval`) to a command list."""
	return [] if value is None else [value] if isinstance(value, str) else list(value)

# EOF
