"""Module: operations — Parse, validate and atomically apply task elaborations."""

from __future__ import annotations

import re

from liquid.exceptions import LiquidError

from auto.features.elaboration.materialize import materialize
from auto.features.elaboration.allocation import BudgetAllocation
from auto.features.elaboration.methodology import validateMethodology
from auto.features.elaboration.model import TaskElaboration, createElaborationFromDict
from auto.features.memory.config import defaultAgents, validateAgents
from auto.features.plan.gaps import prepareGap
from auto.features.plan.model import GAP_KINDS, GapKind, RevisionOp, createPlan
from auto.features.plan.operations import PlanGet, PlanRevise, PlanStatus, dependencyGraph, tasksAt
from auto.features.plan.policy import EFFECT, PolicyResolve, taskRestrictions
from auto.features.plan.references import latestRun
from auto.features.plan.store import commit, journal, locked, planPath, recover
from auto.features.task.model import createTaskFromText, taskText
from auto.features.task.operations import RunOutput, TaskGet, TaskStart, TaskValidate, effectiveTask
from auto.features.script.config import commands
from auto.runtime.model import RunStatus, budgetValue
from auto.runtime.operations import Resolve, isReference
from auto.utils.data import AutoError, atomicWrite, digest, document, documentText, identifier, identity, jsonText, sections, yamlLoad
from auto.utils.references import relocateMeta


# -----------------------------------------------------------------------------
#
# UTILITIES
#
# -----------------------------------------------------------------------------


def proposed(context, elaboration: TaskElaboration, *, dry: bool = False, deferred=None):
	"""Validate an elaboration's targets and compute its revision changes."""
	path = planPath(context)
	tasks = tasksAt(path)
	target = elaboration.task
	if target not in tasks:
		raise AutoError(f"Unknown elaboration target: {target}")
	changes, new = [], []
	removals = [item["id"] for item in elaboration.removals]
	removals += [item["replaces"] for item in elaboration.tasks if item.get("replaces")]
	def remove(id):
		for child in list(tasks):
			if child != "root" and tasks[child].meta.get("parent", "root") == id:
				remove(child)
		tasks.pop(id)
	for id in removals:
		if id == "root" or id not in tasks or tasks[id].meta.get("parent", "root") != target:
			raise AutoError(f"Removal/replacement must name a child of {target}: {id}")
		remove(id)
	for item in elaboration.removals:
		changes.append({"op": RevisionOp.REMOVE, "task": item["id"]})
	for value in elaboration.tasks:
		id = identifier(value["id"])
		if id == "root" or id in tasks or (path / "tasks" / (id + ".md")).exists() or (path / ".history" / (id + ".md")).exists():
			raise AutoError(f"Task id is not new: {id}")
		fields = set()
		task = materialize(context, value, target, dry=dry, deferred=fields if dry else None)
		if deferred is not None:
			deferred[id] = fields
		tasks[id] = task
		new.append(task)
		change = {"op": RevisionOp.REPLACE if value.get("replaces") else RevisionOp.ADD, "task": task.asDict()}
		if value.get("replaces"):
			change["old"] = value["replaces"]
		changes.append(change)
	BudgetAllocation(context, tasks, new, target).allocate()
	for change in changes:
		if isinstance(change.get("task"), dict):
			change["task"] = tasks[change["task"]["id"]].asDict()
	return tasks, new, changes


def policyGaps(context, elaboration: TaskElaboration, new):
	"""Pin references to proposed definitions, which are not on disk yet."""
	base = "auto:" + createPlan(context.plan).id
	staged = {f"{base}/task/{task.id}/definition": digest(taskText(task).encode()) for task in new}
	result = []
	for original in elaboration.gaps:
		gap = dict(original)
		subjects = []
		for subject in gap.get("subjects", []):
			ref = subject if isinstance(subject, str) else subject.get("ref", "")
			if ref.startswith("task:"):
				ref = base + "/task/" + ref[5:]
			if ref in staged:
				if isinstance(subject, dict) and subject.get("digest", staged[ref]) != staged[ref]:
					raise AutoError("Proposed approval subject digest does not match task definition")
				subject = {"ref": ref, "digest": staged[ref]}
			subjects.append(subject)
		if "subjects" in gap:
			gap["subjects"] = subjects
		result.append(gap)
	return result, staged


