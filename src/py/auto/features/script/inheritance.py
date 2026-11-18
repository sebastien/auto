"""Module: inheritance — Scripts specialising a template with `from`."""

from __future__ import annotations

import re

from auto.features.script.model import Script
from auto.runtime.operations import Resolve, isReference
from auto.utils.data import AutoError, document, merge, sections
from auto.utils.references import relocate, relocateMeta

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------

# Catalogue metadata describes the template itself, not its specialisations.
OWN_KEYS = ("kind", "identity", "from")
# The input receiving the parts of a body that fill no other input.
BRIEF = "brief"

# -----------------------------------------------------------------------------
#
# UTILITIES
#
# -----------------------------------------------------------------------------


def templateRef(value: str) -> str:
	"""Expand the catalogue shorthand `kind/name` to `auto:catalogue/kind/name`."""
	return value if isReference(value) else "auto:catalogue/" + value


def inputName(heading: str) -> str:
	"""Map a section heading to the input it fills (eg. `Out of scope` → `out_of_scope`)."""
	return re.sub(r"[^a-z0-9]+", "_", heading.lower()).strip("_")


def listItems(text: str) -> list[str]:
	"""Read a section bound to a list input: its bullet items, or the whole text as one item."""
	items = []
	for line in text.splitlines():
		bullet = re.match(r"\s*[-*+] +(.+)", line)
		if bullet:
			items.append(bullet[1].strip())
		elif items and line.strip():
			# A wrapped item continues on indented lines.
			items[-1] += " " + line.strip()
	return items or [text]


def bindSections(body: str, inputs: dict, given: dict) -> tuple[dict, str]:
	"""Bind the `##` sections of a body to the inputs they name; return the rest as text.

	When the rest fills `brief`, its sections become `####`, to nest under the
	template's own headings."""
	intro, parts = sections(body)
	bound, rest = {}, [intro.strip()] if intro.strip() else []
	level = "####" if BRIEF in inputs else "##"
	for heading, text in parts.items():
		name = inputName(heading)
		if name == BRIEF and name in inputs:
			rest.append(text)
			continue
		if name not in inputs:
			rest.append(f"{level} {heading}\n\n{text}")
			continue
		if name in given:
			raise AutoError(f"Input {name} is given both as a section and in the frontmatter", recovery=f"Keep one of `## {heading}` or inputs.{name}.")
		# Section text is data: escaping keeps `!` and `PATH` forms from being resolved.
		bound[name] = listItems(text) if isinstance(inputs[name], list) else "\\" + text
	return bound, "\n\n".join(rest)

# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------


def inheritTemplate(context, script: Script, stack: tuple = ()) -> Script:
	"""Compose a script with the template named by its `from`, if any.

	The script's metadata takes precedence over the template's, its `##`
	sections fill the template inputs they name, and what remains fills
	`brief` (or follows the template's process when it has no `brief`).
	"""
	ref = script.meta.get("from")
	if not isinstance(ref, str):
		# Materialised plan tasks record `from` as `{ref, digest}`: already composed.
		return script
	ref = templateRef(ref)
	if len(stack) >= context.maxDepth:
		raise AutoError(f"Template inheritance is too deep: {ref}")
	content = Resolve(context, ref, script.path.parent)
	# Receipts name local templates by path, so that runs compare across folders.
	ref = str(content.path) if ref.startswith(("./", "../", "/")) else ref
	if ref in stack or content.path == script.path.resolve():
		raise AutoError(f"Recursive template inheritance: {ref}")
	meta, body = document(content.text)
	_, parts = sections(body)
	# A task template keeps its instructions in `## Process`; a script template is its body.
	parent = inheritTemplate(context, Script(parts.get("Process", body), content.path, meta, content.ref), (*stack, ref))
	parentMeta = relocateMeta({key: value for key, value in parent.meta.items() if key not in OWN_KEYS}, content.path.parent)
	childMeta = relocateMeta({key: value for key, value in script.meta.items() if key != "from"}, script.path.parent)
	inputs = parentMeta.get("inputs", {})
	bound, rest = bindSections(script.content, inputs, childMeta.get("inputs", {}))
	if rest and BRIEF in inputs:
		if BRIEF in childMeta.get("inputs", {}):
			raise AutoError(f"Input {BRIEF} is given both as body text and in the frontmatter")
		bound[BRIEF], rest = "\\" + rest, ""
	composed = merge(parentMeta, childMeta, {"inputs": bound} if bound else {})
	template = {"ref": ref, "digest": content.digest, "script": str(script.path)}
	if parent.template:
		template["templates"] = [parent.template["ref"], *parent.template.get("templates", [])]
	artifact = composed.get("artifact")
	if isinstance(artifact, str) and artifact:
		artifactContent = Resolve(context, artifact, content.path.parent)
		artifactMeta, _ = document(artifactContent.text)
		output = relocateMeta({"output": artifactMeta.get("output", {})}, artifactContent.path.parent)["output"]
		# The artifact gives the output contract; explicit output values win.
		composed["output"] = merge(output, composed.get("output", {}))
		template["artifact"] = str(artifactContent.path) if artifact.startswith(("./", "../", "/")) else artifact
	text = parent.content.rstrip("\n") + ("\n\n" + relocate(rest, script.path.parent) if rest else "") + "\n"
	# The content's own references are relative to the template that wrote it.
	return Script(text, parent.path, composed, script.ref, template)

# EOF
