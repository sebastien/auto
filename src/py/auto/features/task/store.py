"""Module: store — Run directories, journals and receipt persistence."""

from __future__ import annotations

import os
from pathlib import Path

from auto.features.plan.events import recordPlanStatus, recordRun
from auto.features.plan.references import readReceipt
from auto.features.plan.store import locked, runPlanPath
from auto.runtime.model import TERMINAL
from auto.utils.data import atomicWrite, identifier, jsonText
from auto.utils.layout import runsPath


def runDirectory(context, run) -> Path:
	"""Return a run's directory, discovering its plan when none is selected."""
	if context.runScope is not None:
		return context.runScope.directory(context, run)
	return runsPath(runPlanPath(context, run)) / identifier(run["id"] if isinstance(run, dict) else run)


def emit(path: Path, event: dict) -> None:
	"""Append one event to a run journal and flush it durably."""
	with (path / "journal.jsonl").open("a") as stream:
		stream.write(jsonText(event) + "\n")
		stream.flush()
		os.fsync(stream.fileno())


def saveRun(context, path, run, state=None):
	"""Persist a run receipt or its active state."""
	if context.runScope is not None:
		return context.runScope.save(context, path, run, state)
	plan = path.parent.parent
	with locked(plan):
		previous = readReceipt(path)
		changed = previous is None or previous["status"] != run["status"] or previous.get("gap") != run.get("gap")
		if changed:
			recordPlanStatus(context, plan)
		if state is not None:
			atomicWrite(path / "state.json", jsonText(state))
		atomicWrite(path / ("receipt.json" if run["status"] in TERMINAL else "active.json"), jsonText(run))
		if run["status"] in TERMINAL:
			(path / "active.json").unlink(missing_ok=True)
		if changed:
			recordRun(context, plan, run)


# EOF
