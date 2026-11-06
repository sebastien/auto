"""Module: config — Runtime defaults and interface context discovery."""

from __future__ import annotations

import os
from pathlib import Path

from auto.runtime.model import Context
from auto.utils.config import loadConfig
from auto.utils.data import identifier


def contextFor(directory: Path | None = None, plan: str | None = None, sources: dict | None = None, **overrides) -> Context:
	cwd = (directory or Path.cwd()).resolve()
	project = next((p for p in (cwd, *cwd.parents) if (p / ".auto").is_dir() or (p / ".git").exists()), cwd)
	config = loadConfig(project, sources)
	selected = plan or os.environ.get("AUTO_PLAN")
	planPath = None
	if selected:
		planPath = Path(selected).resolve() if "/" in selected or Path(selected).is_dir() else project / ".auto/plans" / identifier(selected)
	else:
		planPath = next((p for p in (cwd, *cwd.parents) if (p / "plan.md").is_file() and p.parent.name == "plans"), None)
	explicit = {}
	for key in ("engine", "provider", "model", "agent"):
		value = overrides.get(key) if overrides.get(key) is not None else os.environ.get("AUTO_" + key.upper())
		if value is not None:
			explicit[key] = value
			if sources is not None:
				for path in list(sources):
					if path[0] == key:
						del sources[path]
				sources[(key,)] = "--" + key if overrides.get(key) is not None else "AUTO_" + key.upper()
	return Context(project=project, plan=planPath, config=explicit, defaults=config)

# EOF
