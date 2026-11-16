"""Module: triage — Decide whether a goal runs directly as one task or needs a plan.

A cheap, single-turn judgement with the decision profile, made before
planning: most everyday goals need no elaboration. Triage never blocks a
goal: when it fails, the goal is planned as before.
"""

from __future__ import annotations

from auto.features.plan.policy import BUILTINS, policyLayers, ruleFor
from auto.features.script.operations import conform, executePrepared, prepare
from auto.features.task.model import createTaskFromText
from auto.features.task.operations import effectiveTask
from auto.runtime.model import Event, Limits, RunStatus, budgetValue
from auto.runtime.operations import Resolve
from auto.utils.data import AutoError
from auto.utils.workspace import git

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------

TRIAGE = "auto:catalogue/task/triage"
DIRECT = "direct"
PLAN = "plan"
ROUTES = (DIRECT, PLAN)
# Project files listed for the decision: enough to see its shape, small enough to stay cheap.
FILES = 200

# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------


def projectFiles(context) -> list:
	"""List the project's files known to git, truncated to `FILES`."""
	names = [name for name in (git(context.project, "ls-files", "-z") or "").split("\0") if name and not name.startswith(".auto/")]
	return names[:FILES] + ([f"… and {len(names) - FILES} more"] if len(names) > FILES else [])


def jsonObject(text) -> str:
	"""Return the JSON object in a model's answer: small models often fence it or add a sentence."""
	if not isinstance(text, str):
		return text
	start, end = text.find("{"), text.rfind("}")
	return text[start:end + 1] if 0 <= start < end else text


def GoalTriage(context, goal: str) -> dict:
	"""Decide how to carry out a goal: `route` (direct or plan), the `effects` it needs and why; plan when triage fails."""
	try:
		content = Resolve(context, TRIAGE)
		task = effectiveTask(context, createTaskFromText(content.text, content.path))
		layers = policyLayers(context, {})
		effects = [{"name": effect, "rule": ruleFor(effect, layers)["rule"]} for effect in BUILTINS if effect != "repo.read"]
		ctx = context.derive(inputs={"goal": goal.strip(), "files": projectFiles(context), "effects": effects},
			limits=Limits(budgetValue(task.meta.get("budget"))), question=None)
		request = prepare(ctx, task.script())
		output = None
		for event in executePrepared(ctx, request, {"kind": "triage", "task": "root"}):
			if event["type"] in (Event.SCRIPT_QUESTION, Event.POLICY_REQUEST):
				raise AutoError("Triage asked a question; it must answer on its own", "output")
			if event["type"] == Event.SCRIPT_ENDED:
				if event["status"] != RunStatus.SUCCEEDED:
					raise AutoError((event.get("failure") or {}).get("reason") or event["status"], "system")
				output = event["output"]
		decision = conform(jsonObject(output), request["output"])
	# Any failure (engine, template, output) falls back to planning: triage is an optimisation, never a gate.
	except Exception as error:
		return {"route": PLAN, "effects": [], "reason": f"Triage failed, so the goal is planned: {error}", "failed": True}
	return {"route": decision["route"], "effects": [effect for effect in dict.fromkeys(decision["effects"]) if effect in BUILTINS and effect != "repo.read"],
		"reason": decision["reason"].strip()}

# EOF
