"""Admit plan runs against measured usage and durable in-flight allocations."""

from auto.features.plan.model import createPlan
from auto.features.task.operations import RunList, hasCheck
from auto.runtime.model import RunStatus, budgetValue
from auto.utils.data import AutoError, readJson
from auto.utils.layout import runsPath


def planRunBudget(context, task, budget, run=None, *, assessment=False):
	"""Caller holds the plan writer lock until the active receipt is published."""
	limits = budgetValue(createPlan(context.plan).meta.get("budget"))
	if not limits:
		return budget
	usage = (run or {}).get("usage", {})
	runs = RunList(context)
	assessments = {value["id"]: readJson(runsPath(context.plan) / value["id"] / "assessment.json") for value in runs}
	requested = budgetValue(task.meta.get("budget")) if task.id != "root" and not assessment else {}
	emptyAggregate = not task.sections.get("Process") and not any(hasCheck(task, phase) for phase in ("Pre", "Post", "Apply", "Evaluation"))
	result = dict(budget)
	for key, maximum in limits.items():
		spent = sum(value["usage"].get(key, 0) for value in runs)
		reserved = sum(max(0, value["budget"].get(key, maximum) - value["usage"].get(key, 0)) for value in runs
			if value["id"] != (run or {}).get("id") and value["status"] in {RunStatus.PENDING, RunStatus.RUNNING})
		reserved += sum(max(0, assessments[value["id"]]["budget"].get(key, maximum) - value["usage"].get(key, 0)) for value in runs
			if value["id"] != (run or {}).get("id") and assessments[value["id"]])
		available = max(0, maximum - spent - reserved)
		needed = max(0, requested.get(key, 0) - usage.get(key, 0))
		if emptyAggregate and key != "time":
			needed = 0
		if not assessment and (needed > available or available <= 0 and (key == "time" or not emptyAggregate)):
			raise AutoError(f"Plan {key} budget cannot cover the run allocation", "budget")
		cap = usage.get(key, 0) + available
		result[key] = min(result.get(key, cap), cap)
	return result