def validGap(gap, tasks) -> bool:
	"""Tell whether a proposed gap has a supported kind and resolvable targets."""
	return gap.get("kind") in GAP_KINDS and bool(gap.get("question")) \
		and bool(gap.get("needed_by")) and all(id in tasks for id in gap["needed_by"])


def policyGapCovers(gap, task, kind, effect, rule, ref, staged) -> bool:
	"""Tell whether a proposed gap covers an effect requiring confirmation."""
	return (gap.get("kind") == kind and task.id in gap.get("needed_by", []) and effect in gap.get("effects", [])
		and effect in gap.get("question", "") and rule in gap.get("question", "").lower()
		and (kind != GapKind.APPROVAL or {"ref": ref, "digest": staged[ref]} in gap.get("subjects", [])))


def asElaboration(context, value) -> TaskElaboration:
	"""Normalise a text, mapping or model elaboration to the model."""
	if isinstance(value, str):
		value = ElaborationParse(context, value)
	elif not isinstance(value, TaskElaboration):
		value = createElaborationFromDict(value)
	if value.methodology is not None and (not isinstance(value.methodology, str) or value.methodology and not isReference(value.methodology)):
		raise AutoError("Methodology must be a reference string")
	return value


def _apply(context, plan, elaboration: TaskElaboration, run) -> dict:
	"""Prepare outside the writer lock, then commit against the captured revision."""
	context = context.derive(plan=planPath(context, plan), evidence=[], author=run["id"] if isinstance(run, dict) else run)
	if context.cancelled():
		raise AutoError("Elaboration application cancelled", "cancelled")
	baseRevision = (journal(context.plan) or [{}])[-1].get("id")
	readonly = context.derive(allowShell=False, variables=dict(context.variables))
	methodSource = Resolve(readonly, elaboration.methodology) if elaboration.methodology else None
	violations = ElaborationValidate(context, context.plan, elaboration, methodSource=methodSource)
	if violations:
		raise AutoError(jsonText(violations), "output")
	if context.cancelled():
		raise AutoError("Elaboration application cancelled", "cancelled")
	prepared = proposed(context, elaboration)
	violations = ElaborationValidate(context, context.plan, elaboration, prepared=prepared, methodSource=methodSource)
	if violations:
		raise AutoError(jsonText(violations), "output")
	tasks, new, changes = prepared
	gaps, staged = policyGaps(context, elaboration, new)
	revision = identity()
	records, gapOps = [], []
	for gap in gaps:
		value = prepareGap(context, context.plan, {key: value for key, value in gap.items() if key != "name"}, revision, staged)
		records.append({"kind": "gap", "value": value})
		gapOps.append({"op": RevisionOp.RAISE, "gap": value["id"]})
	for ref in elaboration.context:
		Resolve(context, ref["ref"] if isinstance(ref, dict) else ref)
	if methodSource:
		current = Resolve(readonly, elaboration.methodology)
		if (current.ref, current.digest) != (methodSource.ref, methodSource.digest):
			raise AutoError("An input changed while applying the elaboration: " + elaboration.methodology, "output")
	allocation = BudgetAllocation(context, tasks, new, elaboration.task)
	pins = {}
	for pin in context.evidence:
		if pin["ref"] in pins and pins[pin["ref"]]["digest"] != pin["digest"]:
			raise AutoError("An input changed while applying the elaboration: " + pin["ref"], "output")
		pins[pin["ref"]] = pin
	with locked(context.plan):
		if context.cancelled():
			raise AutoError("Elaboration application cancelled", "cancelled")
		if (journal(context.plan) or [{}])[-1].get("id") != baseRevision:
			raise AutoError("Plan changed during elaboration validation; validate and retry")
		allocation.validate()
		return PlanRevise(context, context.plan, changes, elaboration.rationale or elaboration.summary or "Elaboration",
			revision=revision, records=records, extraOps=gapOps, provenance=list(pins.values()),
			learnings=elaboration.learnings, expectedRevision=baseRevision, elaborationRun=context.author)




# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------


def ElaborationStart(context, plan, task: str, reason: str | None = None) -> dict:
	"""Run the plan Elaborator on the goal of a task."""
	context = context.derive(plan=planPath(context, plan))
	planData = PlanGet(context)
	definition = TaskGet(context, task)
	settings = planData.get("elaborator", {})
	content = Resolve(context, settings.get("task", "auto:catalogue/task/elaborate"))
	elaborator = createTaskFromText(content.text, content.path)
	elaborator.id = "elaborator"
	elaborator.meta["id"] = "elaborator"
	if "budget" in settings:
		elaborator.meta["budget"] = settings["budget"]
	remaining = {key: max(0, amount - planData["usage"].get(key, 0)) for key, amount in budgetValue(planData.get("budget")).items()}
	parentBudget = definition.meta.get("budget")
	if task != "root":
		parentBudget = effectiveTask(context.derive(allowShell=False, variables=dict(context.variables)), definition).meta.get("budget")
	for key, amount in budgetValue(parentBudget).items():
		remaining[key] = min(remaining.get(key, amount), amount)
	elaboratorBudget = budgetValue(effectiveTask(context, elaborator).meta.get("budget"))
	for key, amount in remaining.items():
		elaboratorBudget[key] = min(elaboratorBudget.get(key, amount), amount)
	if elaboratorBudget:
		elaborator.meta["budget"] = elaboratorBudget
	state = {"task": task, "plan": planData, "status": PlanStatus(context), "reason": reason,
		"outputs": {id: "task:" + id for id in planData["tasks"] if latestRun(context.plan, id, True)}}
	goal = {"goal": definition.meta.get("goal"), "output": definition.meta.get("output"), "artifact": definition.meta.get("artifact"),
		"post": definition.sections.get("Post"), "evaluation": definition.sections.get("Evaluation")}
	for key, section, commandKey in (("post", "Post", "post"), ("evaluation", "Evaluation", "eval")):
		if definition.meta.get(commandKey):
			goal[key] = {"commands": commands(definition.meta[commandKey]), "content": definition.sections.get(section, "")}
	inputs = {"target": task, "goal": jsonText(goal), "state": jsonText(state), "budget": jsonText(remaining) if remaining else ""}
	inputs["policy"] = jsonText(PolicyResolve(context, context.plan, task))
	if not elaborator.meta.get("inputs", {}).get("context"):
		inputs["context"] = list(planData.get("inputs", {}).values())
	if not elaborator.meta.get("inputs", {}).get("learnings"):
		inputs["learnings"] = planData.get("learnings", [])
	return TaskStart(context.derive(variables={**context.variables, "target": task}), elaborator, inputs, metaTask=True)


def ElaborationParse(context, text: str) -> TaskElaboration:
	"""Parse the Markdown/YAML interchange format, excluding fenced example headings."""
	# Models sometimes wrap the whole reply in one code block, as the template is shown.
	wrapped = re.fullmatch(r"\s*(`{3,})[\w-]*[ \t]*\r?\n(.*?)\r?\n\1[ \t]*\s*", text, re.S)
	if wrapped:
		text = wrapped[2]
	# Models sometimes drop the newline after the opening fence (`---task: root`).
	text = re.sub(r"\A---(?=[A-Za-z_])", "---\n", text)
	meta, body = document(text)
	_, parts = sections(body)
	if not meta.get("task"):
		raise AutoError("Elaboration requires its target task")
	elaboration = createElaborationFromDict({**meta, "rationale": parts.pop("Rationale", "")})
	for heading, content in parts.items():
		kind, separator, name = heading.partition(": ")
		if not separator or kind not in {"task", "gap", "remove"}:
			raise AutoError(f"Invalid elaboration section: {heading}")
		if kind == "remove":
			elaboration.removals.append({"id": name, "reason": content})
			continue
		match = re.match(r"^```ya?ml\s*\n(.*?)\n```\s*\n?(.*)$", content, re.S)
		if not match:
			raise AutoError(f"{heading} requires a YAML block")
		fields = yamlLoad(match[1]) or {}
		if not isinstance(fields, dict):
			raise AutoError(f"{heading} fields must be a mapping")
		if kind == "task":
			description, taskSections = sections(match[2], 3)
			elaboration.tasks.append({**fields, "id": name, "description": description.strip(), "sections": taskSections})
		else:
			elaboration.gaps.append({**fields, "name": name, "question": match[2].strip()})
	return elaboration


