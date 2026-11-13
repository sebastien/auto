"""Allocate resolved task budgets and recheck current plan capacity."""

from auto.features.task.operations import RunList, effectiveTask
from auto.runtime.model import budgetValue
from auto.utils.data import AutoError


class BudgetAllocation:
	def __init__(self, context, tasks, new, target):
		self.context, self.new = context, new
		ids = {task.id for task in new}
		self.retained = [task for id, task in tasks.items()
			if id != "root" and id not in ids and task.meta.get("parent", "root") == target]
		readonly = context.derive(allowShell=False, evidence=[], variables=dict(context.variables))
		scope = {task.id: task for task in [*new, *self.retained]}
		if target != "root":
			scope[target] = tasks[target]
		self.budgets = {}
		for id, task in scope.items():
			try:
				self.budgets[id] = budgetValue(effectiveTask(readonly, task).meta.get("budget"))
			except AutoError as error:
				if id not in ids:
					raise
				raise AutoError(str(error), "output") from error
		self.plan = budgetValue(tasks["root"].meta.get("budget"))
		# The root profile is a default for individual allocations, not a plan cap.
		self.parent = self.plan if target == "root" else self.budgets[target]
		self.dimensions = sorted(self.plan.keys() | self.parent.keys())

	def remaining(self):
		"""Read current usage; callers may hold the plan writer lock for commit."""
		total, spent = {}, {}
		for run in RunList(self.context):
			usage = spent.setdefault(run["task"], {})
			for key in self.dimensions:
				amount = run["usage"].get(key, 0)
				total[key] = total.get(key, 0) + amount
				usage[key] = usage.get(key, 0) + amount
		result = {}
		for key in self.dimensions:
			available = min(self.plan.get(key, float("inf")) - total.get(key, 0), self.parent.get(key, float("inf")))
			reserved = sum(max(0, self.budgets[task.id].get(key, 0) - spent.get(task.id, {}).get(key, 0)) for task in self.retained)
			result[key] = available - reserved
		return result

	def validate(self):
		for key, available in self.remaining().items():
			if sum(self.budgets[task.id].get(key, 0) for task in self.new) > available:
				raise AutoError(f"Elaborated {key} budgets exceed remaining budget", "output")

	def allocate(self):
		"""Resolve fixed dimensions first, then divide the unallocated remainder."""
		for key, available in self.remaining().items():
			fixed = sum(self.budgets[task.id].get(key, 0) for task in self.new)
			missing = [task for task in self.new if key not in self.budgets[task.id]]
			for task in missing:
				share = max(0, available - fixed) / len(missing)
				self.budgets[task.id][key] = int(share) if key == "tokens" else share
		self.validate()
		for task in self.new:
			if self.budgets[task.id] or "budget" in task.meta:
				task.meta["budget"] = dict(self.budgets[task.id])
