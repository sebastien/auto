"""Module: policy — Resolve layered plan rules and journal human grants."""

from __future__ import annotations

import re

from auto.features.plan.model import GapKind, GapState, RevisionOp, createPlan
from auto.features.plan.store import commit, journal, locked, planPath, recover
from auto.features.plan.references import definitionPath
from auto.features.task.model import Task
from auto.runtime.config import RUNTIME_DEFAULTS
from auto.runtime.operations import Engine, Knowledge, Resolve
from auto.utils.data import AutoError, digest
from auto.utils.layout import planFile, taskFile, taskId, taskRef

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------


EFFECT = re.compile(r"^[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)*$")
RANK = {"allow": 0, "confirm": 1, "deny": 2}
BUILTINS = ("repo.read", "repo.write", "shell.run", "vcs.commit", "vcs.push", "net.read", "net.write", "deploy.staging", "deploy.production", "data.delete")

# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------


def validatePolicy(policy):
	"""Validate a `{effect: rule}` policy mapping."""
	if not isinstance(policy, dict):
		raise AutoError("Policy must be a mapping")
	for effect, rule in policy.items():
		if not isinstance(effect, str) or not EFFECT.fullmatch(effect) or not isinstance(rule, str) or rule not in RANK:
			raise AutoError(f"Invalid policy rule: {effect} = {rule}")


def ruleFor(effect, layers):
	"""Resolve each layer's most specific match without loosening earlier layers."""
	rule, source, restricted = ("allow" if effect in {"repo.read", "shell.run"} else "confirm"), "built-in", False
	for name, rules in layers:
		matches = [key for key in rules if effect == key or effect.startswith(key + ".")]
		if matches:
			candidate = rules[max(matches, key=len)]
			if not restricted or RANK[candidate] > RANK[rule]:
				rule, source = candidate, name
			restricted = True
	return {"rule": rule, "source": source}


def policyLayers(context, meta):
	"""Resolve policy layers from configuration and the plan."""
	layers = list(context.policyLayers)
	if not layers and "policy" in context.defaults:
		layers.append(("configuration", context.defaults["policy"]))
	layers.append(("plan", meta.get("policy", {})))
	for _, policy in layers:
		validatePolicy(policy)
	return layers


def definitionDigest(path, source: str) -> str | None:
	"""Return the digest of the task definition a grant source names, or None when it names none or is gone."""
	base = "auto:" + createPlan(path).id + "/task/"
	if not source.startswith(base) or not source.endswith("/definition"):
		return None
	task = source.removeprefix(base).removesuffix("/definition")
	file = planFile(path) if task == "root" else taskFile(path, task)
	try:
		return digest(file.read_bytes())
	except OSError:
		return None


def journalGrants(path):
	"""Read human grants; a grant whose source is a task definition (eg. the goal) expires if that definition changes."""
	result = []
	for row in journal(path):
		if row.get("author") != "human":
			continue
		for op in row["operations"]:
			if op.get("op") != RevisionOp.GRANT:
				continue
			if "source_digest" in op and definitionDigest(path, op.get("source", "")) != op["source_digest"]:
				continue
			result.append((row, op))
	return result


def planApproval(path) -> bool:
	"""Tell whether confirming the plan approves its tasks' `confirm` effects (a guided plan)."""
	return createPlan(path).meta.get("approval") == "plan"


def PlanApprove(context, plan=None) -> list:
	"""Grant each task its declared `confirm` effects, pinned to its definition: a person confirmed or ran the plan.

	Only plans created for plan approval (`approval: plan`) take it; `deny` effects are never granted."""
	path = planPath(context, plan)
	if not planApproval(path) or context.author != "human":
		return []
	from auto.features.plan.operations import tasksAt
	base = "auto:" + createPlan(path).id
	with locked(path):
		recover(context, path)
		operations = []
		for id, task in tasksAt(path).items():
			confirmed = [effect for effect, rule in taskRestrictions(context.derive(plan=path), task).items() if rule == "confirm"]
			if confirmed:
				source = f"{base}/task/{id}/definition"
				operations += [{"op": RevisionOp.GRANT, "effect": effect, "scope": f"{base}/task/{id}", "source": source,
					"source_digest": definitionDigest(path, source)} for effect in confirmed]
		if operations:
			commit(context, path, operations, reason="Plan confirmed")
	return operations


def PolicyResolve(context, plan=None, task=None):
	"""Return rules, applicable grants and effects not enforced by an integration."""
	path = planPath(context, plan)
	plan = createPlan(path)
	meta = plan.meta
	layers = policyLayers(context, meta)
	from auto.features.plan.operations import tasksAt
	tasks = tasksAt(path)
	if isinstance(task, Task):
		definition, task = task, task.id
		tasks[task] = definition
	task = None if task is None else taskId(task)
	if task is not None and task not in tasks:
		raise AutoError(f"Unknown task: {task}")
	effects = set(BUILTINS)
	effects.update(key for _, policy in layers for key in policy)
	for definition in ([tasks[task]] if task else tasks.values()):
		declared = definition.meta.get("effects", [])
		if not isinstance(declared, list) or any(not isinstance(e, str) or not EFFECT.fullmatch(e) for e in declared):
			raise AutoError(f"Invalid effects for task: {definition.id}")
		effects.update(declared)
	base = "auto:" + plan.id
	grants = [{**op, "author": row["author"], "revision": row["id"]} for row, op in journalGrants(path)
		if task is None or op.get("scope") in {base, base + "/task/" + task}]
	# An integration must explicitly claim each effect; no claim means no coverage.
	definitions = [tasks[task]] if task else list(tasks.values())
	enforced = set(effects)
	for definition in definitions:
		name = context.config.get("engine", definition.meta.get("engine", context.defaults.get("engine", RUNTIME_DEFAULTS["engine"])))
		try:
			declared = getattr(Engine(context, name), "enforceableEffects", ())
			enforced.intersection_update(declared if isinstance(declared, (set, frozenset, list, tuple)) else ())
		except AutoError:
			enforced.clear()
	return {"rules": {effect: ruleFor(effect, layers) for effect in sorted(effects)}, "default": "confirm",
		"grants": grants, "unenforceable": sorted(effects - enforced), **({"approval": "plan"} if meta.get("approval") == "plan" else {})}


