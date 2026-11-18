"""Module: config — Runtime defaults and interface context discovery."""

from __future__ import annotations

import os
import math
import re
from pathlib import Path

from auto.runtime.model import Context
from auto.utils.config import RUNTIME_TABLES, loadConfig
from auto.utils.data import AutoError, identifier, merge
from auto.utils.layout import PLAN_PREFIX, STORE, currentPlan, isPlan, planNumber, planPaths, storePath

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------

RUNTIME_DEFAULTS = {"engine": "pi", "isolated": False}
CONTEXT_KEYS = ("plan", "engine", "provider", "model", "agent")
# What an engine invocation is for, so that defaults can differ by role.
ROLES = ("planning", "tasks", "checks", "repairs", "memory", "script")
# What a run may do to the project: `read` is enforced where the engine can, and
# otherwise only warned about; the workspace is compared after the run either way.
ACCESS = ("read", "write")
# Engines that can keep a run from editing files.
READ_ONLY_ENGINES = frozenset({"pi", "opencode", "llm"})

# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------


def validateConfig(value: dict) -> None:
	"""Validate the runtime settings of one configuration layer."""
	for name in ("inputs", "params", "output", "env"):
		if name in value and (not isinstance(value[name], dict) or any(not isinstance(key, str) for key in value[name])):
			raise AutoError(f"{name} must be a string-keyed mapping")
	for name in ("engine", "provider", "model", "agent", "cwd", "prompt", "title", "description"):
		if name in value and not isinstance(value[name], str):
			raise AutoError(f"{name} must be a string")
	for name in RUNTIME_TABLES:
		if name not in value:
			continue
		table = value[name]
		if not isinstance(table, dict) or any(not isinstance(key, str) for key in table):
			raise AutoError(f"{name} must be a string-keyed mapping")
		for key, item in table.items():
			if not re.fullmatch(r"[A-Za-z0-9_-]+", key):
				raise AutoError(f"{name} names must be letters, digits, '_' or '-': {key}")
			if not isinstance(item, dict):
				raise AutoError(f"{name}.{key} must be a mapping")
			if name == "roles" and key not in ROLES:
				raise AutoError(f"Unknown role: {key}", recovery="Use one of: " + ", ".join(ROLES) + ".")
			if name == "engines" and "engine" in item:
				raise AutoError(f"engines.{key} cannot set engine")
			if any(nested in item for nested in RUNTIME_TABLES):
				raise AutoError(f"{name}.{key} cannot contain " + " or ".join(RUNTIME_TABLES))
			validateConfig(item)
	if "isolated" in value and type(value["isolated"]) is not bool:
		raise AutoError("isolated must be a boolean")
	if "access" in value and value["access"] not in ACCESS:
		raise AutoError("access must be one of: " + ", ".join(ACCESS))
	if "tools" in value and (not isinstance(value["tools"], list) or any(not isinstance(item, str) or not re.fullmatch(r"[A-Za-z0-9_*:.-]+", item) for item in value["tools"])):
		raise AutoError("tools must be a list of tool names; [] disables all tools")
	if "temperature" in value and (type(value["temperature"]) not in (int, float) or not math.isfinite(value["temperature"])):
		raise AutoError("temperature must be a finite number")
	if any(not isinstance(item, str) for item in value.get("env", {}).values()):
		raise AutoError("Environment overrides must be strings")
	for reserved in {"inputs", "params", "plan", "run", "output"} & value.get("inputs", {}).keys():
		raise AutoError(f"Input name is reserved: {reserved}")
	for name in ("pre", "post", "transform", "apply", "eval"):
		if name in value and not (isinstance(value[name], str) or isinstance(value[name], list) and all(isinstance(item, str) for item in value[name])):
			raise AutoError(f"{name} must be a command or a list of commands")
	if "each" in value:
		each = value["each"]
		if not isinstance(each, dict) or len(each) != 1 or not all(isinstance(key, str) for key in each):
			raise AutoError("each must be a mapping with a single input name")
		if next(iter(each)) in {"inputs", "params", "plan", "run", "output"}:
			raise AutoError(f"Input name is reserved: {next(iter(each))}")


def roleFor(kind: str | None = None, phase: str | None = None, attempt: int = 0) -> str:
	"""Name what an engine invocation is for, so that role defaults can apply."""
	if kind in (None, "script") and phase is None:
		return "script"
	if kind == "memory":
		return "memory"
	if attempt:
		return "repairs"
	if kind == "triage" or kind == "elaboration" and phase in (None, "Process"):
		return "planning"
	if kind == "assessment" or phase not in (None, "Process"):
		return "checks"
	return "tasks"


def runtimeConfig(context: Context, defaults: dict | None = None, role: str | None = None) -> dict:
	"""Merge runtime settings, then the selected engine's and role's defaults, then
	the caller's defaults and explicit settings, in increasing precedence."""
	base = merge(RUNTIME_DEFAULTS, context.defaults)
	engines, roles = base.pop("engines", {}) or {}, base.pop("roles", {}) or {}
	roleDefaults = (roles.get(role) or {}) if role else {}
	selected = merge(base, roleDefaults, defaults or {}, context.config)
	engine = selected.get("engine", RUNTIME_DEFAULTS["engine"])
	result = merge(base, engines.get(engine) or {}, roleDefaults, defaults or {}, context.config)
	result.pop("engines", None)
	result.pop("roles", None)
	return result


def planDirectory(plans: Path, name: str) -> Path:
	"""Return the plan directory named `name`, or the only one numbered `name` (eg. `00a` or `pln-00a`)."""
	path = plans / name
	if path.exists() or not plans.is_dir() or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
		return path
	number = name.removeprefix(PLAN_PREFIX)
	matches = [match for match in planPaths(plans.parent) if planNumber(match.name) == number]
	if len(matches) > 1:
		raise AutoError(f"Ambiguous plan: {name}", recovery="Use one of: " + ", ".join(match.name for match in matches) + ".")
	return matches[0] if matches else path


def contextFor(directory: Path | None = None, plan: str | None = None, sources: dict | None = None, **overrides) -> Context:
	"""Build the operation context from flags, environment and configuration."""
	cwd = (directory or Path.cwd()).resolve()
	project = next((p for p in (cwd, *cwd.parents) if (p / ".auto").is_dir() or (p / ".git").exists()), cwd)
	policyLayers, configurationLayers = [], []
	config = loadConfig(project, sources, policyLayers, configurationLayers)
	runtime = config.pop("runtime", {})
	validateConfig(runtime)
	config.update(runtime)
	from auto.wiring import validateContext
	validateContext(Context(project=project, defaults=config, policyLayers=policyLayers, configurationLayers=configurationLayers))
	selected = plan or os.environ.get("AUTO_PLAN")
	planPath = None
	if selected:
		planPath = Path(selected).resolve() if "/" in selected or Path(selected).is_dir() else planDirectory(storePath(project), identifier(selected))
	else:
		planPath = next((p for p in (cwd, *cwd.parents) if isPlan(p) and p.parent.name == STORE), None) or currentPlan(project)
	explicit = {}
	for key in CONTEXT_KEYS[1:]:
		value = overrides.get(key) if overrides.get(key) is not None else os.environ.get("AUTO_" + key.upper())
		if value is not None:
			explicit[key] = value
			if sources is not None:
				for path in list(sources):
					if path[:2] == ("runtime", key):
						del sources[path]
				sources[("runtime", key)] = "--" + key if overrides.get(key) is not None else "AUTO_" + key.upper()
	return Context(project=project, plan=planPath, config=explicit, defaults=config, policyLayers=policyLayers, configurationLayers=configurationLayers)

# EOF
