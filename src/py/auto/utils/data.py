"""Module: data — YAML documents, stable JSON and atomic local writes."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ruamel.yaml import YAML

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------


class AutoError(Exception):
	"""Reports an operation failure with a stable failure kind."""

	def __init__(self, message: str, kind: str = "input"):
		super().__init__(message)
		self.kind = kind

# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------


def jsonText(value: object) -> str:
	"""Serialise a value as compact, key-sorted JSON."""
	return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def yamlLoad(text: str) -> object:
	"""Parse YAML text with safe, pure-Python semantics."""
	try:
		return YAML(typ="safe", pure=True).load(text)
	except Exception as error:
		raise AutoError(f"Invalid YAML: {error}") from error


def yamlText(value: object) -> str:
	"""Serialise a value as block-style YAML."""
	stream = io.StringIO()
	writer = YAML(typ="safe", pure=True)
	writer.default_flow_style = False
	writer.dump(value, stream)
	return stream.getvalue()


def document(text: str) -> tuple[dict, str]:
	"""Split YAML frontmatter from a markdown body."""
	if not text.startswith("---\n"):
		return {}, text
	match = re.search(r"(?m)^---\s*$", text[4:])
	if not match:
		raise AutoError("Unclosed YAML frontmatter")
	meta = yamlLoad(text[4:4 + match.start()]) or {}
	if not isinstance(meta, dict) or any(not isinstance(key, str) for key in meta):
		raise AutoError("Frontmatter must be a string-keyed mapping")
	return meta, text[4 + match.end():].lstrip("\r\n")


def documentText(meta: dict, body: str) -> str:
	"""Join frontmatter and a markdown body into one document."""
	return "---\n" + yamlText(meta) + "---\n" + body


def merge(*layers: dict) -> dict:
	"""Merge mappings recursively, later layers winning."""
	result = {}
	for layer in layers:
		for key, value in layer.items():
			result[key] = merge(result.get(key, {}) if isinstance(result.get(key), dict) else {}, value) if isinstance(value, dict) else value
	return result


def digest(data: bytes) -> str:
	"""Return the canonical `sha256:` digest of bytes."""
	return "sha256:" + hashlib.sha256(data).hexdigest()


def timestamp() -> str:
	"""Return the current UTC datetime in ISO form."""
	return datetime.now(timezone.utc).isoformat()


def identity() -> str:
	"""Return a sortable, unique record identity."""
	return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:12]


def identifier(value: str) -> str:
	"""Validate a record identifier and return it."""
	if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", value):
		raise AutoError(f"Invalid identifier: {value!r}")
	return value


def atomicWrite(path: Path, text: str) -> None:
	"""Replace a file atomically, creating its parent directory."""
	path.parent.mkdir(parents=True, exist_ok=True)
	fd, temporary = tempfile.mkstemp(prefix=".auto-", dir=path.parent)
	try:
		with os.fdopen(fd, "w", encoding="utf-8") as stream:
			stream.write(text)
			stream.flush()
			os.fsync(stream.fileno())
		os.replace(temporary, path)
		fd = os.open(path.parent, os.O_RDONLY)
		try:
			os.fsync(fd)
		finally:
			os.close(fd)
	finally:
		Path(temporary).unlink(missing_ok=True)


def readJson(path: Path, default=None):
	"""Read JSON from a file, or return the default."""
	return json.loads(path.read_text()) if path.exists() else default


def sections(body: str, level: int = 2) -> tuple[str, dict[str, str]]:
	"""Split headings outside fenced code; preserve nested headings verbatim."""
	result, intro, current, fence = {}, [], None, None
	for line in body.splitlines(keepends=True):
		marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
		if marker:
			token = marker[1]
			if fence is None:
				fence = token
			elif token[0] == fence[0] and len(token) >= len(fence):
				fence = None
		match = re.match(r"^" + "#" * level + r" ([^\n]+)\s*$", line) if fence is None else None
		if match:
			current = match[1].strip()
			if current in result:
				raise AutoError(f"Duplicate section: {current}")
			result[current] = []
		else:
			(result[current] if current is not None else intro).append(line)
	return "".join(intro), {name: "".join(lines).strip() for name, lines in result.items()}

# EOF
