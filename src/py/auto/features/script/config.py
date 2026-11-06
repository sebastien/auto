"""Module: config — Validate executable metadata at the authoring boundary."""

from __future__ import annotations

import math

from auto.utils.data import AutoError


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


def commands(value) -> list[str]:
	"""Normalise a lifecycle key (`pre`, `post`, `apply`, `eval`) to a command list."""
	return [] if value is None else [value] if isinstance(value, str) else list(value)

# EOF