def ElaborationValidate(context, plan, elaboration, task: str | None = None, *, prepared=None, methodSource=None) -> list:
	"""Return available violations without writes; apply rechecks resolved metadata.
	`task`, when given, is the task the elaboration must target."""
	context = context.derive(plan=planPath(context, plan), evidence=[])
	violations = []
	try:
		elaboration = asElaboration(context, elaboration)
		if task is not None and elaboration.task != task:
			raise AutoError(f"The elaboration must target {task} (frontmatter `task: {task}`), not {elaboration.task};"
				f" its children are new tasks")
		deferred = {}
		tasks, new, _ = prepared if prepared is not None else proposed(context, elaboration, dry=True, deferred=deferred)
		gaps, staged = policyGaps(context, elaboration, new)
		for task in new:
			violations.extend(TaskValidate(context, task, graph=False))
			if not task.meta.get("title"):
				violations.append({"element": "task:" + task.id, "message": "Task requires a title"})
		graph = dependencyGraph(context, tasks)
		names = set()
		for gap in gaps:
			if gap.get("name") in names:
				raise AutoError("Duplicate local gap name")
			names.add(gap.get("name"))
			if not validGap(gap, tasks):
				raise AutoError(f"Invalid gap: {gap.get('name')}")
			if gap["kind"] == GapKind.APPROVAL and not gap.get("subjects"):
				raise AutoError("Approval gaps require existing pinned subjects; use hints.gate for future outputs")
		for task in new:
			ref = next(ref for ref in staged if ref.endswith("/task/" + task.id + "/definition"))
			for effect, rule in taskRestrictions(context, task).items():
				kind = GapKind.APPROVAL if rule == "confirm" else GapKind.DECISION
				if not any(policyGapCovers(gap, task, kind, effect, rule, ref, staged) for gap in gaps):
					requirement = "checkpoint" if kind == GapKind.APPROVAL else "grant decision gap"
					violations.append({"element": "task:" + task.id, "message": f"Effect {effect} ({rule}) requires a {requirement}"})
		BudgetAllocation(context, tasks, new, elaboration.task).validate()
		for ref in elaboration.context:
			target = ref.get("ref") if isinstance(ref, dict) else ref
			if not isinstance(target, str):
				raise AutoError(f"Invalid context entry {jsonText(ref).strip()}: context lists PATH references, eg. ./docs/spec.md")
			Resolve(context, target)
		if elaboration.methodology:
			content = methodSource or Resolve(context.derive(allowShell=False, variables=dict(context.variables)), elaboration.methodology)
			validateMethodology(context, content, tasks, graph, deferred=deferred,
				overrides={value["id"]: value for value in elaboration.tasks})
	except (AutoError, ValueError, KeyError, TypeError, LiquidError) as error:
		violations.append({"element": "elaboration", "message": str(error)})
	# Models tend to wire gap answers as plan inputs: say how answers reach tasks.
	names = {gap.get("name") for gap in getattr(elaboration, "gaps", [])}
	for violation in violations:
		match = re.fullmatch(r"Unknown plan input: (.+)", violation["message"])
		if match and match[1] in names:
			violation["message"] += (f" ({match[1]} is a gap: its answer is given to the tasks in its needed_by"
				" when they run, so do not reference it in inputs)")
	return violations


