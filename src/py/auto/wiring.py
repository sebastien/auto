"""Module: wiring — Composition root for reference resolvers and feature gateways.

The runtime owns the scheme registry; features register their resolvers here so
the runtime never imports a feature module. This is the only place that knows
both sides of the reference boundary.
"""

from __future__ import annotations

from auto.runtime.model import Context

# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------


def validateContext(context: Context) -> None:
	"""Validate feature configuration at the composition boundary."""
	from auto.features.memory.config import defaultAgents, housekeepingFor, memoryPolicy
	from auto.features.memory.accounting import constraintsFor
	from auto.integration.resolvers import validateResolvers
	from auto.features.elaboration.catalogue import actionPatterns
	actionPatterns(context)
	defaultAgents(context)
	housekeepingFor(context)
	for name in context.defaults.get("memory", {}).get("agents", {}) or {"": {}}:
		constraintsFor(context, name)
		if name:
			memoryPolicy(context, name)
	validateResolvers(context.defaults.get("resolvers", {}))


def installResolvers(context: Context) -> None:
	"""Install schemes without replacing explicitly registered Python resolvers."""
	from auto.features.elaboration.catalogue import catalogueContent
	from auto.features.plan.references import planContent
	from auto.features.memory.references import agentContent
	from auto.features.script.runs import scriptRunContent
	from auto.integration.resolvers import configuredContent, validateResolvers
	for prefix, resolver in {
		"auto:catalogue/": catalogueContent,
		"auto:agent/": agentContent,
		"auto:runs/": scriptRunContent,
		"auto:": planContent,
		"plan:": planContent,
		"task:": planContent,
		"run:": planContent,
		"revision:": planContent,
		"gap:": planContent,
		"decision:": planContent,
		"memo:": lambda ctx, target: ctx.runtime.knowledgeFor().readReference(target.partition(":")[2]),
	}.items():
		context.resolvers.setdefault(prefix, resolver)
	for scheme in validateResolvers(context.defaults.get("resolvers", {})):
		context.resolvers.setdefault(scheme + ":", configuredContent)


def prepareRun(context: Context, run: dict, state: dict):
	"""Compose optional memory actuation without a task-to-memory dependency."""
	from auto.features.memory.operations import RunWake
	return RunWake(context, run, state)


def planRunBudget(context: Context, task, budget: dict, run=None, *, assessment=False):
	from auto.features.plan.budgets import planRunBudget as allocate
	return allocate(context, task, budget, run, assessment=assessment)


def prepareRunRequest(context: Context, run: dict, state: dict, request: dict):
	from auto.features.memory.operations import RunMemoryRequest
	return RunMemoryRequest(context, run, state, request)


def recordRunMetadata(run: dict, event: dict):
	if event.get("name") == "memory_cache":
		from auto.features.memory.operations import RunMemoryUsage
		RunMemoryUsage(run, event.get("value"))


def planEventRecorded(context: Context, path):
	from auto.features.memory.scheduler import diagnostic, queuePlan
	try:
		queuePlan(context, path)
	except Exception as error:
		diagnostic(context.project / ".auto/housekeeping", error)

# EOF
