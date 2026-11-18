"""Module: catalogue — Layered framework assets and source-relative companions."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from auto.runtime.model import Content
from auto.utils.data import AutoError, digest, document, identifier, sections

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------

# Project scripts matched by `[catalogue] actions` globs, listed as task entries.
ACTIONS = "actions"

# -----------------------------------------------------------------------------
#
# UTILITIES
#
# -----------------------------------------------------------------------------


def builtinCatalogue() -> Path:
	"""Locate the shipped catalogue in a checkout or an installation."""
	builtin = Path(__file__).resolve().parents[4] / "md/auto/catalogue"
	return builtin if builtin.is_dir() else Path(sys.prefix) / "share/auto/catalogue"


def layers(context):
	"""Return catalogue directory layers in decreasing precedence."""
	return [("project", context.project / ".auto/catalogue"),
		("user", Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "auto/catalogue"), ("builtin", builtinCatalogue())]


def actionPatterns(context) -> list[str]:
	"""Return the validated `[catalogue] actions` globs, relative to the project."""
	config = context.defaults.get("catalogue") or {}
	patterns = config.get(ACTIONS, []) if isinstance(config, dict) else None
	if not isinstance(patterns, list) or any(not isinstance(pattern, str) or not pattern or Path(pattern).is_absolute() for pattern in patterns):
		raise AutoError("catalogue.actions must be a list of glob patterns relative to the project", recovery='Use eg. [catalogue] actions = ["action-*.md"].')
	return patterns


def actionFiles(context) -> dict[str, Path]:
	"""Map project actions, matched by the `[catalogue] actions` globs, from name to file.

	An action is named by its file stem (eg. `action-document_audit`); the
	first match wins, and stems that are not identifiers are left out."""
	found = {}
	for pattern in actionPatterns(context):
		for path in sorted(context.project.glob(pattern)):
			if path.is_file() and path.suffix == ".md" and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", path.stem):
				found.setdefault(path.stem, path.resolve())
	return found


def entryPaths(context, kind: str, name: str) -> list[tuple[str, Path]]:
	"""Return where an entry may be, by decreasing precedence: actions come after the project catalogue."""
	result = []
	for layer, root in layers(context):
		result.append((layer, root / kind / (name + ".md")))
		if layer == "project" and kind == "task" and name in (actions := actionFiles(context)):
			result.append((ACTIONS, actions[name]))
	return result


def locate(context, identity: str) -> tuple[str, Path]:
	"""Find the layer and file of a catalogue reference."""
	parts = identity.removeprefix("auto:catalogue/").split("/")
	if len(parts) != 2:
		raise AutoError("Catalogue references require kind/name")
	kind, name = map(identifier, parts)
	for layer, path in entryPaths(context, kind, name):
		if path.is_file():
			return layer, path
	raise AutoError(f"Catalogue entry not found: {identity}")


def signature(context, meta: dict, body: str, path: Path) -> dict:
	"""Describe how a task entry is used: what it specialises, its inputs and params."""
	from auto.features.script.inheritance import inheritTemplate
	from auto.features.script.model import Script
	result = {}
	inputs = meta.get("inputs", {})
	if isinstance(meta.get("from"), str):
		composed = inheritTemplate(context, Script(body, path, meta))
		result["specialises"] = composed.template["ref"]
		inputs = composed.meta.get("inputs", {})
		# What the action gives itself, in its frontmatter or sections, is not
		# left to its callers; bound section text is an escaped Literal.
		given = set(meta.get("inputs", {})) | set(sectionInputs(body)) | {name for name, value in inputs.items()
			if isinstance(value, str) and value.startswith("\\")}
		inputs = {name: value for name, value in inputs.items() if name not in given}
		# The template's description says how to specialise it, not what this
		# action does: without its own, an action is described by its first paragraph.
		intro = sections(body)[0].strip().split("\n\n")[0].replace("\n", " ")
		own = {"title": meta.get("title") or composed.meta.get("title"), "description": meta.get("description") or intro or composed.meta.get("description")}
		meta = {**composed.meta, **own}
	for key in ("title", "description"):
		if meta.get(key):
			result[key] = meta[key]
	if isinstance(inputs, dict) and inputs:
		result["inputs"] = sorted(inputs)
		required = sorted(name for name, value in inputs.items() if value is None)
		if required:
			result["required"] = required
	if isinstance(meta.get("params"), dict) and meta["params"]:
		result["params"] = meta["params"]
	for key in ("access", "artifact"):
		if isinstance(meta.get(key), str):
			result[key] = meta[key]
	return result


def sectionInputs(body: str) -> list[str]:
	"""Return the input names the `##` sections of a body give."""
	from auto.features.script.inheritance import inputName
	return [inputName(heading) for heading in sections(body)[1]]

# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------


def catalogueContent(context, identity):
	"""Resolve a catalogue reference from the first matching layer."""
	_, path = locate(context, identity)
	data = path.read_bytes()
	return Content(identity, data, digest(data), path)


def CatalogueGet(context, identity: str) -> dict:
	"""Read the first matching framework entry, retaining its layer and origin."""
	layer, path = locate(context, identity)
	data = path.read_bytes()
	meta, body = document(data.decode("utf-8"))
	return {**meta, "identity": identity, "content": body, "path": str(path), "digest": digest(data), "layer": layer}


def CatalogueList(context, kind: str | None = None, query: str | None = None) -> list:
	"""List entries once; project overrides actions, then user, then built-in.

	Task entries also give their signature: the template they specialise, and
	the inputs (`required` ones having no default), params, access and
	artifact they take, so that an Elaborator can instantiate them."""
	entries = {}
	if kind:
		identifier(kind)
	candidates = []
	for layer, root in layers(context):
		candidates += [(path.parent.name, path.stem) for path in sorted(root.glob(f"{kind or '*'}/*.md"))]
		if layer == "project" and kind in (None, "task"):
			candidates += [("task", name) for name in sorted(actionFiles(context))]
	for entryKind, name in candidates:
		if "." in name:
			continue
		identity = "auto:catalogue/" + entryKind + "/" + name
		if identity in entries:
			continue
		entry = CatalogueGet(context, identity)
		row = {key: entry.get(key) for key in ("identity", "kind", "title", "description", "layer")}
		if entryKind == "task":
			row["kind"] = row["kind"] or "task"
			try:
				meta, body = document(Path(entry["path"]).read_text(encoding="utf-8"))
				row.update(signature(context, meta, body, Path(entry["path"])))
			except AutoError as error:
				row["error"] = str(error)
		entries[identity] = row
	return [entry for entry in entries.values() if not query or query.lower() in str(entry).lower()]

# EOF
