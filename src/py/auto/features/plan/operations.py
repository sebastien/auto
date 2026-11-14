"""Module: operations — Plan graphs, revisioned mutations and derived status."""

from __future__ import annotations

from pathlib import Path

from auto.features.plan.gaps import GapList, repairLocked
from auto.features.plan.gates import taskGapState
from auto.features.plan.control import planHold
from auto.features.memory.config import validateAgents
from auto.features.plan.model import GapState, PlanState, RevisionOp, createPlan
from auto.features.plan.references import latestRun, planContent
from auto.features.plan.store import commit, journal, locked, planPath, recover
from auto.features.task.model import Task, createTaskFromDict, createTaskFromText, taskText
from auto.runtime.model import Content, IN_FLIGHT, RunStatus
from auto.runtime.operations import Resolve, Select
from auto.utils.data import AutoError, digest, document, documentText, identifier, jsonText, readJson


# -----------------------------------------------------------------------------
#
# UTILITIES
#
# -----------------------------------------------------------------------------


def tasksAt(path: Path) -> dict[str, Task]:
	"""Read the root and child tasks of a plan under its lock."""
	with locked(path):
		if (path / ".pending.json").exists():
			raise AutoError("Plan has an interrupted commit; run PlanRepair before reading its tasks", "system")
		return _tasksAt(path)


def _tasksAt(path: Path) -> dict[str, Task]:
	"""Read plan tasks without acquiring the plan lock."""
	root = createTaskFromText((path / "plan.md").read_text(), path / "plan.md", True)
	result = {"root": root}
	for file in sorted((path / "tasks").glob("*.md")):
		task = createTaskFromText(file.read_text(), file)
		if task.id in result:
			raise AutoError(f"Duplicate task id: {task.id}")
		result[task.id] = task
	return result


def dependencyGraph(context, tasks: dict, *, taskInputs: dict | None = None) -> dict[str, set[str]]:
	"""Validate definition dependencies, with optional inputs for selected runs."""
	from auto.features.task.operations import TaskReferences
	context = context.derive(inputs={})
	definitions = {}
	plan = createPlan(planPath(context)).id
	owners = sorted({"auto:": planContent, "task:": planContent, **context.resolvers}.items(), key=lambda item: len(item[0]), reverse=True)
	def definition(ref):
		prefix = f"auto:{plan}/task/" if ref.startswith(f"auto:{plan}/task/") else "task:"
		if not ref.startswith(prefix) or not ref.endswith("/definition"):
			return None
		id = ref[len(prefix):-len("/definition")]
		if id not in tasks or next(resolve for start, resolve in owners if ref.startswith(start)) is not planContent:
			return None
		if id in definitions:
			return definitions[id]
		task = tasks[id]
		text = taskText(task)
		if task.path.is_file():
			stored = task.path.read_bytes()
			if taskText(createTaskFromText(stored.decode("utf-8"), task.path, id == "root")) == text:
				# Unchanged definitions retain their exact bytes and registered owner.
				definitions[id] = None
				return None
		if id == "root":
			meta, body = document(text)
			text = documentText({**meta, "id": plan}, body)
		data = text.encode()
		content = Content(f"auto:{plan}/task/{id}/definition", data, digest(data), task.path)
		definitions[id] = content
		return content
	references = {id: TaskReferences(context.derive(inputs=taskInputs[id]) if taskInputs is not None and id in taskInputs else context,
		task, definitions=definition) for id, task in tasks.items()}
	graph = {id: set(value["dependencies"]) for id, value in references.items()}
	readonly = context.derive(allowShell=False, variables=dict(context.variables))
	for value in references.values():
		for ref in value["definitions"]:
			target, _, selector = ref.partition("#")
			content = definition(target)
			if content is not None:
				if len(content.data) > context.maxBytes:
					raise AutoError("Reference exceeds byte limit: " + ref)
				Select(content, selector, ref)
			else:
				Resolve(readonly, ref)
	for id, task in tasks.items():
		if id != "root":
			parent = task.meta.get("parent", "root")
			if parent not in tasks:
				raise AutoError(f"Missing parent: {parent}")
			graph[parent].add(id)
	visiting, visited = set(), set()
	def visit(id):
		if id in visiting:
			raise AutoError(f"Dependency cycle at task:{id}")
		if id in visited:
			return
		if id not in graph:
			raise AutoError(f"Missing dependency task:{id}")
		visiting.add(id)
		for dependency in graph[id]:
			visit(dependency)
		visiting.remove(id)
		visited.add(id)
	for id in graph:
		visit(id)
	return graph


# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------


def PlanGet(context, id=None) -> dict:
	"""Return a plan with its task facets and derived status."""
	path = planPath(context, id)
	with locked(path):
		meta = createPlan(path).meta
		tasks = tasksAt(path)
		ctx = context.derive(plan=path)
		status = PlanStatus(ctx, path)
		from auto.features.task.operations import RunList
		runs = RunList(ctx)
		facets = {}
		for id, task in tasks.items():
			facets[id] = {**task.asDict(), "state": status["tasks"][id], "attempts": sum(run["task"] == id for run in runs),
				"children": [child for child in tasks if child != "root" and tasks[child].meta.get("parent", "root") == id]}
		return {**meta, "root": "root", "path": str(path), "tasks": facets, "revision": status["revision"], "hold": status["hold"], "continuation": status["continuation"],
			"status": status["status"], "usage": status["usage"], "gaps": status["gaps"]}


def PlanList(context, filter: dict | None = None) -> list:
	"""List plan summaries, filtered by status or field; unreadable plans are listed, not fatal."""
	result = []
	for path in sorted((context.project / ".auto/plans").glob("*/plan.md")):
		path = path.parent
		try:
			plan = createPlan(path)
			status = PlanStatus(context, path)
		except (AutoError, OSError, ValueError) as error:
			result.append({"id": path.name, "status": "Unreadable", "error": str(error), "path": str(path)})
			continue
		result.append({"id": status["id"], "status": status["status"], "goal": str(plan.meta.get("goal") or plan.description or "").strip().partition("\n")[0],
			"tasks": len(status["tasks"]), "done": sum(state == RunStatus.SUCCEEDED for state in status["tasks"].values()), "gaps": len(status["gaps"]),
			"hold": (status["hold"] or {}).get("mode"), "usage": status["usage"], **planDates(context, path), "path": str(path)})
	return [plan for plan in result if all(plan.get(key) == value for key, value in (filter or {}).items())]


def planDates(context, path: Path) -> dict:
	"""Derive when a plan was created and last changed from its revisions and runs, else from plan.md."""
	from datetime import datetime, timezone
	from auto.features.task.operations import RunList
	dates = [row["date"] for row in journal(path) if row.get("date")]
	for run in RunList(context.derive(plan=path)):
		dates += [date for date in (run.get("started"), run.get("ended")) if date]
	dates.append(datetime.fromtimestamp((path / "plan.md").stat().st_mtime, timezone.utc).isoformat())
	dates.sort(key=lambda date: datetime.fromisoformat(date))
	return {"created": dates[0], "updated": dates[-1]}


