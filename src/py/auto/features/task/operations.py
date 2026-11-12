"""Module: operations — Task contracts, persisted runs and resumable lifecycle."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from liquid.exceptions import LiquidError

# NOTE: Task runs live within a plan (spec-003), so task operations use plan storage and policy directly.
from auto.features.plan.gaps import GapDecide, GapList, GapRaise, planRef, repairLocked
from auto.features.plan.model import GapKind, GapState, RevisionOp, createPlan
from auto.features.plan.operations import PlanRevise, dependencyGraph, tasksAt
from auto.features.plan.policy import EFFECT, PolicyResolve, runtimeRule, shellPermitted, taskPolicyBlocked
from auto.features.plan.references import latestRun, readReceipt, runDirectories
from auto.features.plan.store import journal, locked, planPath, recover
from auto.features.script.config import commands
from auto.features.script.model import Script
from auto.features.script.operations import ScriptExpand, ScriptInputs, conform, executePrepared, prepare
from auto.features.script.templates import Renderer
from auto.features.task.model import Run, Task, createRunFromDict, createTaskFromDict, createTaskFromText
from auto.runtime.config import RUNTIME_DEFAULTS
from auto.runtime.integration import CancellableEngine
from auto.runtime.model import Context, Event, FailureKind, Limits, RunStatus, TERMINAL, budgetValue, failureFor, kindFor, statusFor
from auto.runtime.operations import Engine, Knowledge, Resolve, Shell, Value
from auto.utils.data import AutoError, atomicWrite, document, identifier, jsonText, merge, readJson, timestamp
from auto.utils.references import relocate

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------


VERDICT = {"type": "object",
	"properties": {"pass": {"type": "boolean"}, "reason": {"type": "string"}},
	"required": ["pass", "reason"], "additionalProperties": False}
EVALUATION = {"type": "object",
	"properties": {"score": {"type": "number", "minimum": 0, "maximum": 1}, "feedback": {"type": "string"}},
	"required": ["score", "feedback"], "additionalProperties": False}
UNSET = object()
# Front matter keys holding the shell commands of each lifecycle phase.
LIFECYCLE = {"Pre": "pre", "Post": "post", "Apply": "apply", "Evaluation": "eval"}


# -----------------------------------------------------------------------------
#
# UTILITIES
#
# -----------------------------------------------------------------------------


def resolveTask(context, task) -> Task:
	"""Resolve a task model, mapping or identifier to a Task."""
	if isinstance(task, Task):
		return task
	if isinstance(task, dict):
		return createTaskFromDict(task, Path(task.get("path", context.project / "task.md")))
	return TaskGet(context, task)


def effectiveTask(context, task: Task) -> Task:
	"""Resolve profile and artifact defaults at their own source locations."""
	meta, parts = dict(task.meta), dict(task.sections)
	if "each" in meta:
		raise AutoError("each is only supported by scripts: a task run is a single item")
	if meta.get("profile"):
		content = Resolve(context, meta["profile"], task.path.parent)
		profile, _ = document(content.text)
		meta = merge({key: value for key, value in profile.items() if key in {"engine", "provider", "model", "agent", "temperature", "budget"}}, meta)
	if meta.get("artifact"):
		content = Resolve(context, meta["artifact"], task.path.parent)
		artifact = createTaskFromText(content.text, content.path)
		contract = dict(artifact.meta.get("output", {}))
		for key, value in contract.items():
			if isinstance(value, str) and value.startswith(("./", "../")):
				contract[key] = str((content.path.parent / value).resolve())
		meta["output"] = merge(contract, meta.get("output", {}))
		for key in ("post", "eval"):
			if artifact.meta.get(key):
				meta[key] = commands(artifact.meta[key]) + commands(meta.get(key))
		for name in ("Post", "Evaluation"):
			if name in artifact.sections:
				# Keep the included fragment's relative references attached to its origin.
				text = relocate(artifact.sections[name], content.path.parent)
				parts[name] = text + "\n" + parts.get(name, "")
	return Task(task.id, meta, parts, task.path, task.description)


def runDirectory(context, run) -> Path:
	"""Return the storage directory of a run in the current plan."""
	return planPath(context) / "runs" / identifier(run["id"] if isinstance(run, dict) else run)


def emit(path: Path, event: dict) -> None:
	"""Append one event to a run journal and flush it durably."""
	with (path / "journal.jsonl").open("a") as stream:
		stream.write(jsonText(event) + "\n")
		stream.flush()
		os.fsync(stream.fileno())


def saveRun(path, run, state=None):
	"""Persist a run receipt or its active state."""
	if state is not None:
		atomicWrite(path / "state.json", jsonText(state))
	atomicWrite(path / ("receipt.json" if run["status"] in TERMINAL else "active.json"), jsonText(run))
	if run["status"] in TERMINAL:
		(path / "active.json").unlink(missing_ok=True)


def hasCheck(task, phase) -> bool:
	"""Tell whether a task defines a check for a lifecycle phase."""
	return bool(task.sections.get(phase) or task.meta.get(LIFECYCLE[phase]))


def outputText(value) -> str:
	"""Return an output as text, serialising non-strings as JSON."""
	return "" if value is None else value if isinstance(value, str) else jsonText(value)


def checkRequest(context, task, check):
	"""Expand checks, run shell evidence first, then ask the model only if needed.

	Front matter commands run before the body `!?` checks: both add up, the
	body does not override the front matter."""
	renderer = Renderer(context, task.script(check))
	namespace = renderer.bindings()
	if check != "Pre":
		namespace["output"] = context.variables.get("result")
	text = renderer.render(namespace)
	shells = [renderer.shellArgument(command, namespace) for command in commands(task.meta.get(LIFECYCLE[check]))]
	prose = []
	for line in text.splitlines():
		(shells if line.startswith("!?") else prose).append(line[2:] if line.startswith("!?") else line)
	stdin = "" if check == "Pre" else outputText(context.variables.get("result"))
	evidence, last = [], None
	for command in shells:
		stdout, stderr, code = Shell(context, command, renderer.config, stdin)
		if check != "Evaluation":
			last = {"pass": code == 0, "reason": stdout + stderr}
			if code:
				return None, last
		else:
			if code:
				raise AutoError(f"Evaluation shell failed: {stderr}", "output")
			last = conform(stdout, {"format": "json", "schema": EVALUATION})
		evidence.append(stdout + stderr)
	if not "\n".join(prose).strip():
		return None, last or ({"score": 1, "feedback": "No evaluation"} if check == "Evaluation" else {"pass": True, "reason": "No check"})
	meta = {**task.meta, "output": {"format": "json", "schema": EVALUATION if check == "Evaluation" else VERDICT}}
	# Already-expanded prose is supplied as data, so transclusions cannot execute twice.
	content = "{{ instructions }}\n\n{{ evidence }}\n\nTask inputs: {{ inputs | json }}\nRun output: {{ result | json }}"
	ctx = context.derive(variables={**context.variables, "instructions": "\n".join(prose), "evidence": evidence, "result": context.variables.get("result")})
	return prepare(ctx, Script(content, task.path, meta)), None


def applyEffects(context, task) -> dict:
	"""Run the `apply` commands, in order, with the run output on stdin."""
	renderer = Renderer(context, task.script("Apply"))
	result = context.variables.get("result")
	namespace = {**renderer.bindings(), "output": result}
	for command in commands(task.meta.get("apply")):
		command = renderer.shellArgument(command, namespace)
		stdout, stderr, code = Shell(context, command, renderer.config, outputText(result))
		if code:
			return {"pass": False, "reason": (stdout + stderr).strip() or f"apply command failed ({code}): {command}"}
	return {"pass": True, "reason": "Applied"}


def _drive(context, path, run, state, request, answer=None):
	"""Drive one engine stream, persisting usage and raising policy gaps."""
	taskId = run["task"] if run["task"] != "elaborator" else state.get("target", "root")
	definition = TaskGet(context, taskId)
	request["policy"] = PolicyResolve(context, context.plan, taskId)
	if run["task"] == "elaborator":
		definition = createTaskFromDict({**state["task"], "id": "elaborator", "effects": []}, Path(state["task"]["path"]))
		base = "auto:" + createPlan(context.plan).id
		request["policy"]["grants"] = [grant for grant in request["policy"]["grants"] if grant["scope"] == base]
	request["policy"]["effects"] = definition.meta.get("effects", [])
	request["policy"]["blocked"] = taskPolicyBlocked(context, definition)
	state["request"] = request
	run["runtime"].update({key: request["config"][key] for key in ("engine", "model") if key in request["config"]})
	saveRun(path, run, state)
	stream = executePrepared(context, request)
	try:
		while True:
			event = stream.send(answer)
			answer = None
			if event["type"] != Event.POLICY_REQUEST:
				emit(path, event)
			if context.observe:
				observed = {**event, "value": dict(context.limits.usage)} if event.get("type") == Event.SCRIPT_META and event.get("name") == "usage" else event
				context.observe(observed)
			run["usage"] = dict(context.limits.usage)
			if event["type"] == Event.SCRIPT_META and event["name"] == "session":
				run["runtime"]["session"] = event["value"]
			if event["type"] == Event.SCRIPT_META and event["name"] == "runtime":
				run["runtime"].update(event["value"])
			if event["type"] == Event.POLICY_REQUEST:
				effect, action = event.get("effect"), event.get("action")
				if not isinstance(action, str) or not action:
					raise AutoError("Runtime policy request requires an action")
				rule, covered = runtimeRule(context, definition, effect)
				if covered:
					answer = True
					continue
				if run["task"] == "elaborator":
					emit(path, {"type": Event.POLICY_DENIED, "effect": effect, "action": action})
					answer = False
					continue
				base = "auto:" + createPlan(context.plan).id
				ref = f"{base}/task/{taskId}/definition"
				pin = Resolve(context, ref)
				question = f"{rule.capitalize()} {effect} for action: {action}?"
				policyRequest = {"effect": effect, "action": action, "rule": rule, "scope": f"{base}/task/{taskId}"}
				gapDefinition = {"kind": GapKind.APPROVAL if rule == "confirm" else GapKind.DECISION, "question": question, "needed_by": [taskId],
					"effects": [effect], "subjects": [{"ref": ref, "digest": pin.digest}], "runtime_policy": policyRequest, "raised_by": run["id"]}
				gap = GapRaise(context, context.plan, gapDefinition)
				run.update(status=RunStatus.WAITING, gap=gap, question={"type": Event.POLICY_REQUEST, "content": question, **policyRequest})
				state["question"] = run["question"]
				context.variables["_detach"] = True
				emit(path, {"type": Event.POLICY_REQUESTED, "effect": effect, "action": action})
				emit(path, {"type": Event.GAP_RAISED, "gap": gap})
				saveRun(path, run, state)
				return None, True
			if event["type"] == Event.SCRIPT_QUESTION:
				target = run["task"] if run["task"] != "elaborator" else state.get("target", "root")
				gap = GapRaise(context, context.plan, {"kind": GapKind.INFORMATION, "question": event["content"], "needed_by": [target], "raised_by": run["id"]})
				run.update(status=RunStatus.WAITING, gap=gap, question=event)
				state["question"] = event
				context.variables["_detach"] = True
				run["usage"] = dict(context.limits.usage)
				emit(path, {"type": Event.GAP_RAISED, "gap": gap})
				saveRun(path, run, state)
				return None, True
			if event["type"] == Event.SCRIPT_ENDED:
				if event["status"] != RunStatus.SUCCEEDED:
					failure = event.get("failure", {"kind": FailureKind.SYSTEM, "reason": "Engine failed"})
					raise AutoError(failure["reason"], kindFor(event["status"], failure))
				return event["output"], False
			saveRun(path, run, state)
	finally:
		stream.close()


def _continue(context, path, run, state, answer=UNSET):
	"""Run the lifecycle phases of a prepared run, resuming at the first incomplete one."""
	task = createTaskFromDict(state["task"], Path(state["task"]["path"]))
	limits = Limits(run["budget"], dict(run["usage"]), initialTime=run["usage"].get("time", 0))
	variables = {"plan": createPlan(context.plan).id, "run": {"id": run["id"], "output": str(path / run.get("outputFile", "output.txt"))}}
	cancelled = lambda: (path / "cancel").exists()
	context = context.derive(config=state["config"], defaults=state.get("defaults", {}), inputs=state["inputs"],
		params=state.get("params", {}), variables=variables, limits=limits, evidence=run["inputs"], cancelled=cancelled)
	context = context.derive(allowShell=context.allowShell and shellPermitted(context, task))
	if context.observe:
		context.observe({"type": Event.RUN_STARTED, "task": run["task"], "budget": run["budget"], "usage": run["usage"]})
	run["status"] = RunStatus.RUNNING
	try:
		if answer is not UNSET:
			state["request"]["answer"] = Value(context, answer, context.plan)
		if "resolvedInputs" not in state:
			state["resolvedInputs"] = Renderer(context, task.script()).bindings()["inputs"]
		context.resolvedInputs = state["resolvedInputs"]
		for phase in ("Pre", "Process", "Post", "Apply", "Evaluation"):
			if phase in state["completed"]:
				continue
			if phase != "Process" and not hasCheck(task, phase):
				state["completed"].append(phase)
				continue
			limits.check()
			state["phase"] = phase
			if context.observe:
				context.observe({"type": Event.RUN_PHASE, "phase": phase})
			if phase in {"Post", "Apply", "Evaluation"}:
				context.variables["result"] = RunOutput(context, run)
				context.variables["run"]["output"] = str(path / run["outputFile"])
			request = state.get("request")
			value = None
			try:
				if request is None:
					if phase == "Process":
						if not task.sections.get("Process"):
							value = context.inputs.get("children", {})
							contract = prepare(context, task.script())["output"] if task.meta.get("output") else {"format": "json"}
							value = conform(value, contract)
						else:
							request = prepare(context, task.script())
					elif phase == "Apply":
						value = applyEffects(context, task)
					else:
						request, value = checkRequest(context, task, phase)
				if request:
					value, waiting = _drive(context, path, run, state, request)
					if waiting:
						return run
			except AutoError as error:
				if phase != "Evaluation" or error.kind in {FailureKind.TIMEOUT, FailureKind.BUDGET, FailureKind.CANCELLED}:
					raise
				run["evaluation_failure"] = {"kind": error.kind, "reason": str(error)}
				emit(path, {"type": Event.TASK_EVALUATION_FAILED, "failure": run["evaluation_failure"]})
				state.pop("request", None)
				state["completed"].append(phase)
				saveRun(path, run, state)
				continue
			state.pop("request", None)
			state["completed"].append(phase)
			if phase == "Process":
				run["outputFile"] = "output.json" if not isinstance(value, str) or task.meta.get("output", {}).get("format") == "json" else "output.txt"
				atomicWrite(path / run["outputFile"], jsonText(value) if run["outputFile"].endswith(".json") else value)
			elif phase == "Evaluation":
				run["evaluation"] = value
				emit(path, {"type": Event.TASK_EVALUATED, "evaluation": value})
			else:
				run[phase.lower()] = value
				emit(path, {"type": Event.TASK_CHECKED, "check": phase.lower(), "verdict": value})
				if not value["pass"]:
					run["status"] = RunStatus.SKIPPED if phase == "Pre" else RunStatus.FAILED
					run["failure"] = {"kind": "input" if phase == "Pre" else "system" if phase == "Apply" else "output", "reason": value["reason"]}
					break
			saveRun(path, run, state)
		limits.check()
		if run["status"] == RunStatus.RUNNING:
			run["status"] = RunStatus.SUCCEEDED
			if task.meta.get("hints", {}).get("gate") == "approval":
				graph = dependencyGraph(context, tasksAt(context.plan))
				needed = [id for id, deps in graph.items() if task.id in deps] or ["root"]
				content = Resolve(context, "run:" + run["id"])
				GapRaise(context, context.plan, {"kind": GapKind.APPROVAL, "question": f"Approve output of {task.id}?", "needed_by": needed,
					"subjects": [{"ref": content.ref, "digest": content.digest}], "raised_by": run["id"]})
	except (AutoError, KeyboardInterrupt) as error:
		if "_policyDeniedAction" in context.variables:
			emit(path, {"type": Event.POLICY_DENIED, "effect": "shell.run", "action": context.variables.pop("_policyDeniedAction")})
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
			emit(path, {"type": Event.RUN_ENDED, "status": run["status"]})
		if context.observe:
			context.observe({"type": Event.RUN_FINISHED, "run": run})
		saveRun(path, run, state)
	return run


def resumeGap(context, gap, answer):
	"""Resume the waiting run that raised a resolved gap."""
	for run in RunList(context, {"status": RunStatus.WAITING}):
		if run.get("gap") == gap:
			path = runDirectory(context, run)
			with locked(path):
				run = RunGet(context, run)
				if run["status"] == RunStatus.WAITING:
					value = Knowledge(context).get(f"{planRef(context.plan)}/gap/{gap}")
					policy = value.get("runtime_policy")
					if policy:
						decision = Knowledge(context).get(f"{planRef(context.plan)}/decision/{value['decision']}")
						answer = decision.get("answer")
						human = decision.get("author") == "human"
						approves = human and (answer is True or isinstance(answer, str) and answer.lower() in {"approve", "approved", "yes"})
						grants = human and answer == {"grant": policy["effect"], "scope": policy["scope"]}
						approved = approves if policy["rule"] == "confirm" else grants
						currentRule, _ = runtimeRule(context, TaskGet(context, value["needed_by"][0]), policy["effect"])
						grant = any(op.get("op") == RevisionOp.GRANT and op.get("source") == decision["identity"]
							and op.get("effect") == policy["effect"] and op.get("scope") == policy["scope"]
							for row in journal(context.plan) for op in row["operations"])
						try:
							pinned = all(Resolve(context, pin["ref"]).digest == pin["digest"] for pin in value.get("subjects", []))
						except AutoError:
							pinned = False
						answer = bool(approved and pinned and (currentRule != "deny" or grant))
						if not answer:
							emit(path, {"type": Event.POLICY_DENIED, "effect": policy["effect"], "action": policy["action"]})
					emit(path, {"type": Event.GAP_RESOLVED, "gap": gap})
					state = readJson(path / "state.json")
					return _continue(context, path, run, state, answer)


def _assess(context, run, check, instructions=None):
	"""Evaluate one check against a concluded run, under its budget."""
	path = runDirectory(context, run)
	with locked(path):
		receipt = RunGet(context, run)
		if receipt["status"] not in TERMINAL:
			raise AutoError("Wait for the active lifecycle before reassessing a run")
		state = readJson(path / "state.json")
		if state is None:
			raise AutoError("Run did not reach assessment")
		task = createTaskFromDict(state["task"], Path(state["task"]["path"]))
		if instructions is not None:
			task.sections[check] = instructions.get("content", "") if isinstance(instructions, dict) else instructions
		if not hasCheck(task, check):
			raise AutoError(f"Run has no {check} check")
		limits = Limits(receipt["budget"], dict(receipt["usage"]), initialTime=receipt["usage"]["time"])
		variables = {"plan": createPlan(context.plan).id, "run": {"id": path.name, "output": str(path / receipt.get("outputFile", "output.txt"))},
			"result": RunOutput(context, run) if check != "Pre" else None}
		ctx = context.derive(config=state["config"], defaults=state.get("defaults", {}), inputs=state["inputs"],
			resolvedInputs=state.get("resolvedInputs", {}), limits=limits, variables=variables)
		stream = None
		try:
			limits.check()
			request, value = checkRequest(ctx, task, check)
			if request:
				stream = executePrepared(ctx, request)
				for event in stream:
					if event["type"] == Event.SCRIPT_ENDED:
						if event["status"] != RunStatus.SUCCEEDED:
							raise AutoError(event.get("failure", {}).get("reason", "Assessment failed"), "output")
						value = event["output"]
					if event["type"] == Event.SCRIPT_QUESTION:
						raise AutoError("Standalone reassessment cannot suspend a completed run")
			limits.check()
			if check == "Evaluation":
				emit(path, {"type": Event.TASK_EVALUATED, "evaluation": value})
			else:
				emit(path, {"type": Event.TASK_CHECKED, "check": check.lower(), "verdict": value})
			return value
		finally:
			if stream:
				stream.close()
			limits.usage["time"] = limits.initialTime + time.monotonic() - limits.started
			emit(path, {"type": Event.ASSESSMENT_USAGE, "usage": {key: limits.usage[key] - receipt["usage"][key] for key in limits.usage}})




# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------


def TaskCreate(context, task: dict) -> str:
	"""Create a task through a plan revision, returning its id."""
	PlanRevise(context, context.plan, [{"op": RevisionOp.ADD, "task": task}], "Create task")
	return task["id"]


def TaskGet(context, id: str) -> Task:
	"""Read a task of the current plan by identifier."""
	path = planPath(context)
	identifier(id)
	file = path / "plan.md" if id == "root" else path / "tasks" / (id + ".md")
	if not file.exists():
		raise AutoError(f"Task not found: {id}")
	return createTaskFromText(file.read_text(), file, id == "root")


def TaskUpdate(context, id: str, changes: dict) -> Task:
	"""Update a task without runs through a plan revision."""
	task = TaskGet(context, id)
	PlanRevise(context, context.plan, [{"op": RevisionOp.EDIT, "task": merge(task.asDict(), changes)}], "Update task")
	return TaskGet(context, id)


def TaskDelete(context, id: str) -> None:
	"""Remove a task and its children, keeping its runs."""
	PlanRevise(context, context.plan, [{"op": RevisionOp.REMOVE, "task": id}], "Delete task")


def TaskList(context, filter: dict | None = None) -> list:
	"""List task definitions, filtered by id prefix or input."""
	return [task.asDict() for task in tasksAt(planPath(context)).values() if (not filter or
		(not filter.get("prefix") or task.id.startswith(filter["prefix"])) and
		(not filter.get("input") or filter["input"] in TaskDependencies(context, task)))]


def TaskValidate(context, task, graph: bool = True, aggregate: bool = False) -> list:
	"""Return the violations of a task definition."""
	violations = []
	try:
		task = effectiveTask(context, resolveTask(context, task))
		identifier(task.id)
		effects = task.meta.get("effects", [])
		if not isinstance(effects, list) or any(not isinstance(effect, str) or not EFFECT.fullmatch(effect) for effect in effects):
			raise AutoError("effects must be a list of dotted effect names")
		budgetValue(task.meta.get("budget"))
		if task.meta.get("output", {}).get("format", "markdown") not in {"markdown", "text", "json"}:
			raise AutoError("Unsupported output format")
		if task.meta.get("engine"):
			Engine(context, task.meta["engine"])
		if "Process" not in task.sections and task.id != "root" and not aggregate and task.meta.get("hints", {}).get("elaborate") != "later":
			raise AutoError("Task requires Process unless it is an aggregate or deferred")
		TaskDependencies(context, task)
		if graph and context.plan:
			tasks = tasksAt(context.plan)
			tasks[task.id] = task
			dependencyGraph(context, tasks)
	except (AutoError, ValueError, LiquidError) as error:
		violations.append({"element": "task:" + getattr(task, "id", "unknown"), "message": str(error)})
	return violations


def TaskDependencies(context, task) -> list:
	"""Return the ids of the tasks a task references."""
	task = effectiveTask(context, resolveTask(context, task))
	refs = set()
	for section in task.sections or {"Process": ""}:
		refs.update(ScriptInputs(context, task.script(section))["references"])
	if context.plan:
		prefix = "auto:" + createPlan(planPath(context)).id + "/task/"
		refs = {"task:" + ref[len(prefix):] if ref.startswith(prefix) else ref for ref in refs}
	return sorted({ref[5:].split("/", 1)[0].split("#", 1)[0] for ref in refs if ref.startswith("task:")})


def TaskExpand(context, task) -> str:
	"""Render a task process as its prompt."""
	return ScriptExpand(context, effectiveTask(context, resolveTask(context, task)).script())


def TaskReady(context, task) -> bool:
	"""Tell whether every input task has a succeeded run."""
	task = resolveTask(context, task)
	path = planPath(context)
	graph = dependencyGraph(context, tasksAt(path))
	return all(latestRun(path, dependency, True) for dependency in graph.get(task.id, TaskDependencies(context, task)))


def TaskStart(context: Context, task, inputs: dict | None = None, *, metaTask: bool = False) -> dict:
	"""Run to a terminal or Waiting boundary; each attempt gets a fresh identity."""
	plan = planPath(context)
	context = context.derive(plan=plan, evidence=[])
	with locked(plan):
		recover(context, plan)
		repairLocked(context, plan)
		if any(op["op"] == RevisionOp.ABANDON for row in journal(plan) for op in row["operations"]):
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
			runtime={"engine": context.config.get("engine", task.meta.get("engine", context.defaults.get("engine", RUNTIME_DEFAULTS["engine"]))),
				"provider": context.config.get("provider", task.meta.get("provider", context.defaults.get("provider"))),
				"model": context.config.get("model", task.meta.get("model", context.defaults.get("model")))},
			budget=budget, usage={"tokens": 0, "cost": 0, "time": 0}, inputs=context.evidence).asDict()
		policyBlocked = taskPolicyBlocked(context, task) if not metaTask else {}
		openGaps = any(task.id in gap["needed_by"] for gap in GapList(context, plan, {"status": GapState.OPEN}))
		blocked = not metaTask and (bool(policyBlocked) or not TaskReady(context, task) or openGaps)
		if blocked:
			run.update(status=RunStatus.BLOCKED, ended=timestamp())
			if policyBlocked:
				run["failure"] = {"kind": FailureKind.INPUT, "reason": f"Policy authorization required: {policyBlocked}"}
			saveRun(path, run)
			return run
		children = {id: RunOutput(context, latestRun(plan, id, True)) for id, child in tasksAt(plan).items()
			if id != "root" and child.meta.get("parent", "root") == task.id}
		if task.meta.get("hints", {}).get("elaborate") == "later" and not task.sections.get("Process") and not children:
			run.update(status=RunStatus.BLOCKED, ended=timestamp(), failure={"kind": FailureKind.INPUT, "reason": "Deferred task must be decomposed before execution"})
			saveRun(path, run)
			return run
		run["children"] = {id: latestRun(plan, id, True)["id"] for id in children}
		for id in children:
			Resolve(context, "task:" + id)
		state = {"task": task.asDict(), "config": context.config, "defaults": context.defaults,
			"inputs": merge(context.inputs, inputs or {}, {"children": children} if children else {}),
			"params": context.params, "completed": [], "target": context.variables.get("target", "root"),
			"applyElaboration": context.variables.get("applyElaboration", False)}
		# The harness and shell checks need the same Auto entry point and plan context.
		state["config"] = merge(state["config"], {"env": {"AUTO_PLAN": str(plan)}})
		run["status"] = RunStatus.RUNNING
		saveRun(path, run, state)
	with locked(path):
		return _continue(context, path, run, state)


def RunGet(context, run) -> dict:
	"""Read a run receipt, folding in assessment usage."""
	directory = runDirectory(context, run)
	value = readReceipt(directory)
	if value is None:
		raise AutoError(f"Run not found: {directory.name}")
	if (directory / "receipt.json").exists():
		for line in (directory / "journal.jsonl").read_text().splitlines() if (directory / "journal.jsonl").exists() else []:
			event = json.loads(line)
			if event["type"] == Event.ASSESSMENT_USAGE:
				for key, amount in event["usage"].items():
					value["usage"][key] = value["usage"].get(key, 0) + amount
	return createRunFromDict(value).asDict()


def RunList(context, filter: dict | None = None) -> list:
	"""List runs of the current plan, filtered by field or date."""
	result = [RunGet(context, directory.name) for directory in runDirectories(planPath(context))]
	return [run for run in result if all((run["started"].startswith(value) if key == "date" else run.get(key) == value) for key, value in (filter or {}).items())]


def RunJournal(context, run, since: int | None = None):
	"""Stream a run journal until it reaches a terminal status."""
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


def RunOutput(context, run):
	"""Read the stored output of a concluded run."""
	value = RunGet(context, run)
	file = runDirectory(context, run) / value.get("outputFile", "output.txt")
	if not file.exists():
		raise AutoError("Run has no output")
	text = file.read_text()
	return json.loads(text) if file.suffix == ".json" else text


def RunAnswer(context, run, answer, rationale: str):
	"""Answer the gap of a waiting run with a decision."""
	value = RunGet(context, run)
	if value["status"] != RunStatus.WAITING:
		raise AutoError("Run is not waiting")
	return GapDecide(context, value["gap"], answer, rationale)


def RunCancel(context, run) -> dict:
	"""Cancel an active run, ending it as `Cancelled`."""
	value = RunGet(context, run)
	if value["status"] in TERMINAL:
		return value
	path = runDirectory(context, run)
	atomicWrite(path / "cancel", "cancel\n")
	with locked(path):
		value = RunGet(context, run)
		if value["status"] not in TERMINAL:
			state = readJson(path / "state.json")
			engine = Engine(context, value["runtime"]["engine"])
			if isinstance(engine, CancellableEngine) and state.get("request"):
				engine.cancel(context, state["request"])
			value.update(status=RunStatus.CANCELLED, ended=timestamp(), failure={"kind": FailureKind.CANCELLED, "reason": "Cancelled by caller"})
			emit(path, {"type": Event.RUN_ENDED, "status": RunStatus.CANCELLED})
			saveRun(path, value)
	return RunGet(context, run)


def RunCheck(context, run, check: str | dict):
	"""Evaluate a named check or a supplied TaskCheck against the stored run."""
	if isinstance(check, dict):
		return _assess(context, run, "Post", check)
	return _assess(context, run, check.title(), None)


def RunEvaluate(context, run):
	"""Evaluate the output of a concluded run."""
	return _assess(context, run, "Evaluation")

# EOF
