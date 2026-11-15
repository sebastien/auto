"""Module: state — The compact plan state and policy an Elaborator reads.

Each fact appears once: the elaborated task's goal is its `goal` input, the
policy is its `policy` input, and fields a reader can derive (paths, empty
values, derived goals, children, succeeded outputs) are left out.
"""

from __future__ import annotations

from auto.features.plan.operations import PlanStatus
from auto.features.task.model import defaultGoal

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------


# Task facets that are locations, derivable or owned by the plan's other inputs.
OMITTED = {"id", "path", "children", "agents", "requested_effects", "identity", "kind"}
# The target's goal and definition of done are its `goal` input.
TARGET_OMITTED = {"goal", "title", "description", "output", "artifact", "post", "eval"}
# A templated task is read from its template (`from`): only its own choices are shown.
TEMPLATE_OMITTED = {"sections", "description", "params", "pre", "post", "eval", "apply", "transform", "output", "artifact"}

# -----------------------------------------------------------------------------
#
# UTILITIES
#
# -----------------------------------------------------------------------------


def compact(value):
	"""Drop empty values (`None`, `""`, `[]`, `{}`) from nested mappings and lists."""
	if isinstance(value, dict):
		value = {key: compact(item) for key, item in value.items()}
		return {key: item for key, item in value.items() if item not in (None, "", [], {})}
	if isinstance(value, list):
		return [compact(item) for item in value]
	return value


def roundedBudget(value: dict | None) -> dict:
	"""Round budgets and usage to the precision a reader needs."""
	rounding = {"time": 0, "cost": 4}
	return {key: (int(round(amount)) if rounding.get(key) == 0 else round(amount, rounding[key])) if key in rounding
		and isinstance(amount, (int, float)) else amount for key, amount in (value or {}).items()}


def gapView(gap: dict) -> dict:
	"""Return what an Elaborator needs of an open gap."""
	return compact({"id": gap["id"], "kind": gap.get("kind") or gap.get("gap_kind"), "question": gap.get("question"),
		"needed_by": gap.get("needed_by"), "options": gap.get("options"),
		"subjects": [subject.get("ref", subject) if isinstance(subject, dict) else subject for subject in gap.get("subjects", [])]})


def taskView(facet: dict, detail: dict, target: bool) -> dict:
	"""Return a task's definition and progress, without derivable or duplicated fields."""
	value = {key: item for key, item in facet.items() if key not in OMITTED and not key.startswith("_")}
	if value.get("goal") == defaultGoal(facet, facet.get("description", "")):
		value.pop("goal")
	if target:
		value = {key: item for key, item in value.items() if key not in TARGET_OMITTED}
		value["sections"] = {name: body for name, body in value.get("sections", {}).items() if name not in {"Post", "Evaluation"}}
	if value.get("from") and not target:
		value = {key: item for key, item in value.items() if key not in TEMPLATE_OMITTED}
	if isinstance(value.get("from"), dict):
		value["from"] = value["from"].get("ref")
	if "budget" in value:
		value["budget"] = roundedBudget(value["budget"])
	if not value.get("attempts"):
		value.pop("attempts", None)
	# A parent depends on its children, which name it as their `parent`.
	value["dependencies"] = sorted(set(detail.get("dependencies", [])) - {facet.get("parent"), *facet.get("children", [])})
	run = {key: item for key, item in (detail.get("run") or {}).items() if key != "status"}
	value["run"] = run if set(run) - {"id"} else None
	return compact(value)

# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------


def elaborationState(context, plan: dict, target: str, reason: str | None) -> dict:
	"""Return the plan state for elaborating `target`, empty for a plan's first elaboration."""
	status = PlanStatus(context)
	tasks = plan["tasks"]
	if list(tasks) == ["root"] and not tasks["root"].get("attempts") and not status["gaps"]:
		return {}
	return compact({"reason": reason, "status": status["status"], "budget": roundedBudget(status["budget"]),
		"usage": roundedBudget(status["usage"]), "hold": status["hold"], "gaps": [gapView(gap) for gap in status["gaps"]],
		"stale": [pin["ref"] for pin in status["stale"]], "staleRuns": [run["run"] for run in status["staleRuns"]],
		"tasks": {id: taskView(facet, status["taskDetails"][id], id == target) for id, facet in tasks.items()}})


def policyView(policy: dict) -> dict:
	"""Return the rules that differ from the default, and grants by scope (`plan` or `task:ID`)."""
	default = policy["default"]
	grants = {}
	for grant in policy["grants"]:
		_, _, task = grant["scope"].partition("/task/")
		grants.setdefault("task:" + task if task else "plan", []).append(grant["effect"])
	return compact({"default": default, "rules": {effect: rule["rule"] for effect, rule in policy["rules"].items() if rule["rule"] != default},
		"grants": {scope: sorted(set(effects)) for scope, effects in grants.items()}})

# EOF
