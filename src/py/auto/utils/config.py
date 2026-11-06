"""Module: config — Load layered user and project configuration."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from auto.utils.data import AutoError, merge, yamlLoad


def loadConfig(project: Path, sources: dict | None = None) -> dict:
	"""Merge home, XDG and project configuration, preferring TOML at each location."""
	xdg = os.environ.get("XDG_CONFIG_HOME")
	configHome = Path(xdg) if xdg and Path(xdg).is_absolute() else Path.home() / ".config"
	result = {}
	for directory in (Path.home() / ".auto", configHome / "auto", project / ".auto"):
		for suffix in ("yaml", "toml"):
			file = directory / f"config.{suffix}"
			if not file.is_file():
				continue
			try:
				value = tomllib.loads(file.read_text(encoding="utf-8")) if suffix == "toml" else yamlLoad(file.read_text(encoding="utf-8"))
			except (tomllib.TOMLDecodeError, UnicodeError, AutoError) as error:
				raise AutoError(f"Invalid configuration {file}: {error}") from error
			if not isinstance(value, dict):
				raise AutoError(f"Configuration must be a mapping: {file}")
			if sources is not None:
				updateSources(result, value, sources, str(file))
			result = merge(result, value)
	return result


def updateSources(current: dict, layer: dict, sources: dict, source: str, prefix: tuple = ()) -> None:
	"""Record the winning source for each leaf using the same rules as `merge`."""
	for key, value in layer.items():
		path = (*prefix, key)
		previous = current.get(key)
		if not isinstance(value, dict) or not isinstance(previous, dict):
			for existing in list(sources):
				if existing[:len(path)] == path:
					del sources[existing]
		if isinstance(value, dict) and value:
			sources.pop(path, None)
			updateSources(previous if isinstance(previous, dict) else {}, value, sources, source, path)
		elif not isinstance(value, dict) or not isinstance(previous, dict) or not previous:
			sources[path] = source

# EOF
