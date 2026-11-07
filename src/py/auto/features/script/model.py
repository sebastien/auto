"""Module: model — A script is source text, metadata and its origin."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from auto.utils.data import document

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------


@dataclass(slots=True)
class Script:
	"""A template with YAML defaults; caller values live in Context."""

	content: str
	path: Path
	meta: dict = field(default_factory=dict)

# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------


def createScript(text: str, path: Path, meta: dict | None = None) -> Script:
	"""Create a script from its markdown document or explicit metadata."""
	if meta is not None:
		return Script(text, path, meta)
	meta, body = document(text)
	return Script(body, path, meta)

# EOF
