"""Module: config — Runtime defaults and interface context discovery."""

from __future__ import annotations

import os
import math
from pathlib import Path

from auto.runtime.model import Context
from auto.utils.config import loadConfig
from auto.utils.data import AutoError, identifier


def validateConfig(value: dict) -> None:
	for name in ("inputs", "params", "output", "env"):
		if name in value and (not isinstance(value[name], dict) or any(not isinstance(key, str) for key in value[name])):
			raise AutoError(f"{name} must be a string-keyed mapping")
	for name in ("engine", "provider", "model", "agent", "cwd", "prompt", "title", "description"):
		if name in value and not isinstance(value[name], str):
			raise AutoError(f"{name} must be a string")
	if "isolated" in value and type(value["isolated"]) is not bool:
		raise AutoError("isolated must be a boolean")
	if "temperature" in value and (type(value["temperature"]) not in (int, float) or not math.isfinite(value["temperature"])):
		raise AutoError("temperature must be a finite number")
	if any(not isinstance(item, str) for item in value.get("env", {}).values()):
		raise AutoError("Environment overrides must be strings")
	for reserved in {"inputs", "params", "plan", "run", "output"} & value.get("inputs", {}).keys():
		raise AutoError(f"Input name is reserved: {reserved}")
	for name in ("pre", "post", "apply", "eval"):
		if name in value and not (isinstance(value[name], str) or isinstance(value[name], list) and all(isinstance(item, str) for item in value[name])):
			raise AutoError(f"{name} must be a command or a list of commands")
	if "each" in value:
		each = value["each"]
		if not isinstance(each, dict) or len(each) != 1 or not all(isinstance(key, str) for key in each):
			raise AutoError("each must be a mapping with a single input name")
		if next(iter(each)) in {"inputs", "params", "plan", "run", "output"}:
			raise AutoError(f"Input name is reserved: {next(iter(each))}")


def contextFor(directory: Path | None = None, plan: str | None = None, sources: dict | None = None, **overrides) -> Context:
	cwd = (directory or Path.cwd()).resolve()
	project = next((p for p in (cwd, *cwd.parents) if (p / ".auto").is_dir() or (p / ".git").exists()), cwd)
	config = loadConfig(project, sources)
	runtime = config.pop("runtime", {})
	validateConfig(runtime)
	config.update(runtime)
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
					if path[:2] == ("runtime", key):
						del sources[path]
				sources[("runtime", key)] = "--" + key if overrides.get(key) is not None else "AUTO_" + key.upper()
	return Context(project=project, plan=planPath, config=explicit, defaults=config)

# EOF