def ElaborationApply(context, plan, elaboration, run) -> dict:
	"""Apply a validated elaboration as a single plan revision."""
	path = planPath(context, plan)
	elaboration = asElaboration(context, elaboration)
	runId = run["id"] if isinstance(run, dict) else run
	if not isinstance(runId, str) or not runId:
		raise AutoError("Elaboration application requires its originating run ID")
	application = path / ".applications" / digest(runId.encode()).removeprefix("sha256:")
	application.mkdir(parents=True, exist_ok=True)
	with locked(application):
		with locked(path):
			recover(context, path)
			for revision in journal(path):
				if revision.get("elaborationRun") == runId:
					return revision
		return _apply(context, path, elaboration, run)


PLAN_NAME_FILLER = frozenset("""a an the and or but of for to in on at by with from into onto as is are be it its them this that these those
	now please some new simple basic small very just also then using via create make build""".split())


def planName(goal: str, words: int = 4, size: int = 32) -> str:
	"""Derive a compact plan id from the significant words of the goal's first sentence."""
	sentence = re.split(r"(?<=[.!?])\s|\n", goal.strip(), maxsplit=1)[0]
	tokens = re.findall(r"[a-z0-9]+", sentence.lower())
	kept = [token for token in tokens if token not in PLAN_NAME_FILLER] or tokens
	name = ""
	for token in kept[:words]:
		if name and len(name) + 1 + len(token) > size:
			break
		name = f"{name}-{token}" if name else token[:size]
	return name or "plan"


def PlanCreate(context, goal: str, inputs: dict | None = None, budget: dict | None = None, effects: list | None = None, agents: list | None = None) -> dict:
	"""Create a root and run the ordinary Elaborator to produce its first revision."""
	if not isinstance(goal, str) or not goal.strip():
		raise AutoError("Plan creation requires a non-empty goal")
	inputs = context.inputs if inputs is None else inputs
	budget = budgetValue(budget)
	if effects is not None and (not isinstance(effects, list) or any(not isinstance(effect, str) or not EFFECT.fullmatch(effect) for effect in effects)):
		raise AutoError("Requested effects must be a list of dotted effect names")
	if effects and context.author != "human":
		raise AutoError("Only a human may request goal effects")
	agents = defaultAgents(context) if agents is None else list(validateAgents(context, agents))
	plans = context.project / ".auto/plans"
	plans.mkdir(parents=True, exist_ok=True)
	# The plans lock makes the counter safe across concurrent creations.
	with locked(plans):
		counter = plans / ".counter"
		number = int(counter.read_text().strip() or "0", 16) if counter.exists() else 0
		used = {path.name.partition("-")[0] for path in plans.iterdir()}
		while f"{number:03x}" in used:
			number += 1
		name = f"{number:03x}-{planName(goal)}"
		path = plans / name
		path.mkdir(exist_ok=False)
		atomicWrite(counter, f"{number + 1:03x}\n")
	root = {"id": name, "goal": goal, **({"requested_effects": effects} if effects else {}),
		**({"agents": agents} if agents is not None else {}),
		**relocateMeta({"inputs": inputs or {}}, context.project), "budget": budget}
	atomicWrite(path / "plan.md", documentText(root, "# " + goal + "\n"))
	if effects:
		base = "auto:" + name
		rootDigest = digest((path / "plan.md").read_bytes())
		operations = [{"op": RevisionOp.GRANT, "effect": effect, "scope": base, "source": base + "/task/root/definition", "source_digest": rootDigest}
			for effect in dict.fromkeys(effects)]
		with locked(path):
			commit(context, path, operations, reason="Human-requested goal effects")
	return PlanRefine(context.derive(plan=path), path, "Initial elaboration")


def PlanRefine(context, plan, reason: str, task: str = "root") -> dict:
	"""Elaborate a task and apply the result as one revision."""
	path = planPath(context, plan)
	context = context.derive(plan=path)
	run = ElaborationStart(context.derive(variables={**context.variables, "applyElaboration": True}), path, task, reason)
	if run["status"] != RunStatus.SUCCEEDED:
		return {"plan": str(path), "run": run, "applied": False}
	elaboration = ElaborationParse(context, RunOutput(context, run["id"]))
	revision = ElaborationApply(context, path, elaboration, run)
	return {"plan": str(path), "run": run, "revision": revision, "applied": True}

# EOF
