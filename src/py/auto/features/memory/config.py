"""Module: config — Validate Auto agents and housekeeping configuration."""

from __future__ import annotations

from auto.features.plan.policy import RANK, validatePolicy
from auto.runtime.model import budgetValue
from auto.utils.data import AutoError, identifier

# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------


def validateAllowance(value) -> dict:
	"""Validate a time-windowed housekeeping budget."""
	if not isinstance(value, dict) or value.get("per") not in {"day", "week", "month"}:
		raise AutoError("Allowance requires per = day, week or month")
	budget = budgetValue({key: amount for key, amount in value.items() if key != "per"})
	if not budget or len(budget) + 1 != len(value):
		raise AutoError("Allowance requires budget dimensions and per")
	return {**budget, "per": value["per"]}


def agentsFor(context) -> dict:
	"""Return configured Auto agents keyed by their stable local IDs."""
	memory = context.defaults.get("memory", {})
	if not isinstance(memory, dict) or not isinstance(memory.get("agents", {}), dict):
		raise AutoError("memory.agents must be a mapping")
	agents, seen = memory.get("agents", {}), set()
	for name, config in agents.items():
		identifier(name)
		if not isinstance(config, dict) or not isinstance(config.get("agent"), str) or not config["agent"]:
			raise AutoError(f"Agent {name} requires a Memo artifact reference")
		if config["agent"].startswith("memo:"):
			raise AutoError(f"Agent {name} must use a Memo entry ID without the memo: prefix")
		if config["agent"] in seen:
			raise AutoError(f"Memo agent configured more than once: {config['agent']}")
		seen.add(config["agent"])
		budgetValue(config.get("budget"))
		if "allowance" in config:
			validateAllowance(config["allowance"])
		if "policy" in config:
			validatePolicy(config["policy"])
	return agents


def validateAgents(context, agents) -> list:
	"""Validate the Auto agent references assigned to a plan."""
	if not isinstance(agents, list) or any(not isinstance(ref, str) for ref in agents) or len(set(agents)) != len(agents):
		raise AutoError("Plan agents must be a list of distinct Auto agent references")
	available = agentsFor(context)
	for ref in agents:
		if not ref.startswith("auto:agent/") or ref.removeprefix("auto:agent/") not in available:
			raise AutoError(f"Unknown Auto agent: {ref}")
	return agents


def housekeepingFor(context) -> dict:
	"""Return the project housekeeping allowance, if configured."""
	settings = context.defaults.get("housekeeping", {})
	if not isinstance(settings, dict):
		raise AutoError("housekeeping must be a mapping")
	return validateAllowance(settings["allowance"]) if "allowance" in settings else {}


def memoryPolicy(context, agent: str) -> dict:
	"""Resolve memory effects without letting an agent loosen user policy."""
	agents = agentsFor(context)
	if agent not in agents:
		raise AutoError(f"Unknown Auto agent: {agent}")
	layers = [*(context.policyLayers or ([("configuration", context.defaults["policy"])] if "policy" in context.defaults else [])),
		("agent", agents[agent].get("policy", {}))]
	defaults = {"memory.observe": "allow", "memory.consolidate": "allow",
		"memory.promote": "confirm", "memory.evolve": "confirm"}
	for _, rules in layers:
		validatePolicy(rules)
	result = {}
	for effect, default in defaults.items():
		rule = None
		for _, rules in layers:
			matches = [key for key in rules if effect == key or effect.startswith(key + ".")]
			if matches:
				candidate = rules[max(matches, key=len)]
				# Defaults are recommendations; only explicit rules constrain later layers.
				rule = candidate if rule is None else max((rule, candidate), key=RANK.get)
		result[effect] = rule or default
	return result

# EOF
