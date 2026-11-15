"""Module: support — Shared helpers for tests that lay out plans by hand."""

from pathlib import Path

from auto.utils.layout import planFile


def rootFile(plan: Path) -> Path:
	"""Return the root task file of a plan directory, creating its folder."""
	file = planFile(Path(plan))
	file.parent.mkdir(parents=True, exist_ok=True)
	return file

# EOF
