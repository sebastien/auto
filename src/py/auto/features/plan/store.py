"""Module: store — Single-writer, recoverable plan revision commits."""

from __future__ import annotations

import fcntl
import json
import threading
from contextlib import contextmanager
from pathlib import Path

from auto.features.plan.model import Revision
from auto.utils.data import AutoError, atomicWrite, identity, jsonText, readJson, timestamp


HELD = threading.local()


def planPath(context, plan=None) -> Path:
	path = plan or context.plan
	if path is None:
		raise AutoError("This operation requires a plan")
	path = Path(path)
	if not path.is_absolute() and len(path.parts) == 1:
		path = context.project / ".auto/plans" / path
	if not (path / "plan.md").exists():
		raise AutoError(f"Plan not found: {path}")
	return path.resolve()


@contextmanager
def locked(path: Path):
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
	file = path / "journal.jsonl"
	if not file.exists():
		return []
	try:
		return [json.loads(line) for line in file.read_text().splitlines() if line]
	except ValueError as error:
		raise AutoError("Invalid plan journal; refusing to overwrite history", "system") from error


def append(path: Path, revision: dict) -> None:
	rows = journal(path)
	if any(row["id"] == revision["id"] for row in rows):
		return
	row = {**revision, "seq": len(rows) + 1}
	# Atomic replacement retains an append-only logical history and avoids torn lines.
	old = (path / "journal.jsonl").read_text() if (path / "journal.jsonl").exists() else ""
	atomicWrite(path / "journal.jsonl", old + jsonText(row) + "\n")


def recover(context, path: Path) -> None:
	"""Roll forward a staged revision idempotently, while holding the writer lock."""
	pending = readJson(path / ".pending.json")
	if not pending:
		return
	if pending["records"]:
		from auto.runtime.operations import Knowledge
		store = Knowledge(context)
		for record in pending["records"]:
			store.write(record["kind"], record["value"], record.get("expected", ""))
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
