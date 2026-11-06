"""Module: operations — Parse, validate and atomically apply task elaborations."""

from __future__ import annotations

import re

from auto.features.elaboration.materialize import materialize
from auto.features.plan.operations import PlanGet, PlanRevise, dependencyGraph, tasksAt
from auto.features.plan.store import journal, locked, planPath, recover
from auto.features.task.model import parseTask
from auto.features.task.operations import TaskGet, TaskStart, TaskValidate, effectiveTask, RunList
from auto.runtime.model import RunStatus, budgetValue
from auto.runtime.operations import Resolve
from auto.utils.data import AutoError, atomicWrite, document, documentText, identifier, identity, jsonText, sections, yamlLoad
from auto.utils.references import relocateMeta


def ElaborationParse(context, text: str) -> dict:
	"""Parse the Markdown/YAML interchange format, excluding fenced example headings."""
	meta, body = document(text)
	_, parts = sections(body)
	result = {**meta, "rationale": parts.pop("Rationale", ""), "tasks": [], "gaps": [], "removals": []}
	if not result.get("task"):
		raise AutoError("Elaboration requires its target task")
	for heading, content in parts.items():
		kind, separator, name = heading.partition(": ")
		if not separator or kind not in {"task", "gap", "remove"}:
			raise AutoError(f"Invalid elaboration section: {heading}")
		if kind == "remove":
			result["removals"].append({"id": name, "reason": content})
			continue
		match = re.match(r"^```ya?ml\s*\n(.*?)\n```\s*\n?(.*)$", content, re.S)
		if not match:
			raise AutoError(f"{heading} requires a YAML block")
		fields = yamlLoad(match[1]) or {}
		if not isinstance(fields, dict):
			raise AutoError(f"{heading} fields must be a mapping")
		if kind == "task":
			description, taskSections = sections(match[2], 3)
			result["tasks"].append({**fields, "id": name, "description": description.strip(), "sections": taskSections})
		else:
			result["gaps"].append({**fields, "name": name, "question": match[2].strip()})
	return result


def proposed(context, elaboration):
	path = planPath(context)
	tasks = tasksAt(path)
	target = elaboration["task"]
	if target not in tasks:
		raise AutoError(f"Unknown elaboration target: {target}")
	changes, new = [], []
	removals = [item["id"] for item in elaboration.get("removals", [])]
	removals += [item["replaces"] for item in elaboration.get("tasks", []) if item.get("replaces")]
	def remove(id):
		for child in list(tasks):
			if child != "root" and tasks[child].meta.get("parent", "root") == id:
				remove(child)
		tasks.pop(id)
	for id in removals:
		if id == "root" or id not in tasks or tasks[id].meta.get("parent", "root") != target:
			raise AutoError(f"Removal/replacement must name a child of {target}: {id}")
		remove(id)
	for item in elaboration.get("removals", []):
		changes.append({"op": "remove", "task": item["id"]})
	for value in elaboration.get("tasks", []):
		id = identifier(value["id"])
		if id == "root" or id in tasks or (path / "tasks" / (id + ".md")).exists() or (path / ".history" / (id + ".md")).exists():
			raise AutoError(f"Task id is not new: {id}")
		task = materialize(context, value, target)
		tasks[id] = task
		new.append(task)
		changes.append({"op": "replace" if value.get("replaces") else "add", "task": task.asDict(), **({"old": value["replaces"]} if value.get("replaces") else {})})
	# Allocate only unspecified dimensions, with explicit allocations deducted first.
	planBudget = budgetValue(tasks["root"].meta.get("budget"))
	parentBudget = budgetValue(tasks[target].meta.get("budget"))
	runs = RunList(context)
	for key, maximum in planBudget.items():
		remaining = min(maximum - sum(run["usage"].get(key, 0) for run in runs), parentBudget.get(key, maximum))
		retained = [task for task in tasks.values() if task.id != "root" and task.meta.get("parent", "root") == target and task not in new]
		remaining -= sum(max(0, budgetValue(task.meta.get("budget")).get(key, 0) - sum(run["usage"].get(key, 0) for run in runs if run["task"] == task.id)) for task in retained)
		explicit = sum(budgetValue(task.meta.get("budget")).get(key, 0) for task in new)
		unallocated = [task for task in new if key not in task.meta.get("budget", {})]
		for task in unallocated:
			share = max(0, remaining - explicit) / len(unallocated)
			task.meta.setdefault("budget", {})[key] = int(share) if key == "tokens" else share
			for change in changes:
				if isinstance(change.get("task"), dict) and change["task"]["id"] == task.id:
					change["task"] = task.asDict()
	return tasks, new, changes


