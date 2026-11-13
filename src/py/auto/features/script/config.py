"""Module: config — Validate executable metadata at the authoring boundary."""

from __future__ import annotations

from auto.runtime.config import validateConfig as validateConfig
from auto.utils.data import AutoError

# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------


def commands(value) -> list[str]:
	"""Normalise a lifecycle key to a command list."""
	return [] if value is None else [value] if isinstance(value, str) else list(value)


def composedMeta(meta: dict, path) -> dict:
	"""Keep executable defaults while dropping ignored child lifecycle commands."""
	if "each" in meta:
		raise AutoError(f"A transcluded script cannot use each: {path}")
	return {key: value for key, value in meta.items() if key not in {"pre", "post", "transform", "apply", "eval"}}

# EOF
