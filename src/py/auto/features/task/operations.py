"""Module: operations — Task contracts, persisted runs and resumable lifecycle."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from liquid.exceptions import LiquidError

from auto.features.plan.model import Plan
from auto.features.plan.references import latestRun
from auto.features.plan.store import locked, planPath, recover
from auto.features.script.model import Script
from auto.features.script.operations import ScriptExpand, ScriptInputs, executePrepared, prepare
from auto.features.script.templates import Renderer
from auto.features.task.model import Run, Task, parseTask, taskFromDict
from auto.runtime.model import Context, FailureKind, Limits, RunStatus, TERMINAL, budgetValue, failureFor, statusFor
from auto.runtime.operations import Resolve, Shell
from auto.utils.data import AutoError, atomicWrite, document, identifier, jsonText, merge, readJson, timestamp
from auto.utils.references import relocate

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------

VERDICT = {"type": "object", "properties": {"pass": {"type": "boolean"}, "reason": {"type": "string"}}, "required": ["pass", "reason"], "additionalProperties": False}
EVALUATION = {"type": "object", "properties": {"score": {"type": "number", "minimum": 0, "maximum": 1}, "feedback": {"type": "string"}}, "required": ["score", "feedback"], "additionalProperties": False}
UNSET = object()


def TaskGet(context, id: str) -> Task:
	path = planPath(context)
	identifier(id)
	file = path / "plan.md" if id == "root" else path / "tasks" / (id + ".md")
	if not file.exists():
		raise AutoError(f"Task not found: {id}")
	return parseTask(file.read_text(), file, id == "root")


def resolveTask(context, task) -> Task:
	if isinstance(task, Task):
		return task
	if isinstance(task, dict):
		return taskFromDict(task, Path(task.get("path", context.project / "task.md")))
	return TaskGet(context, task)


def effectiveTask(context, task: Task) -> Task:
	"""Resolve profile and artifact defaults at their own source locations."""
	meta, parts = dict(task.meta), dict(task.sections)
	if meta.get("profile"):
		content = Resolve(context, meta["profile"], task.path.parent)
		profile, _ = document(content.text)
		meta = merge({key: value for key, value in profile.items() if key in {"engine", "provider", "model", "agent", "temperature", "budget"}}, meta)
	if meta.get("artifact"):
		content = Resolve(context, meta["artifact"], task.path.parent)
		artifact = parseTask(content.text, content.path)
		contract = dict(artifact.meta.get("output", {}))
		for key, value in contract.items():
			if isinstance(value, str) and value.startswith(("./", "../")):
				contract[key] = str((content.path.parent / value).resolve())
		meta["output"] = merge(contract, meta.get("output", {}))
		for name in ("Post", "Evaluation"):
			if name in artifact.sections:
				# Keep the included fragment's relative references attached to its origin.
				text = relocate(artifact.sections[name], content.path.parent)
				parts[name] = text + "\n" + parts.get(name, "")
	return Task(task.id, meta, parts, task.path, task.description)


def TaskDependencies(context, task) -> list:
	task = effectiveTask(context, resolveTask(context, task))
	refs = set()
	for section in task.sections or {"Process": ""}:
		refs.update(ScriptInputs(context, task.script(section))["references"])
	return sorted({ref[5:].split("/", 1)[0].split("#", 1)[0] for ref in refs if ref.startswith("task:")})


def TaskValidate(context, task, graph: bool = True, aggregate: bool = False) -> list:
	violations = []
	try:
		task = effectiveTask(context, resolveTask(context, task))
		identifier(task.id)
		budgetValue(task.meta.get("budget"))
		if "Process" not in task.sections and task.id != "root" and not aggregate and task.meta.get("hints", {}).get("elaborate") != "later":
			raise AutoError("Task requires Process unless it is an aggregate or deferred")
		TaskDependencies(context, task)
		if graph and context.plan:
			from auto.features.plan.operations import dependencyGraph, tasksAt
			tasks = tasksAt(context.plan)
			tasks[task.id] = task
			dependencyGraph(context, tasks)
	except (AutoError, ValueError, LiquidError) as error:
		violations.append({"element": "task:" + getattr(task, "id", "unknown"), "message": str(error)})
	return violations


def TaskCreate(context, task: dict) -> str:
	from auto.features.plan.operations import PlanRevise
	PlanRevise(context, context.plan, [{"op": "add", "task": task}], "Create task")
	return task["id"]


def TaskUpdate(context, id: str, changes: dict) -> Task:
	from auto.features.plan.operations import PlanRevise
	task = TaskGet(context, id)
	PlanRevise(context, context.plan, [{"op": "edit", "task": merge(task.asDict(), changes)}], "Update task")
	return TaskGet(context, id)


def TaskDelete(context, id: str) -> None:
	from auto.features.plan.operations import PlanRevise
	PlanRevise(context, context.plan, [{"op": "remove", "task": id}], "Delete task")


def TaskList(context, filter: dict | None = None) -> list:
	from auto.features.plan.operations import tasksAt
	return [task.asDict() for task in tasksAt(planPath(context)).values() if (not filter or
		(not filter.get("prefix") or task.id.startswith(filter["prefix"])) and
		(not filter.get("input") or filter["input"] in TaskDependencies(context, task)))]


def TaskExpand(context, task) -> str:
	return ScriptExpand(context, effectiveTask(context, resolveTask(context, task)).script())


def TaskReady(context, task) -> bool:
	task = resolveTask(context, task)
	from auto.features.plan.operations import dependencyGraph, tasksAt
	path = planPath(context)
	graph = dependencyGraph(context, tasksAt(path))
	return all(latestRun(path, dependency, True) for dependency in graph.get(task.id, TaskDependencies(context, task)))


def runDirectory(context, run) -> Path:
	return planPath(context) / "runs" / identifier(run["id"] if isinstance(run, dict) else run)


def RunGet(context, run) -> dict:
	path = runDirectory(context, run)
	value = readJson(path / "receipt.json") or readJson(path / "active.json")
	if value is None:
		value = readJson(path / "receipt.json")
	if value is None:
		raise AutoError(f"Run not found: {path.name}")
	if (path / "receipt.json").exists():
		for line in (path / "journal.jsonl").read_text().splitlines() if (path / "journal.jsonl").exists() else []:
			event = json.loads(line)
			if event["type"] == "AssessmentUsage":
				for key, amount in event["usage"].items():
					value["usage"][key] = value["usage"].get(key, 0) + amount
	return Run.fromDict(value).asDict()


def RunList(context, filter: dict | None = None) -> list:
	root = planPath(context) / "runs"
	result = [RunGet(context, path.name) for path in sorted(root.iterdir()) if (path / "active.json").exists() or (path / "receipt.json").exists()] if root.exists() else []
	return [run for run in result if all((run["started"].startswith(value) if key == "date" else run.get(key) == value) for key, value in (filter or {}).items())]


def RunOutput(context, run):
	value = RunGet(context, run)
	file = runDirectory(context, run) / value.get("outputFile", "output.txt")
	if not file.exists():
		raise AutoError("Run has no output")
	text = file.read_text()
	return json.loads(text) if file.suffix == ".json" else text


def emit(path: Path, event: dict) -> None:
	with (path / "journal.jsonl").open("a") as stream:
		stream.write(jsonText(event) + "\n")
		stream.flush()
		os.fsync(stream.fileno())


def RunJournal(context, run, since: int | None = None):
	path = runDirectory(context, run)
	index = since or 0
	while True:
		file = path / "journal.jsonl"
		lines = file.read_text().split("\n")[:-1] if file.exists() else []
		for line in lines[index:]:
			yield json.loads(line)
		index = len(lines)
		if RunGet(context, run)["status"] in TERMINAL:
			return
		time.sleep(.1)


def saveRun(path, run, state=None):
	if state is not None:
		atomicWrite(path / "state.json", jsonText(state))
	atomicWrite(path / ("receipt.json" if run["status"] in TERMINAL else "active.json"), jsonText(run))
	if run["status"] in TERMINAL:
		(path / "active.json").unlink(missing_ok=True)


def checkRequest(context, task, check):
	"""Expand checks, run shell evidence first, then ask the model only if needed."""
	renderer = Renderer(context, task.script(check))
	text = renderer.render()
	shells, prose = [], []
	for line in text.splitlines():
		(shells if line.startswith("!?") else prose).append(line[2:] if line.startswith("!?") else line)
	evidence, last = [], None
	for command in shells:
		stdout, stderr, code = Shell(context, command, renderer.config)
		if check != "Evaluation":
			last = {"pass": code == 0, "reason": stdout + stderr}
			if code:
				return None, last
		else:
			if code:
				raise AutoError(f"Evaluation shell failed: {stderr}", "output")
			from auto.features.script.operations import conform
			last = conform(stdout, {"format": "json", "schema": EVALUATION})
		evidence.append(stdout + stderr)
	if not "\n".join(prose).strip():
		return None, last or ({"score": 1, "feedback": "No evaluation"} if check == "Evaluation" else {"pass": True, "reason": "No check"})
	meta = {**task.meta, "output": {"format": "json", "schema": EVALUATION if check == "Evaluation" else VERDICT}}
	# Already-expanded prose is supplied as data, so transclusions cannot execute twice.
	content = "{{ instructions }}\n\n{{ evidence }}\n\nTask inputs: {{ inputs | json }}\nRun output: {{ result | json }}"
	ctx = context.derive(variables={**context.variables, "instructions": "\n".join(prose), "evidence": evidence, "result": context.variables.get("result")})
	return prepare(ctx, Script(content, task.path, meta)), None


def _drive(context, path, run, state, request, answer=None):
	state["request"] = request
	run["runtime"].update({key: request["config"][key] for key in ("engine", "model") if key in request["config"]})
	saveRun(path, run, state)
	stream = executePrepared(context, request)
	try:
		while True:
			event = stream.send(answer)
			answer = None
			emit(path, event)
			run["usage"] = dict(context.limits.usage)
			if event["type"] == "ScriptMeta" and event["name"] == "session":
				run["runtime"]["session"] = event["value"]
			if event["type"] == "ScriptMeta" and event["name"] == "runtime":
				run["runtime"].update(event["value"])
			if event["type"] == "ScriptQuestion":
				from auto.features.plan.gaps import GapRaise
				gap = GapRaise(context, context.plan, {"kind": "information", "question": event["content"], "needed_by": [run["task"] if run["task"] != "elaborator" else state.get("target", "root")], "raised_by": run["id"]})
				run.update(status=RunStatus.WAITING, gap=gap, question=event)
				state["question"] = event
				context.variables["_detach"] = True
				run["usage"] = dict(context.limits.usage)
				emit(path, {"type": "GapRaised", "gap": gap})
				saveRun(path, run, state)
				return None, True
			if event["type"] == "ScriptEnded":
				if event["status"] != RunStatus.SUCCEEDED:
					failure = event.get("failure", {"kind": FailureKind.SYSTEM, "reason": "Engine failed"})
					raise AutoError(failure["reason"], FailureKind.TIMEOUT if event["status"] == RunStatus.TIMED_OUT else FailureKind.CANCELLED if event["status"] == RunStatus.CANCELLED else failure["kind"])
				return event["output"], False
			saveRun(path, run, state)
	finally:
		stream.close()


def _continue(context, path, run, state, answer=UNSET):
	task = taskFromDict(state["task"], Path(state["task"]["path"]))
	limits = Limits(run["budget"], dict(run["usage"]), initialTime=run["usage"].get("time", 0))
	variables = {"plan": Plan.read(context.plan).id, "run": {"id": run["id"], "output": str(path / run.get("outputFile", "output.txt"))}}
	context = context.derive(config=state["config"], defaults=state.get("defaults", {}), inputs=state["inputs"], params=state.get("params", {}), variables=variables,
		limits=limits, evidence=run["inputs"], cancelled=lambda: (path / "cancel").exists())
	run["status"] = RunStatus.RUNNING
	try:
		if answer is not UNSET:
			from auto.runtime.operations import Value
			state["request"]["answer"] = Value(context, answer, context.plan)
		if "resolvedInputs" not in state:
			state["resolvedInputs"] = Renderer(context, task.script()).bindings()["inputs"]
		context.resolvedInputs = state["resolvedInputs"]
		for phase in ("Pre", "Process", "Post", "Evaluation"):
			if phase in state["completed"]:
				continue
			if phase != "Process" and not task.sections.get(phase):
				state["completed"].append(phase)
				continue
			limits.check()
			state["phase"] = phase
			if phase in {"Post", "Evaluation"}:
				context.variables["result"] = RunOutput(context, run)
				context.variables["run"]["output"] = str(path / run["outputFile"])
			request = state.get("request")
			value = None
			if request is None:
				if phase == "Process":
					if not task.sections.get("Process"):
						value = context.inputs.get("children", {})
						from auto.features.script.operations import conform
						contract = prepare(context, task.script())["output"] if task.meta.get("output") else {"format": "json"}
						value = conform(value, contract)
					else:
						request = prepare(context, task.script())
				else:
					request, value = checkRequest(context, task, phase)
			if request:
				value, waiting = _drive(context, path, run, state, request)
				if waiting:
					return run
			state.pop("request", None)
			state["completed"].append(phase)
			if phase == "Process":
				run["outputFile"] = "output.json" if not isinstance(value, str) or task.meta.get("output", {}).get("format") == "json" else "output.txt"
				atomicWrite(path / run["outputFile"], jsonText(value) if run["outputFile"].endswith(".json") else value)
			elif phase == "Evaluation":
				run["evaluation"] = value
				emit(path, {"type": "TaskEvaluated", "evaluation": value})
			else:
				run[phase.lower()] = value
				emit(path, {"type": "TaskChecked", "check": phase.lower(), "verdict": value})
				if not value["pass"]:
					run["status"] = RunStatus.SKIPPED if phase == "Pre" else RunStatus.FAILED
					run["failure"] = {"kind": "input" if phase == "Pre" else "output", "reason": value["reason"]}
					break
			saveRun(path, run, state)
		limits.check()
		if run["status"] == RunStatus.RUNNING:
			run["status"] = RunStatus.SUCCEEDED
			if task.meta.get("hints", {}).get("gate") == "approval":
				from auto.features.plan.gaps import GapRaise
				from auto.features.plan.operations import dependencyGraph, tasksAt
				graph = dependencyGraph(context, tasksAt(context.plan))
				needed = [id for id, deps in graph.items() if task.id in deps] or ["root"]
				content = Resolve(context, "run:" + run["id"])
				GapRaise(context, context.plan, {"kind": "approval", "question": f"Approve output of {task.id}?", "needed_by": needed,
					"subjects": [{"ref": content.ref, "digest": content.digest}], "raised_by": run["id"]})
	except (AutoError, KeyboardInterrupt) as error:
		kind = error.kind if isinstance(error, AutoError) else FailureKind.CANCELLED
		run["status"] = statusFor(kind)
		run["failure"] = {"kind": failureFor(kind), "reason": str(error)}
	except Exception as error:
		# Integration failures must leave an inspectable terminal receipt.
		run["status"] = RunStatus.FAILED
		run["failure"] = {"kind": "system", "reason": f"{type(error).__name__}: {error}"}
	finally:
		limits.usage["time"] = limits.initialTime + time.monotonic() - limits.started
		run["usage"] = limits.usage
		if run["status"] in TERMINAL:
			run["ended"] = timestamp()
			emit(path, {"type": "RunEnded", "status": run["status"]})
		saveRun(path, run, state)
	return run


def TaskStart(context: Context, task, inputs: dict | None = None, *, metaTask: bool = False) -> dict:
	"""Run to a terminal or Waiting boundary; each attempt gets a fresh identity."""
	plan = planPath(context)
	context = context.derive(plan=plan, evidence=[])
	with locked(plan):
		recover(context, plan)
		from auto.features.plan.gaps import repairLocked, GapList
		repairLocked(context, plan)
		from auto.features.plan.store import journal
		if any(op["op"] == "abandon" for row in journal(plan) for op in row["operations"]):
			raise AutoError("Cannot start a run in an abandoned plan")
		task = effectiveTask(context, resolveTask(context, task))
		Resolve(context, str(task.path))
		previous = latestRun(plan, task.id)
		if previous and previous["status"] in {RunStatus.RUNNING, RunStatus.WAITING}:
			raise AutoError("Task already has an active run")
		stem = task.id + "-" + time.strftime("%Y%m%dT%H%M", time.gmtime())
		counter = 1
		while (plan / "runs" / f"{stem}-{counter:02}").exists():
			counter += 1
		path = plan / "runs" / f"{stem}-{counter:02}"
		path.mkdir(parents=True)
		budget = budgetValue(task.meta.get("budget"))
		run = Run(id=path.name, task=task.id, status=RunStatus.PENDING, started=timestamp(),
			runtime={"engine": context.config.get("engine", task.meta.get("engine", context.defaults.get("engine", "pi"))), "provider": context.config.get("provider", task.meta.get("provider", context.defaults.get("provider"))), "model": context.config.get("model", task.meta.get("model", context.defaults.get("model")))},
			budget=budget, usage={"tokens": 0, "cost": 0, "time": 0}, inputs=context.evidence).asDict()
		blocked = not metaTask and (not TaskReady(context, task) or any(task.id in gap["needed_by"] for gap in GapList(context, plan, {"status": "Open"})))
		if blocked:
			run.update(status=RunStatus.BLOCKED, ended=timestamp())
			saveRun(path, run)
			return run
		from auto.features.plan.operations import tasksAt
		children = {id: RunOutput(context, latestRun(plan, id, True)) for id, child in tasksAt(plan).items() if id != "root" and child.meta.get("parent", "root") == task.id}
		if task.meta.get("hints", {}).get("elaborate") == "later" and not task.sections.get("Process") and not children:
			run.update(status=RunStatus.BLOCKED, ended=timestamp(), failure={"kind": FailureKind.INPUT, "reason": "Deferred task must be decomposed before execution"})
			saveRun(path, run)
			return run
		run["children"] = {id: latestRun(plan, id, True)["id"] for id in children}
		for id in children:
			Resolve(context, "task:" + id)
		state = {"task": task.asDict(), "config": context.config, "defaults": context.defaults, "inputs": merge(context.inputs, inputs or {}, {"children": children} if children else {}), "params": context.params, "completed": [], "target": context.variables.get("target", "root"), "applyElaboration": context.variables.get("applyElaboration", False)}
		# The harness and shell checks need the same Auto entry point and plan context.
		state["config"] = merge(state["config"], {"env": {"AUTO_PLAN": str(plan)}})
		run["status"] = RunStatus.RUNNING
		saveRun(path, run, state)
	with locked(path):
		return _continue(context, path, run, state)


def resumeGap(context, gap, answer):
	for run in RunList(context, {"status": RunStatus.WAITING}):
		if run.get("gap") == gap:
			path = runDirectory(context, run)
			with locked(path):
				run = RunGet(context, run)
				if run["status"] == RunStatus.WAITING:
					emit(path, {"type": "GapResolved", "gap": gap})
					state = readJson(path / "state.json")
					return _continue(context, path, run, state, answer)


def RunAnswer(context, run, answer, rationale: str):
	from auto.features.plan.gaps import GapDecide
	value = RunGet(context, run)
	if value["status"] != RunStatus.WAITING:
		raise AutoError("Run is not waiting")
	return GapDecide(context, value["gap"], answer, rationale)


def RunCancel(context, run) -> dict:
	value = RunGet(context, run)
	if value["status"] in TERMINAL:
		return value
	path = runDirectory(context, run)
	atomicWrite(path / "cancel", "cancel\n")
	with locked(path):
		value = RunGet(context, run)
		if value["status"] not in TERMINAL:
			from auto.runtime.operations import Engine
			state = readJson(path / "state.json")
			engine = Engine(context, value["runtime"]["engine"])
			if hasattr(engine, "cancel") and state.get("request"):
				engine.cancel(context, state["request"])
			value.update(status=RunStatus.CANCELLED, ended=timestamp(), failure={"kind": FailureKind.CANCELLED, "reason": "Cancelled by caller"})
			emit(path, {"type": "RunEnded", "status": RunStatus.CANCELLED})
			saveRun(path, value)
	return RunGet(context, run)


def RunCheck(context, run, check: str | dict):
	"""Evaluate a named check or a supplied TaskCheck against the stored run."""
	return _assess(context, run, check.title() if check in ("pre", "post") else "Post", check if check not in ("pre", "post") else None)


def RunEvaluate(context, run):
	return _assess(context, run, "Evaluation")


def _assess(context, run, check, instructions=None):
	path = runDirectory(context, run)
	with locked(path):
		receipt = RunGet(context, run)
		if receipt["status"] not in TERMINAL:
			raise AutoError("Wait for the active lifecycle before reassessing a run")
		state = readJson(path / "state.json")
		if state is None:
			raise AutoError("Run did not reach assessment")
		task = taskFromDict(state["task"], Path(state["task"]["path"]))
		if instructions is not None:
			task.sections[check] = instructions.get("content", "") if isinstance(instructions, dict) else instructions
		if not task.sections.get(check):
			raise AutoError(f"Run has no {check} check")
		limits = Limits(receipt["budget"], dict(receipt["usage"]), initialTime=receipt["usage"]["time"])
		ctx = context.derive(config=state["config"], defaults=state.get("defaults", {}), inputs=state["inputs"], resolvedInputs=state.get("resolvedInputs", {}), limits=limits,
			variables={"plan": Plan.read(context.plan).id, "run": {"id": path.name, "output": str(path / receipt.get("outputFile", "output.txt"))}, "result": RunOutput(context, run) if check != "Pre" else None})
		stream = None
		try:
			limits.check()
			request, value = checkRequest(ctx, task, check)
			if request:
				stream = executePrepared(ctx, request)
				for event in stream:
					if event["type"] == "ScriptEnded":
						if event["status"] != RunStatus.SUCCEEDED:
							raise AutoError(event.get("failure", {}).get("reason", "Assessment failed"), "output")
						value = event["output"]
					if event["type"] == "ScriptQuestion":
						raise AutoError("Standalone reassessment cannot suspend a completed run")
			limits.check()
			emit(path, {"type": "TaskEvaluated", "evaluation": value} if check == "Evaluation" else {"type": "TaskChecked", "check": check.lower(), "verdict": value})
			return value
		finally:
			if stream:
				stream.close()
			limits.usage["time"] = limits.initialTime + time.monotonic() - limits.started
			emit(path, {"type": "AssessmentUsage", "usage": {key: limits.usage[key] - receipt["usage"][key] for key in limits.usage}})

# EOF