def ElaborationValidate(context, plan, elaboration) -> list:
	"""Return all available violations without modifying plan or knowledge storage."""
	context = context.derive(plan=planPath(context, plan), evidence=[])
	violations = []
	try:
		if isinstance(elaboration, str):
			elaboration = ElaborationParse(context, elaboration)
		tasks, new, _ = proposed(context, elaboration)
		for task in new:
			violations.extend(TaskValidate(context, task, graph=False))
			if not task.meta.get("title"):
				violations.append({"element": "task:" + task.id, "message": "Task requires a title"})
		graph = dependencyGraph(context, tasks)
		names = set()
		for gap in elaboration.get("gaps", []):
			if gap.get("name") in names:
				raise AutoError("Duplicate local gap name")
			names.add(gap.get("name"))
			if gap.get("kind") not in {"input", "information", "decision", "approval"} or not gap.get("question") or not gap.get("needed_by") or any(id not in tasks for id in gap["needed_by"]):
				raise AutoError(f"Invalid gap: {gap.get('name')}")
			if gap["kind"] == "approval" and not gap.get("subjects"):
				raise AutoError("Approval gaps require existing pinned subjects; use hints.gate for future outputs")
		meta = PlanGet(context).get("budget", {})
		limits = budgetValue(meta)
		usage = {key: sum(run["usage"].get(key, 0) for run in RunList(context)) for key in limits}
		parent = tasks[elaboration["task"]]
		parentBudget = budgetValue(parent.meta.get("budget"))
		for key, maximum in limits.items():
			available = min(maximum - usage[key], parentBudget.get(key, maximum))
			allocated = sum(budgetValue(effectiveTask(context, task).meta.get("budget")).get(key, 0) for task in new)
			retained = [task for task in tasks.values() if task.id != "root" and task.meta.get("parent", "root") == elaboration["task"] and task not in new]
			allocated += sum(max(0, budgetValue(task.meta.get("budget")).get(key, 0) - sum(run["usage"].get(key, 0) for run in RunList(context) if run["task"] == task.id)) for task in retained)
			if allocated > available:
				raise AutoError(f"Elaborated {key} budgets exceed remaining budget")
		for ref in elaboration.get("context", []):
			Resolve(context, ref["ref"] if isinstance(ref, dict) else ref)
		if elaboration.get("methodology"):
			content = Resolve(context, elaboration["methodology"])
			method, _ = document(content.text)
			phases = method.get("phases", [])
			order = {phase["id"]: index for index, phase in enumerate(phases)}
			for phase in phases:
				members = [task for task in tasks.values() if task.meta.get("phase") == phase["id"]]
				if not members:
					raise AutoError(f"Missing methodology phase: {phase['id']}")
				for artifact in phase.get("artifacts", []):
					if not any(task.meta.get("artifact") == artifact for task in members):
						raise AutoError(f"Missing phase artifact: {artifact}")
				if phase.get("gate") == "approval" and not all(task.meta.get("hints", {}).get("gate") == "approval" for task in members):
					raise AutoError(f"Missing phase approval gate: {phase['id']}")
			for id, deps in graph.items():
				phase = tasks[id].meta.get("phase")
				if phase:
					if phase not in order:
						raise AutoError(f"Unknown methodology phase: {phase}")
					if any(order.get(tasks[dep].meta.get("phase"), -1) > order[phase] for dep in deps):
						raise AutoError(f"Task {id} depends on a later phase")
	except (AutoError, ValueError, KeyError, TypeError) as error:
		violations.append({"element": "elaboration", "message": str(error)})
	return violations


def ElaborationApply(context, plan, elaboration, run) -> dict:
	path = planPath(context, plan)
	with locked(path):
		recover(context, path)
		runId = run["id"] if isinstance(run, dict) else run
		for revision in journal(path):
			if revision.get("elaborationRun") == runId:
				return revision
		return _apply(context, path, elaboration, run)