def taskRestrictions(context, task):
	"""Return ungranted effects and their effective rules for a task definition."""
	path = planPath(context)
	plan = createPlan(path)
	meta = plan.meta
	layers = policyLayers(context, meta)
	effects = task.meta.get("effects", [])
	if not isinstance(effects, list) or any(not isinstance(e, str) or not EFFECT.fullmatch(e) for e in effects):
		raise AutoError(f"Invalid effects for task: {task.id}")
	base = "auto:" + plan.id
	granted = {op.get("effect") for _, op in journalGrants(path) if op.get("scope") in {base, base + "/task/" + task.id}}
	return {effect: ruleFor(effect, layers)["rule"] for effect in effects if effect not in granted and ruleFor(effect, layers)["rule"] != "allow"}


def taskPolicyBlocked(context, task):
	"""Require a human approval of this exact definition for confirmed effects."""
	from auto.features.plan.gaps import GapList
	restrictions = taskRestrictions(context, task)
	if not restrictions:
		return {}
	if "confirm" not in restrictions.values():
		return restrictions
	base = "auto:" + createPlan(planPath(context)).id
	ref = f"{base}/task/{task.id}/definition"
	current = Resolve(context, ref).digest
	for gap in GapList(context, filter={"status": GapState.RESOLVED, "task": task.id}):
		if gap.get("kind") != GapKind.APPROVAL or {"ref": ref, "digest": current} not in gap.get("subjects", []):
			continue
		decision = Knowledge(context).get(f"{base}/decision/{gap['decision']}")
		answer = decision.get("answer")
		if decision.get("author") == "human" and (answer is True or isinstance(answer, str) and answer.lower() in {"approve", "approved", "yes"}):
			question = gap.get("question", "")
			covered = {effect for effect in gap.get("effects", []) if effect in question and "confirm" in question.lower()}
			restrictions = {effect: rule for effect, rule in restrictions.items() if rule != "confirm" or effect not in covered}
	return restrictions


def shellPermitted(context, task):
	"""Map shell.run to Auto's shell boundary; engines may still use their own tools."""
	path = planPath(context)
	plan = createPlan(path)
	if ruleFor("shell.run", policyLayers(context, plan.meta))["rule"] == "allow":
		return True
	base = "auto:" + plan.id
	if any(op.get("effect") == "shell.run" and op.get("scope") in {base, base + "/task/" + task.id} for _, op in journalGrants(path)):
		return True
	return definitionPath(path, task.id).is_file() and "shell.run" in task.meta.get("effects", []) and "shell.run" not in taskPolicyBlocked(context, task)


def runtimeRule(context, task, effect):
	"""Return the effective rule and whether a runtime action is already covered."""
	if not isinstance(effect, str) or not EFFECT.fullmatch(effect):
		raise AutoError("Invalid runtime policy effect")
	path = planPath(context)
	plan = createPlan(path)
	rule = ruleFor(effect, policyLayers(context, plan.meta))["rule"]
	base = "auto:" + plan.id
	granted = any(op.get("effect") == effect and op.get("scope") in {base, base + "/task/" + task.id} for _, op in journalGrants(path))
	covered = rule == "allow" or granted or effect in task.meta.get("effects", []) and effect not in taskPolicyBlocked(context, task)
	return rule, covered


def grantAnswer(answer):
	"""Read a grant decision's answer with its scope canonical (see `taskRef`)."""
	return {**answer, "scope": taskRef(answer["scope"])} if isinstance(answer, dict) and isinstance(answer.get("scope"), str) else answer


def PolicyGrant(context, plan, effect, scope, decision):
	"""Record a grant backed by a human-authored decision in this plan."""
	path = planPath(context, plan)
	if not isinstance(effect, str) or not EFFECT.fullmatch(effect):
		raise AutoError("Invalid effect")
	base = "auto:" + createPlan(path).id
	scope = taskRef(scope)
	from auto.features.plan.operations import tasksAt
	validScopes = {base} | {base + "/task/" + id for id in tasksAt(path)}
	if scope not in validScopes:
		raise AutoError("Invalid grant scope")
	ref = decision if isinstance(decision, str) and decision.startswith(base + "/decision/") else base + "/decision/" + str(decision)
	with locked(path):
		recover(context, path)
		value = Knowledge(context).get(ref)
		def resolved(row):
			return any(op.get("op") == RevisionOp.RESOLVE and op.get("decision") == value.get("id") for op in row["operations"])
		committed = any(row["id"] == value.get("revision") and row.get("author") == "human" and resolved(row) for row in journal(path))
		if not committed or value.get("identity") != ref or value.get("plan") != base \
			or value.get("author") != "human" or grantAnswer(value.get("answer")) != {"grant": effect, "scope": scope}:
			raise AutoError("Grant requires a human-authored decision in this plan")
		return commit(context.derive(author=value["author"]), path,
			[{"op": RevisionOp.GRANT, "effect": effect, "scope": scope, "source": ref}], reason="Policy grant")


# EOF