def PlanRevise(context, plan, changes: list, reason: str, **metadata) -> dict:
	"""Apply task edits as one revision; definitions with runs cannot be edited."""
	path = planPath(context, plan)
	context = context.derive(plan=path)
	with locked(path):
		recover(context, path)
		repairLocked(context, path)
		if "expectedRevision" in metadata and metadata.pop("expectedRevision") != (journal(path) or [{}])[-1].get("id"):
			raise AutoError("Plan changed during elaboration validation; validate and retry")
		tasks = tasksAt(path)
		writes, operations = {}, []
		def remove(id):
			if id == "root" or id not in tasks:
				raise AutoError(f"Cannot remove task: {id}")
			for child in list(tasks):
				if child != "root" and tasks[child].meta.get("parent", "root") == id:
					remove(child)
			task = tasks.pop(id)
			writes[f".history/{id}.md"] = task.path.read_text() if task.path.exists() else taskText(task)
			writes[f"tasks/{id}.md"] = None
			operations.append({"op": RevisionOp.REMOVE, "task": id})
		for change in changes:
			op = change["op"]
			if op in {RevisionOp.HOLD, RevisionOp.RELEASE}:
				if context.author != "human":
					raise AutoError("Plan holds and releases require a human author")
				if not isinstance(change.get("reason"), str) or not change["reason"].strip():
					raise AutoError("A hold or release requires a reason")
				if op == RevisionOp.HOLD and change.get("mode") not in {"Pause", "Stop"}:
					raise AutoError("Hold mode must be Pause or Stop")
				operations.append({"op": op, "reason": change["reason"], **({"mode": change["mode"]} if op == RevisionOp.HOLD else {})})
			elif op == "agents":
				agents = validateAgents(context, change["agents"])
				root = tasks["root"]
				root.meta["agents"] = agents
				meta, body = document(taskText(root))
				meta["id"] = createPlan(path).id
				writes["plan.md"] = documentText(meta, body)
				operations.append({"op": "agents", "agents": agents})
			elif op == "remove":
				remove(change["task"])
			elif op in {"add", "edit", "replace"}:
				value = change["task"]
				id = identifier(value["id"])
				if id == "root":
					raise AutoError("Use update for the root task")
				if op == "edit":
					if id not in tasks or latestRun(path, id):
						raise AutoError("Only tasks without runs can be edited")
				elif id in tasks or (path / ".history" / (id + ".md")).exists() or latestRun(path, id):
					raise AutoError(f"Task identity already used: {id}")
				if op == "replace":
					old = change["old"]
					parent = tasks[old].meta.get("parent", "root") if old in tasks else "root"
					remove(old)
					value = {**value, "replaces": old, "parent": value.get("parent", parent)}
				task = createTaskFromDict(value, path / "tasks" / (id + ".md"))
				tasks[id] = task
				text = taskText(task)
				writes[f"tasks/{id}.md"] = text
				operation = {"op": op, "task": id, "digest": digest(text.encode()), "parent": task.meta.get("parent", "root")}
				if op == "replace":
					operation["old"] = change["old"]
				operations.append(operation)
			elif op == "update":
				if latestRun(path, "root"):
					raise AutoError("Root definition is immutable after a run")
				if "agents" in change["fields"]:
					raise AutoError("Use the agents revision operation to change plan agents")
				root = tasks["root"]
				root.meta.update(change["fields"])
				rootText = taskText(root)
				meta, body = document(rootText)
				meta["id"] = createPlan(path).id
				writes["plan.md"] = documentText(meta, body)
				operations.append({"op": RevisionOp.UPDATE, "field": list(change["fields"])})
			else:
				raise AutoError(f"Unsupported revision operation: {op}")
		dependencyGraph(context, tasks)
		from auto.features.task.operations import TaskValidate
		for task in tasks.values():
			violations = TaskValidate(context, task, graph=False, aggregate=any(t.meta.get("parent", "root") == task.id for t in tasks.values() if t.id != "root"))
			if violations:
				raise AutoError(jsonText(violations))
		operations.extend(metadata.pop("extraOps", []))
		revision = commit(context, path, operations, writes, reason=reason, **metadata)
	if any(op["op"] == RevisionOp.HOLD and op["mode"] == "Stop" for op in operations):
		from auto.features.task.operations import RunCancel, RunList
		for run in RunList(context):
			if run["status"] in IN_FLIGHT:
				RunCancel(context, run)
	return revision


def PlanReady(context, plan=None) -> list:
	"""Return the tasks whose dependencies and gaps allow a run."""
	path = planPath(context, plan)
	context = context.derive(plan=path)
	from auto.features.plan.policy import taskPolicyBlocked
	gaps = GapList(context, path)
	tasks = tasksAt(path)
	graph = dependencyGraph(context, tasks)
	def ready(id, dependencies):
		run = latestRun(path, id)
		return not taskPolicyBlocked(context, tasks[id]) and not taskGapState(context, id, tasks, dependencies, gaps) \
			and all(latestRun(path, dependency, True) for dependency in dependencies) \
			and (run or {}).get("status") not in {RunStatus.PENDING, RunStatus.RUNNING, RunStatus.WAITING} and not latestRun(path, id, True)
	return [id for id, dependencies in graph.items() if ready(id, dependencies)]