def _apply(context, plan, elaboration, run) -> dict:
	context = context.derive(plan=planPath(context, plan), evidence=[], author=run["id"] if isinstance(run, dict) else run)
	if isinstance(elaboration, str):
		elaboration = ElaborationParse(context, elaboration)
	baseRevision = (journal(context.plan) or [{}])[-1].get("id")
	violations = ElaborationValidate(context, context.plan, elaboration)
	if violations:
		raise AutoError(jsonText(violations), "output")
	_, _, changes = proposed(context, elaboration)
	from auto.features.plan.gaps import prepareGap
	revision = identity()
	records, gapOps = [], []
	for gap in elaboration.get("gaps", []):
		value = prepareGap(context, context.plan, {key: value for key, value in gap.items() if key != "name"}, revision)
		records.append({"kind": "gap", "value": value})
		gapOps.append({"op": "raise", "gap": value["id"]})
	pins = []
	for ref in elaboration.get("context", []):
		content = Resolve(context, ref["ref"] if isinstance(ref, dict) else ref)
		pins.append({"ref": content.ref, "digest": content.digest})
	return PlanRevise(context, context.plan, changes, elaboration.get("rationale", elaboration.get("summary", "Elaboration")),
		revision=revision, records=records, extraOps=gapOps, provenance=pins, learnings=elaboration.get("learnings", []), expectedRevision=baseRevision, elaborationRun=context.author)


def ElaborationStart(context, plan, task: str, reason: str | None = None) -> dict:
	context = context.derive(plan=planPath(context, plan))
	planData = PlanGet(context)
	definition = TaskGet(context, task)
	settings = planData.get("elaborator", {})
	content = Resolve(context, settings.get("task", "auto:catalogue/task/elaborate"))
	elaborator = parseTask(content.text, content.path)
	elaborator.id = "elaborator"
	elaborator.meta["id"] = "elaborator"
	if "budget" in settings:
		elaborator.meta["budget"] = settings["budget"]
	remaining = {key: max(0, amount - planData["usage"].get(key, 0)) for key, amount in budgetValue(planData.get("budget")).items()}
	for key, amount in budgetValue(definition.meta.get("budget")).items():
		remaining[key] = min(remaining.get(key, amount), amount)
	from auto.features.plan.operations import PlanStatus
	from auto.features.plan.references import latestRun
	state = {"task": task, "plan": planData, "status": PlanStatus(context), "reason": reason,
		"outputs": {id: "task:" + id for id in planData["tasks"] if latestRun(context.plan, id, True)}}
	inputs = {"goal": jsonText({"goal": definition.meta.get("goal"), "output": definition.meta.get("output"), "artifact": definition.meta.get("artifact"), "post": definition.sections.get("Post"), "evaluation": definition.sections.get("Evaluation")}),
		"state": jsonText(state), "budget": jsonText(remaining) if remaining else ""}
	if not elaborator.meta.get("inputs", {}).get("context"):
		inputs["context"] = list(planData.get("inputs", {}).values())
	if not elaborator.meta.get("inputs", {}).get("learnings"):
		inputs["learnings"] = planData.get("learnings", [])
	return TaskStart(context.derive(variables={**context.variables, "target": task}), elaborator, inputs, metaTask=True)

def PlanCreate(context, goal: str, inputs: dict | None = None, budget: dict | None = None) -> dict:
	"""Create a root and run the ordinary Elaborator to produce its first revision."""
	name = re.sub(r"[^a-z0-9]+", "-", goal.lower()).strip("-")[:48].rstrip("-") or "plan"
	if name == "catalogue":
		name = "plan-catalogue"
	path = context.project / ".auto/plans" / name
	if path.exists():
		name += "-" + identity().rsplit("-", 1)[-1]
		path = path.with_name(name)
	path.mkdir(parents=True, exist_ok=False)
	atomicWrite(path / "plan.md", documentText({"id": name, "goal": goal, **relocateMeta({"inputs": inputs or {}}, context.project), "budget": budgetValue(budget)}, "# " + goal + "\n"))
	return PlanRefine(context.derive(plan=path), path, "Initial elaboration")


def PlanRefine(context, plan, reason: str, task: str = "root") -> dict:
	"""Elaborate a task and apply the result as one revision."""
	path = planPath(context, plan)
	context = context.derive(plan=path)
	run = ElaborationStart(context.derive(variables={**context.variables, "applyElaboration": True}), path, task, reason)
	if run["status"] != RunStatus.SUCCEEDED:
		return {"plan": str(path), "run": run, "applied": False}
	from auto.features.task.operations import RunOutput
	elaboration = ElaborationParse(context, RunOutput(context, run["id"]))
	revision = ElaborationApply(context, path, elaboration, run)
	return {"plan": str(path), "run": run, "revision": revision, "applied": True}


# EOF
