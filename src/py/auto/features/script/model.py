"""Module: model — A script is source text, metadata and its origin."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class Script:
	"""A template with YAML defaults; caller values live in Context."""
	content: str
	path: Path
	meta: dict = field(default_factory=dict)

# EOF