def PlanStatus(context, plan=None) -> dict:
	"""Return the derived progress, gaps, budget and policy of a plan."""
	path = planPath(context, plan)
	context = context.derive(plan=path)
	from auto.features.plan.policy import PolicyResolve, taskPolicyBlocked
	from auto.features.task.operations import RunList
	meta = createPlan(path).meta
	tasks, runs = tasksAt(path), RunList(context)
	graph = dependencyGraph(context, tasks)
	allGaps = GapList(context, path)
	gaps = [gap for gap in allGaps if gap["status"] == GapState.OPEN]
	states, policyTasks = {}, {}
	def state(id, run):
		gapState = taskGapState(context, id, tasks, graph[id], allGaps)
		if gapState:
			return gapState
		if policyTasks[id]["blocked"] or any(not latestRun(path, dependency, True) for dependency in graph[id]):
			return RunStatus.BLOCKED
		status = run["status"] if run else "Ready"
		return "Ready" if status == RunStatus.SUCCEEDED and not latestRun(path, id, True) else status
	for id in tasks:
		run = latestRun(path, id)
		policyTasks[id] = {"effects": tasks[id].meta.get("effects", []), "blocked": taskPolicyBlocked(context, tasks[id])}
		states[id] = state(id, run)
	usage = {key: sum(run["usage"].get(key, 0) for run in runs) for key in ("tokens", "cost", "time")}
	stale, staleRuns = [], []
	for revision in journal(path):
		for pin in revision.get("context", []):
			try:
				changed = Resolve(context, pin["ref"]).digest != pin["digest"]
			except AutoError:
				changed = True
			if changed and pin not in stale:
				stale.append(pin)
	for run in runs:
		changed = []
		for pin in run.get("inputs", []):
			try:
				if Resolve(context, pin["ref"]).digest == pin["digest"]:
					continue
			except AutoError:
				pass
			changed.append(pin)
		if changed:
			staleRuns.append({"run": run["id"], "inputs": changed})
	abandoned = any(op["op"] == RevisionOp.ABANDON for row in journal(path) for op in row["operations"])
	if abandoned:
		status = PlanState.ABANDONED
	elif states.get("root") == RunStatus.SUCCEEDED:
		status = PlanState.SUCCEEDED
	elif states.get("root") in {RunStatus.FAILED, RunStatus.TIMED_OUT}:
		status = PlanState.FAILED
	elif any(run["task"] in tasks for run in runs):
		status = PlanState.ACTIVE
	else:
		status = PlanState.DRAFT
	policy = PolicyResolve(context, path)
	policy["tasks"] = policyTasks
	return {"id": meta["id"], "status": status, "tasks": states, "gaps": gaps, "policy": policy, "budget": meta.get("budget", {}), "usage": usage, "hold": planHold(path),
		"continuation": readJson(path / "continuation.json"),
		"failures": [run["id"] for run in runs if run.get("failure")], "stale": stale, "staleRuns": staleRuns, "revision": (journal(path) or [{}])[-1].get("id")}


def PlanJournal(context, plan=None, since: str | None = None) -> list:
	"""Return plan revisions, optionally after a cursor."""
	rows = journal(planPath(context, plan))
	if since:
		indices = [index for index, row in enumerate(rows) if row["id"] == since]
		if not indices:
			raise AutoError("Unknown revision cursor")
		rows = rows[indices[0] + 1:]
	return rows


def PlanAbandon(context, plan, reason: str) -> dict:
	"""End a plan as abandoned and cancel its active runs."""
	path = planPath(context, plan)
	context = context.derive(plan=path)
	with locked(path):
		recover(context, path)
		repairLocked(context, path)
		revision = commit(context, path, [{"op": RevisionOp.ABANDON}], reason=reason)
	from auto.features.task.operations import RunList, RunCancel
	for run in RunList(context):
		if run["status"] in IN_FLIGHT:
			RunCancel(context, run)
	return revision

# EOF
