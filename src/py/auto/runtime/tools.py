"""Module: tools — What an engine's tool call touched, for journals and receipts."""

from __future__ import annotations

import json
import re
from pathlib import Path

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------

# Tool names, lowercased, by what they do to the files they name. Others only "use" them.
WRITES = frozenset({"write", "edit", "multiedit", "apply_patch", "patch", "create", "str_replace", "str_replace_editor",
	"notebookedit", "notebook_edit", "delete", "remove", "move", "rename", "mkdir"})
READS = frozenset({"read", "view", "cat", "glob", "grep", "list", "ls", "find", "search", "lsp", "codesearch", "notebookread"})
# Input keys naming one file or directory, and keys naming several.
PATH_KEYS = ("filePath", "file_path", "path", "file", "filename", "notebook_path", "target", "source", "destination")
PATHS_KEYS = ("paths", "files", "filePaths")
# Patch bodies name their files on header lines (apply_patch and unified diffs).
PATCH_KEYS = ("patchText", "patch", "diff", "input")
PATCH_FILE = re.compile(r"^(?:\*\*\* (?:Add|Update|Delete) File|\*\*\* Move to|\+\+\+ b/|--- a/)[: ]\s*(.+?)\s*$", re.M)
# Inputs worth a short mention when they name no file: a command, a pattern, an address.
DETAIL_KEYS = ("command", "cmd", "pattern", "query", "url", "description")
DETAIL_LIMIT = 200

# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------


def toolAccess(name: str) -> str:
	"""Classify a tool by name as reading, writing or merely using what it names."""
	name = str(name).lower()
	return "write" if name in WRITES or "write" in name or "edit" in name else "read" if name in READS or "read" in name else "use"


def relative(path: str, project: Path | None, cwd: Path | None = None) -> str:
	"""Show a path relative to the project when it lies within it; relative paths start at `cwd`."""
	if project is None:
		return path
	try:
		return str((Path(cwd or project) / path).resolve().relative_to(Path(project).resolve()))
	except (ValueError, OSError):
		return path


def toolSummary(name: str, input, project: Path | None = None, cwd: Path | None = None) -> dict:
	"""Summarise a tool call as `{access, files?, detail?}`, never copying file contents."""
	if isinstance(input, str):
		try:
			input = json.loads(input)
		except ValueError:
			input = {}
	input = input if isinstance(input, dict) else {}
	files = [input[key] for key in PATH_KEYS if isinstance(input.get(key), str) and input[key]]
	for key in PATHS_KEYS:
		if isinstance(input.get(key), list):
			files += [item for item in input[key] if isinstance(item, str) and item]
	for key in PATCH_KEYS:
		if isinstance(input.get(key), str):
			files += PATCH_FILE.findall(input[key])
	for edit in input.get("edits", []) if isinstance(input.get("edits"), list) else []:
		if isinstance(edit, dict) and isinstance(edit.get("filePath", edit.get("path")), str):
			files.append(edit.get("filePath", edit.get("path")))
	summary = {"access": toolAccess(name)}
	files = list(dict.fromkeys(relative(item, project, cwd) for item in files if item != "/dev/null"))
	if files:
		summary["files"] = files
	detail = next((input[key] for key in DETAIL_KEYS if isinstance(input.get(key), str) and input[key]), None)
	if detail:
		detail = " ".join(detail.split())
		summary["detail"] = detail if len(detail) <= DETAIL_LIMIT else detail[:DETAIL_LIMIT - 1] + "…"
	return summary


def recordFiles(record: dict, event: dict) -> bool:
	"""Add the files a completed tool call touched to `record["files"]`, telling if it changed; failed calls touched nothing known."""
	if event.get("content") != "completed" or not event.get("files"):
		return False
	files = record.setdefault("files", {"read": [], "written": []})
	kind = "written" if event.get("access") == "write" else "read"
	added = [item for item in event["files"] if item not in files[kind]]
	files[kind] += added
	return bool(added)

# EOF
