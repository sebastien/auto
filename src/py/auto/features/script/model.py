"""Module: model — A script is source text, metadata and its origin."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re

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
	ref: str | None = None
	# The template a script inherits with `from`: `{ref, digest, artifact?}`.
	template: dict | None = None

	@property
	def identity(self):
		"""Distinguish selected resources while retaining filesystem aliases."""
		ref = self.ref or ""
		target, _, selector = ref.partition("#")
		lines = re.fullmatch(r"L(\d+)-(\d+)", selector)
		if lines:
			selector = f"L{int(lines[1])}-{int(lines[2])}"
		if not ref or ref.startswith(("./", "../", "/", "auto:catalogue/")) or re.fullmatch(r"(?:task:|auto:[^/#]+/task/)[^/#]+/definition(?:#.*)?", ref):
			return ("file", str(self.path.resolve()), selector)
		return ("reference", target + ("#" + selector if selector else ""))

# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------


def createScript(text: str, path: Path, meta: dict | None = None, *, ref: str | None = None) -> Script:
	"""Create a script from its markdown document or explicit metadata."""
	if meta is not None:
		return Script(text, path, meta, ref)
	meta, body = document(text)
	return Script(body, path, meta, ref)

# EOF
