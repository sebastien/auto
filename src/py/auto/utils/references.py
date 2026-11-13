"""Module: references — Relative reference relocation for materialised assets."""

from __future__ import annotations

import re
from pathlib import Path

from auto.utils.data import AutoError, merge

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
		mapping = result.get(group, {})
		if not isinstance(mapping, dict) or any(not isinstance(name, str) for name in mapping):
			raise AutoError(f"{group} must be a string-keyed mapping")
		for key, value in mapping.items():
			if isinstance(value, str) and value.startswith(("./", "../")):
				result[group][key] = str((base / value).resolve())
	for key in ("profile", "artifact"):
		value = result.get(key)
		if isinstance(value, str) and value.startswith(("./", "../")):
			result[key] = str((base / value).resolve())
	if isinstance(result.get("cwd"), str) and not Path(result["cwd"]).is_absolute():
		result["cwd"] = str((base / result["cwd"]).resolve())
	return result

# EOF
