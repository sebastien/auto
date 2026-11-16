"""Module: workspace — What changed in a project's git working tree while Auto ran.

A snapshot records the commit checked out and the content of every file that
differs from it (uncommitted or untracked, ignored files left out). Comparing
two snapshots gives the files added, modified or deleted between them, whether
or not they were committed in between. A project outside git has no snapshot.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

from auto.utils.layout import STORE

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------

ADDED = "added"
MODIFIED = "modified"
DELETED = "deleted"

# -----------------------------------------------------------------------------
#
# UTILITIES
#
# -----------------------------------------------------------------------------


def git(project: Path, *args: str) -> str | None:
	"""Return the output of a git command run in `project`, or None when it fails."""
	try:
		result = subprocess.run(["git", "-C", str(project), *args], capture_output=True, text=True, timeout=60)
	except (OSError, subprocess.SubprocessError):
		return None
	return result.stdout if result.returncode == 0 else None


def names(output: str | None) -> set[str]:
	"""Split NUL-separated paths, leaving out Auto's own store."""
	return {name for name in (output or "").split("\0") if name and name.split("/")[0] != STORE}


def blob(path: Path) -> str | None:
	"""Return the git blob identity of a file's content, or None when it is absent (or a directory)."""
	try:
		data = os.readlink(path).encode() if path.is_symlink() else path.read_bytes()
	except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
		return None
	except OSError:
		return "unreadable"
	return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def tree(project: Path, head: str | None) -> dict[str, str]:
	"""Return the blob of every file of a commit, under the project directory."""
	if head is None:
		return {}
	result = {}
	for line in (git(project, "ls-tree", "-r", "-z", head) or "").split("\0"):
		meta, _, name = line.partition("\t")
		if name:
			result[name] = meta.split()[2]
	return result

# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------


def snapshot(project: Path) -> dict | None:
	"""Record the commit of the project's working tree and the content of the files that differ from it."""
	if git(project, "rev-parse", "--is-inside-work-tree") is None:
		return None
	head = (git(project, "rev-parse", "--verify", "-q", "HEAD") or "").strip() or None
	# Without a commit yet, every file in the index differs from it.
	tracked = git(project, "diff", "--name-only", "--relative", "-z", "HEAD") if head else git(project, "ls-files", "-z")
	files = names(tracked) | names(git(project, "ls-files", "-z", "--others", "--exclude-standard"))
	return {"head": head, "files": {name: blob(project / name) for name in sorted(files)}}


def changes(project: Path, before: dict | None) -> list[dict] | None:
	"""Return the files added, modified or deleted since the `before` snapshot, or None outside git."""
	after = snapshot(project)
	if before is None or after is None:
		return None
	candidates = set(before["files"]) | set(after["files"])
	if before["head"] != after["head"]:
		if before["head"] and after["head"]:
			candidates |= names(git(project, "diff", "--name-only", "--relative", "-z", before["head"], after["head"]))
		else:
			candidates |= names(git(project, "ls-tree", "-r", "-z", "--name-only", after["head"] or before["head"]))
	trees = {}
	def content(state: dict, name: str):
		if name in state["files"]:
			return state["files"][name]
		if state["head"] not in trees:
			trees[state["head"]] = tree(project, state["head"])
		return trees[state["head"]].get(name)
	result = []
	for name in sorted(candidates):
		old, new = content(before, name), content(after, name)
		if old != new:
			result.append({"path": name, "change": ADDED if old is None else DELETED if new is None else MODIFIED})
	return result

# EOF
