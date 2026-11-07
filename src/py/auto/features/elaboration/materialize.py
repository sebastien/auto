"""Module: materialize — Freeze task templates and retain reference origins."""

from __future__ import annotations

from auto.features.task.model import Task, createTaskFromText
from auto.runtime.operations import Resolve
from auto.utils.data import merge
from auto.utils.references import relocate, relocateMeta

# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------


def materialize(context, value: dict, parent: str) -> Task:
	"""Instantiate a task from its template and elaborated fields."""
	definition = dict(value)
	meta, parts, description = {}, {}, ""
	if definition.get("from"):
		ref = definition["from"]
		ref = ref["ref"] if isinstance(ref, dict) else ref
		content = Resolve(context, ref)
		template = createTaskFromText(content.text, content.path)
		meta = relocateMeta(template.meta, content.path.parent)
		parts = {name: relocate(body, content.path.parent) for name, body in template.sections.items()}
		description = template.description
		definition["from"] = {"ref": ref, "digest": content.digest}
	merged = merge(meta, relocateMeta({key: item for key, item in definition.items() if key not in {"sections", "description"}}, context.project))
	merged["id"], merged["parent"] = definition["id"], parent
	return Task(definition["id"], merged, {**parts, **{name: relocate(body, context.project) for name, body in definition.get("sections", {}).items()}},
		context.plan / "tasks" / (definition["id"] + ".md"), definition.get("description", description))

# EOF
