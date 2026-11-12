"""Module: store — Single-writer, recoverable plan revision commits."""

from __future__ import annotations

import fcntl
import json
import threading
from contextlib import contextmanager
from pathlib import Path

from auto.features.plan.model import Revision
from auto.runtime.operations import Knowledge
from auto.utils.data import AutoError, atomicWrite, identity, jsonText, readJson, timestamp

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------


HELD = threading.local()

# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------


def planPath(context, plan=None) -> Path:
	"""Resolve the directory of the current or named plan."""
	path = plan or context.plan
	if path is None:
		raise AutoError("This operation requires a plan", recovery='Select an existing plan with --plan <name-or-path> or AUTO_PLAN. '
			'If you have no plan, create one with auto plan create "<goal>". '
			'Task runs take a task ID defined in that plan, not a catalogue Markdown path.')
	path = Path(path)
	if not path.is_absolute() and len(path.parts) == 1:
		path = context.project / ".auto/plans" / path
	if not (path / "plan.md").exists():
		raise AutoError(f"Plan not found: {path}", recovery='Point --plan at a directory containing plan.md, '
			'or create a plan with auto plan create "<goal>". Check AUTO_PLAN if you did not pass --plan.')
	return path.resolve()


@contextmanager
def locked(path: Path):
	"""Serialise plan writers using an inter-process lock."""
	path = path.resolve()
	held = getattr(HELD, "paths", set())
	if path in held:
		yield
		return
	# Keep the inode: unlinking lock files can admit two writers on different inodes.
	with (path / ".lock").open("a") as stream:
		fcntl.flock(stream, fcntl.LOCK_EX)
		HELD.paths = {*held, path}
		try:
			yield
		finally:
			HELD.paths = held


def journal(path: Path) -> list:
	"""Read the plan journal, refusing to overwrite invalid history."""
	file = path / "journal.jsonl"
	if not file.exists():
		return []
	try:
		return [json.loads(line) for line in file.read_text().splitlines() if line]
	except ValueError as error:
		raise AutoError("Invalid plan journal; refusing to overwrite history", "system") from error


def append(path: Path, revision: dict) -> None:
	"""Append one revision to the journal, assigning its sequence."""
	rows = journal(path)
	if any(row["id"] == revision["id"] for row in rows):
		return
	row = {**revision, "seq": len(rows) + 1}
	# Atomic replacement retains an append-only logical history and avoids torn lines.
	old = (path / "journal.jsonl").read_text() if (path / "journal.jsonl").exists() else ""
	atomicWrite(path / "journal.jsonl", old + jsonText(row) + "\n")


def mergeRecord(store, record: dict) -> None:
	"""Re-apply one staged record, reconciling a concurrent change from its base."""
	for attempt in range(3):
		value, expected = record["value"], record.get("expected", "")
		if expected and record.get("base"):
			current = store.get(value["identity"])
			if current["digest"] != expected:
				base = record["base"]
				missing = object()
				keys = (base.keys() | value.keys()) - {"digest"}
				changed = {key for key in keys if base.get(key, missing) != value.get(key, missing)}
				conflicts = any(current.get(key, missing) != base.get(key, missing) and current.get(key, missing) != value.get(key, missing) for key in changed)
				if conflicts:
					raise AutoError("Conflicting Memo update requires reconciliation", "system")
				removed = changed - value.keys()
				value = {**current, **{key: value[key] for key in changed if key in value}}
				for key in removed:
					value.pop(key, None)
				expected = current["digest"]
		try:
			store.write(record["kind"], value, expected)
			return
		except AutoError:
			if not expected or attempt == 2 or store.get(value["identity"])["digest"] == expected:
				raise


def recover(context, path: Path) -> None:
	"""Roll forward a staged revision idempotently, while holding the writer lock."""
	pending = readJson(path / ".pending.json")
	if not pending:
		return
	if pending["records"]:
		store = Knowledge(context)
		for record in pending["records"]:
			mergeRecord(store, record)
	for name, content in pending["writes"].items():
		file = path / name
		if not file.resolve().is_relative_to(path):
			raise AutoError("Invalid staged write path", "system")
		if content is None:
			file.unlink(missing_ok=True)
		else:
			atomicWrite(file, content)
	append(path, pending["revision"])
	(path / ".pending.json").unlink()


def commit(context, path: Path, operations: list, writes: dict | None = None, records: list | None = None,
	reason: str = "", revision: str | None = None, **metadata) -> dict:
	"""Stage and publish a validated change; caller holds the plan lock."""
	if "provenance" in metadata:
		metadata["context"] = metadata.pop("provenance")
	row = Revision(id=revision or identity(), author=context.author, date=timestamp(), reason=reason, operations=operations, metadata=metadata).asDict()
	atomicWrite(path / ".pending.json", jsonText({"revision": row, "writes": writes or {}, "records": records or []}))
	recover(context, path)
	return journal(path)[-1]

# EOF
