"""Module: catalogue — Layered framework assets and source-relative companions."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from auto.runtime.model import Content
from auto.utils.data import AutoError, digest, document, identifier

# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------


def layers(context):
	"""Return catalogue layers in decreasing precedence."""
	builtin = Path(__file__).resolve().parents[4] / "md/auto/catalogue"
	if not builtin.is_dir():
		builtin = Path(sys.prefix) / "share/auto/catalogue"
	return [("project", context.project / ".auto/catalogue"),
		("user", Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "auto/catalogue"), ("builtin", builtin)]


def catalogueContent(context, identity):
	"""Resolve a catalogue reference from the first matching layer."""
	parts = identity.removeprefix("auto:catalogue/").split("/")
	if len(parts) != 2:
		raise AutoError("Catalogue references require kind/name")
	kind, name = map(identifier, parts)
	for _, root in layers(context):
		path = root / kind / (name + ".md")
		if path.is_file():
			data = path.read_bytes()
			return Content(identity, data, digest(data), path)
	raise AutoError(f"Catalogue entry not found: {identity}")


def CatalogueGet(context, identity: str) -> dict:
	"""Read the first matching framework entry, retaining its layer and origin."""
	content = catalogueContent(context, identity)
	meta, body = document(content.text)
	layer = next(name for name, root in layers(context) if content.path.is_relative_to(root))
	return {**meta, "identity": identity, "content": body, "path": str(content.path), "digest": content.digest, "layer": layer}


def CatalogueList(context, kind: str | None = None, query: str | None = None) -> list:
	"""List entries once; project overrides user, which overrides built-in."""
	entries = {}
	if kind:
		identifier(kind)
	for _, root in layers(context):
		for path in sorted(root.glob(f"{kind or '*'}/*.md")):
			if "." in path.stem:
				continue
			identity = "auto:catalogue/" + path.parent.name + "/" + path.stem
			if identity in entries:
				continue
			entry = CatalogueGet(context, identity)
			entries[identity] = {key: entry.get(key) for key in ("identity", "kind", "title", "description", "layer")}
	return [entry for entry in entries.values() if not query or query.lower() in str(entry).lower()]

# EOF
