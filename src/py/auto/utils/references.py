"""Module: references — Relative reference relocation for materialised assets."""

from __future__ import annotations

import re
from pathlib import Path

from auto.utils.data import merge

# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------


def relocate(text: str, base: Path) -> str:
	"""Rewrite source-relative transclusions to absolute paths from `base`."""
	def path(value):
		value = value.strip('"')
		return '"' + str((base / value).resolve()) + '"' if " " in value else str((base / value).resolve())
	text = re.sub(r'(?m)^@(\.{1,2}/[^\n]+|"\.{1,2}/[^"\n]+")$', lambda m: "@" + path(m[1]), text)
	return re.sub(r'{{@(\.{1,2}/[^}]+|"\.{1,2}/[^"}]+")}}', lambda m: "{{@" + path(m[1]) + "}}", text)


def relocateMeta(meta: dict, base: Path) -> dict:
	"""Rewrite source-relative metadata values to absolute paths from `base`."""
	result = merge(meta)
	for group in ("inputs", "params", "output"):
		for key, value in result.get(group, {}).items():
			if isinstance(value, str) and value.startswith(("./", "../")):
				result[group][key] = str((base / value).resolve())
	for key in ("profile", "artifact", "cwd"):
		value = result.get(key)
		if isinstance(value, str) and value.startswith(("./", "../")):
			result[key] = str((base / value).resolve())
	return result

# EOF
