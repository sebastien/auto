"""Conformance to ordered methodology phases, artifacts and output gates."""

from pathlib import Path

from auto.features.elaboration.catalogue import layers
from auto.features.script.sources import ScriptSources
from auto.features.elaboration.metadata import MetadataProjection
from auto.runtime.operations import Resolve, isReference
from auto.utils.data import AutoError, document, identifier


def validateMethodology(context, content, tasks, graph, *, deferred=None, overrides=None):
	"""Check known constraints and whether deferred producers can cover the rest."""
	context = context.derive(allowShell=False, variables=dict(context.variables))
	deferred, overrides = deferred or {}, overrides or {}
	method, _ = document(content.text)
	phases = method.get("phases", [])
	if not isinstance(phases, list):
		raise AutoError("Methodology phases must be a list")
	order = {}
	for phase in phases:
		if not isinstance(phase, dict) or not isinstance(phase.get("id"), str) or not phase["id"].strip():
			raise AutoError("Methodology phases require a non-empty string id")
		if phase["id"] in order:
			raise AutoError("Duplicate methodology phase: " + phase["id"])
		order[phase["id"]] = len(order)
		for name in ("artifacts", "templates"):
			values = phase.get(name, [])
			if not isinstance(values, list) or any(not isReference(value) for value in values):
				raise AutoError(f"Methodology phase {name} must be a list of references")
		if phase.get("gate") not in (None, "approval"):
			raise AutoError("Unsupported methodology phase gate: " + str(phase["gate"]))

	projections = {id: MetadataProjection(deferred.get(id, ()), overrides.get(id)) for id in tasks}
	def known(id, *path):
		return projections[id].known(*path)

	sources = {id: ScriptSources(context, task.meta.get("_template_sources")) for id, task in tasks.items()}
	def identities(ref, path):
		keys = {("reference", ref)}
		if ref.startswith(("./", "../", "/", "auto:catalogue/")):
			keys.add(("file", str(Path(path).resolve()), ref.partition("#")[2]))
		return frozenset(keys)

	def capturedPath(path, selector):
		key = ("file", str(path.resolve()), selector)
		for source in sources.values():
			for record in source.records.values():
				keys = identities(record["ref"], record["path"])
				if key in keys:
					return keys

	def requiredIdentity(ref):
		# Direct captured aliases retain their original selection. Other local
		# aliases can identify the same captured asset after source deletion.
		for source in sources.values():
			record = source.record(ref, content.path.parent)
			if record is not None:
				return identities(record["ref"], record["path"])
		target, _, selector = ref.partition("#")
		if target.startswith(("./", "../", "/")):
			captured = capturedPath(content.path.parent / target, selector)
			if captured:
				return captured
		try:
			resolved = Resolve(context, ref, content.path.parent)
		except AutoError:
			if not target.startswith("auto:catalogue/") or context.variables.get("_policyDeniedAction"):
				raise
			parts = target.removeprefix("auto:catalogue/").split("/")
			if len(parts) != 2:
				raise
			kind, name = map(identifier, parts)
			paths = [root / kind / (name + ".md") for _, root in layers(context)]
			# A live higher-priority selection must not be replaced by a stale
			# snapshot of some other catalogue layer.
			if any(path.is_file() for path in paths):
				raise
			for path in paths:
				captured = capturedPath(path, selector)
				if captured:
					return captured | {("reference", ref)}
			raise
		return identities(resolved.ref, resolved.path)

	artifacts, gates = {}, {}
	for id, task in tasks.items():
		phase = task.meta.get("phase")
		if known(id, "phase") and phase is not None:
			if not isinstance(phase, str) or phase and phase not in order:
				raise AutoError(f"Unknown methodology phase: {phase}")
		hints = task.meta.get("hints", {})
		if not isinstance(hints, dict):
			raise AutoError(f"Task {id} hints must be a mapping")
		gates[id] = hints.get("gate")
		ref = task.meta.get("artifact")
		if known(id, "artifact") and ref:
			if not isinstance(ref, str):
				raise AutoError(f"Task {id} artifact must be a reference string")
			resolved = sources[id].resolve(ref, task.path.parent)
			artifacts[id] = identities(resolved.ref, resolved.path)
	for id, dependencies in graph.items():
		phase = tasks[id].meta.get("phase")
		if not known(id, "phase") or not phase:
			continue
		for dependency in dependencies:
			if known(dependency, "phase") and order.get(tasks[dependency].meta.get("phase"), -1) > order[phase]:
				raise AutoError(f"Task {id} depends on a later phase")

	requirements = []
	for phase in phases:
		members = [id for id in tasks if known(id, "phase") and tasks[id].meta.get("phase") == phase["id"]]
		if phase.get("gate") == "approval" and any(known(id, "hints", "gate") and gates[id] != "approval" for id in members):
			raise AutoError(f"Missing phase approval gate: {phase['id']}")
		required = {}
		for ref in phase.get("artifacts", []):
			keys = requiredIdentity(ref)
			for alias in [alias for alias in required if alias & keys]:
				keys |= alias
				del required[alias]
			required[keys] = ref
		for artifact, ref in required.items():
			if not any(known(id, "artifact") and artifacts.get(id, frozenset()) & artifact for id in members):
				requirements.append((phase, artifact, "Missing phase artifact: " + ref))
		if not members and not required:
			requirements.append((phase, None, "Missing methodology phase: " + phase["id"]))

	candidates = []
	for phase, artifact, message in requirements:
		possible = []
		for id, task in tasks.items():
			if known(id, "phase") and task.meta.get("phase") != phase["id"]:
				continue
			if phase.get("gate") == "approval" and known(id, "hints", "gate") and gates[id] != "approval":
				continue
			if artifact is not None and known(id, "artifact") and not artifacts.get(id, frozenset()) & artifact:
				continue
			possible.append(id)
		if not possible:
			raise AutoError(message)
		candidates.append(possible)
	assigned = {}
	def assign(index, seen):
		for id in candidates[index]:
			if id in seen:
				continue
			seen.add(id)
			if id not in assigned or assign(assigned[id], seen):
				assigned[id] = index
				return True
		return False
	for index in range(len(requirements)):
		if not assign(index, set()):
			raise AutoError("Methodology phases and artifacts require distinct producing tasks")
