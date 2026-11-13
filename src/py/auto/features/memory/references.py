"""Canonical agent and consolidation output, receipt and journal references."""

from auto.features.memory.config import agentsFor
from auto.runtime.model import Content
from auto.runtime.operations import Knowledge
from auto.utils.data import AutoError, digest, identifier, readJson


def agentContent(context, ref):
	parts = ref.removeprefix("auto:agent/").split("/")
	name = identifier(parts[0])
	config = agentsFor(context).get(name)
	if config is None:
		raise AutoError(f"Unknown Auto agent: {name}")
	if len(parts) == 1:
		content = Knowledge(context).readReference(config["agent"])
		return Content(ref, content.data, content.digest, content.path)
	if len(parts) not in (3, 4) or parts[1] != "run":
		raise AutoError(f"Unknown agent reference: {ref}")
	path = context.project / ".auto/agents" / name / "runs" / identifier(parts[2])
	part = parts[3] if len(parts) == 4 else "output"
	if part == "output":
		run = readJson(path / "receipt.json") or readJson(path / "active.json") or {}
		file = path / run.get("outputFile", "output.json")
	elif part in {"receipt", "journal"}:
		file = path / ("receipt.json" if part == "receipt" else "journal.jsonl")
	else:
		raise AutoError(f"Unknown agent run reference part: {part}")
	try:
		data = file.read_bytes()
	except OSError as error:
		raise AutoError(f"Cannot resolve {ref}: {error}") from error
	return Content(ref, data, digest(data), file)
