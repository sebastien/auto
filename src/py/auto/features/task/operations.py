"""Module: operations — Task contracts, persisted runs and resumable lifecycle."""

from __future__ import annotations

import json
import re
import time
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path

from liquid.exceptions import LiquidError

# Plan tasks use plan storage and policy; a supplied run scope reuses the lifecycle for agent tasks.
from auto.features.plan.gaps import GapDecide, GapList, GapRaise, applyPending, planRef, repairLocked
from auto.features.plan.gates import taskGapState
from auto.features.plan.control import planHold
from auto.features.plan.model import GapKind, GapState, RevisionOp, createPlan
from auto.features.plan.operations import PlanRevise, dependencyGraph, tasksAt
from auto.features.plan.policy import EFFECT, PolicyResolve, runtimeRule, shellPermitted, taskPolicyBlocked
from auto.features.plan.references import latestRun, readReceipt, runDirectories
from auto.features.plan.store import exclusive, journal, locked, planPath, recover
from auto.features.script.config import commands
from auto.features.script.model import Script
from auto.features.script.operations import ScriptExpand, ScriptInputs, conform, executePrepared, prepare
from auto.features.script.templates import Renderer
from auto.features.script.sources import ScriptSources
from auto.features.task.model import Run, Task, canonicalTask, createRunFromDict, createTaskFromDict, createTaskFromText
from auto.features.task.store import emit, runDirectory, saveRun
from auto.runtime.config import RUNTIME_DEFAULTS, validateConfig
from auto.runtime.integration import CancellableEngine
from auto.runtime.model import Context, Event, FailureKind, Limits, RunStatus, TERMINAL, budgetValue, failureFor, kindFor, statusFor
from auto.runtime.operations import Engine, Knowledge, Resolve, Shell, Value
from auto.utils.data import AutoError, atomicWrite, document, identifier, identity, jsonText, merge, readJson, timestamp
from auto.utils.references import relocate
from auto.utils.layout import runsPath, taskFile, taskId, taskStem

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
	if meta.get("budget") is None:
		meta.pop("budget", None)
	for name in ("profile", "artifact"):
		if name in meta and meta[name] is not None and not isinstance(meta[name], str):
			raise AutoError(f"Task {name} must be a reference string")
	if "each" in meta:
		raise AutoError("each is only supported by scripts: a task run is a single item")
	sources = ScriptSources(context, meta.get("_template_sources"))
	if meta.get("profile"):
		content = sources.resolve(meta["profile"], task.path.parent)
		profile, _ = document(content.text)
		if task.projectDefaults is not None:
			profile = task.projectDefaults(profile)
		meta = merge({key: value for key, value in profile.items() if key in {"engine", "provider", "model", "agent", "temperature", "budget", "memory"}}, meta)
	if meta.get("artifact"):
		original = sources.record(meta["artifact"], task.path.parent)
		ref = original.get("compiled_artifact") if original else None
		if ref:
			if not isinstance(ref, str):
				raise AutoError("Compiled artifact source must be a reference string")
			compiled = sources.record(ref, task.path.parent)
			if not re.fullmatch(r"auto:[^/#]+/task/[^/#]+/source/[a-f0-9]{64}", ref) or not compiled or compiled["ref"] != ref or compiled.get("source") != {"ref": original["ref"], "digest": original["digest"]}:
				raise AutoError("Compiled artifact source does not match the selected artifact")
		content = sources.resolve(ref or meta["artifact"], task.path.parent)
		artifact = createTaskFromText(content.text, content.path)
		validateConfig({key: value for key, value in artifact.meta.items() if key in {"post", "eval"}})
		if artifact.meta.get("_template_bindings"):
			meta["_template_bindings"] = merge(artifact.meta["_template_bindings"], meta.get("_template_bindings", {}))
		contract = dict(artifact.meta.get("output", {}))
		for key, value in contract.items():
			if isinstance(value, str) and value.startswith(("./", "../")):
				contract[key] = str((content.path.parent / value).resolve())
		meta["output"] = merge(contract, meta.get("output", {}))
		for key in ("post", "eval"):
			if key in artifact.meta:
				validateConfig({key: meta[key]} if key in meta else {})
				meta[key] = commands(artifact.meta[key]) + commands(meta.get(key))
		for name in ("Post", "Evaluation"):
			if name in artifact.sections:
				# Keep the included fragment's relative references attached to its origin.
				text = relocate(artifact.sections[name], content.path.parent)
				parts[name] = text + "\n" + parts.get(name, "")
	return replace(task, meta=meta, sections=parts)


def resolvedInputs(state: dict) -> dict:
	"""Return a run's resolved inputs, stored as their changes to its inputs."""
	return {**state.get("inputs", {}), **state.get("resolvedInputs", {})} if "resolvedInputs" in state else {}


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
	checks = []
	renderer = Renderer(context, task.script(check), checks=checks)
	namespace = renderer.bindings()
	if check != "Pre":
		namespace["output"] = context.variables.get("result")
	text = renderer.render(namespace)
	shells = [renderer.shellArgument(command, namespace) for command in commands(task.meta.get(LIFECYCLE[check]))] + checks
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
	if not text.strip():
		return None, last or ({"score": 1, "feedback": "No evaluation"} if check == "Evaluation" else {"pass": True, "reason": "No check"})
	meta = {**task.meta, "output": {"format": "json", "schema": EVALUATION if check == "Evaluation" else VERDICT}}
	# Already-expanded prose is supplied as data, so transclusions cannot execute twice.
	content = "{{ instructions }}\n\n{{ evidence }}\n\nTask inputs: {{ inputs | json }}\nRun output: {{ result | json }}"
	ctx = context.derive(variables={**context.variables, "instructions": text, "evidence": evidence, "result": context.variables.get("result")})
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


def executionPolicy(context, run, state):
	"""Resolve the same execution permissions for lifecycle and reassessment."""
	if context.runScope is not None:
		return context.runScope.policy(context, run, state)
	if context.policyDefaults is not None:
		context = context.derive(defaults=context.policyDefaults)
	taskId = run["task"] if run["task"] != "elaborator" else state.get("target", "root")
	ref = "auto:" + createPlan(context.plan).id + "/task/" + taskId + "/definition"
	content = Resolve(context.derive(allowShell=False, variables=dict(context.variables)), ref)
	definition = createTaskFromText(content.text, content.path, taskId == "root")
	policy = PolicyResolve(context, context.plan, definition)
	if run["task"] == "elaborator":
		definition = createTaskFromDict({**state["task"], "id": "elaborator", "effects": []}, Path(state["task"]["path"]))
		base = "auto:" + createPlan(context.plan).id
		policy["grants"] = [grant for grant in policy["grants"] if grant["scope"] == base]
	policy["effects"] = definition.meta.get("effects", [])
	policy["blocked"] = taskPolicyBlocked(context, definition)
	return definition, policy


def _drive(context, path, run, state, request, answer=None):
	"""Drive one engine stream, persisting usage and raising policy gaps."""
	definition, request["policy"] = executionPolicy(context, run, state)
	request = context.runtime.prepareRunRequest(run, state, request)
	taskId = run["task"] if run["task"] != "elaborator" else state.get("target", "root")
	state["request"] = request
	run["runtime"].update({key: request["config"][key] for key in ("engine", "model") if key in request["config"]})
	saveRun(context, path, run, state)
	kind = "memory" if context.runScope is not None else "elaboration" if run["task"] == "elaborator" else "task"
	stream = executePrepared(context, request, {"kind": kind, "phase": state.get("phase"), "attempt": state.get("repairs", 0), "run": run["id"], "task": taskId})
	journaled = {}
	try:
		while True:
			event = stream.send(answer)
			answer = None
			# Engines repeat metadata (eg. usage after each step): journal only changes.
			repeated = event["type"] == Event.SCRIPT_META and journaled.get(event.get("name"), journaled) == event.get("value")
			if event["type"] == Event.SCRIPT_META:
				journaled[event.get("name")] = event.get("value")
			if event["type"] != Event.POLICY_REQUEST and not repeated:
				emit(path, event)
			if context.observe:
				observed = {**event, "value": dict(context.limits.usage)} if event.get("type") == Event.SCRIPT_META and event.get("name") == "usage" else event
				context.observe(observed)
			run["usage"] = dict(context.limits.usage)
			if event["type"] == Event.SCRIPT_META:
				context.runtime.recordRunMetadata(run, event)
			if event["type"] == Event.SCRIPT_META and event["name"] == "session":
				run["runtime"]["session"] = event["value"]
			if event["type"] == Event.SCRIPT_META and event["name"] == "runtime":
				run["runtime"].update(event["value"])
			if event["type"] == Event.POLICY_REQUEST:
				effect, action = event.get("effect"), event.get("action")
				if not isinstance(action, str) or not action:
					raise AutoError("Runtime policy request requires an action")
				if context.runScope is not None:
					answer = context.runScope.authorizeRequest(context, effect)
					if not answer:
						emit(path, {"type": Event.POLICY_DENIED, "effect": effect, "action": action})
					continue
				policyContext = context.derive(defaults=context.policyDefaults) if context.policyDefaults is not None else context
				rule, covered = runtimeRule(policyContext, definition, effect)
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
				saveRun(context, path, run, state)
				return None, True
			if event["type"] == Event.SCRIPT_QUESTION:
				if context.runScope is not None:
					raise AutoError("An agent-scoped run cannot suspend for a human answer; record a question in its report", "output")
				target = run["task"] if run["task"] != "elaborator" else state.get("target", "root")
				gap = GapRaise(context, context.plan, {"kind": GapKind.INFORMATION, "question": event["content"], "needed_by": [target], "raised_by": run["id"],
					**({"fields": event["fields"]} if event.get("fields") else {})})
				run.update(status=RunStatus.WAITING, gap=gap, question=event)
				state["question"] = event
				context.variables["_detach"] = True
				run["usage"] = dict(context.limits.usage)
				emit(path, {"type": Event.GAP_RAISED, "gap": gap})
				saveRun(context, path, run, state)
				return None, True
			if event["type"] == Event.SCRIPT_ENDED:
				if event["status"] != RunStatus.SUCCEEDED:
					failure = event.get("failure", {"kind": FailureKind.SYSTEM, "reason": "Engine failed"})
					raise AutoError(failure["reason"], kindFor(event["status"], failure))
				return event["output"], False
			saveRun(context, path, run, state)
	finally:
		stream.close()


def _continue(context, path, run, state, answer=UNSET, *, admitted=False):
	"""Run the lifecycle phases of a prepared run, resuming at the first incomplete one."""
	task = createTaskFromDict(state["task"], Path(state["task"]["path"]))
	if not admitted and context.runScope is None:
		with locked(context.plan):
			run["budget"] = context.runtime.planRunBudget(task, run["budget"], run)
			run["status"] = RunStatus.RUNNING
			saveRun(context, path, run, state)
	if context.limits:
		for key, amount in context.limits.budget.items():
			cap = run["usage"].get(key, 0) + max(0, amount - context.limits.usage.get(key, 0))
			run["budget"][key] = min(run["budget"].get(key, cap), cap)
	limits = Limits(run["budget"], dict(run["usage"]), initialTime=run["usage"].get("time", 0))
	variables = context.runScope.variables(context, path, run) if context.runScope is not None else {"plan": createPlan(context.plan).id}
	variables["run"] = {"id": run["id"], "output": str(path / run.get("outputFile", "output.txt"))}
	callerCancelled = context.cancelled
	cancelled = lambda: (path / "cancel").exists() or callerCancelled()
	context = context.derive(config=state["config"], policyDefaults=context.policyDefaults if context.policyDefaults is not None else context.defaults,
		defaults=state.get("defaults", {}), inputs=state["inputs"],
		params=state.get("params", {}), variables=variables, limits=limits, evidence=run["inputs"], cancelled=cancelled)
	if context.runScope is None:
		context = context.derive(allowShell=context.allowShell and shellPermitted(context.derive(defaults=context.policyDefaults), task))
	if context.observe:
		context.observe({"type": Event.RUN_STARTED, "task": run["task"], "budget": run["budget"], "usage": run["usage"]})
	run["status"] = RunStatus.RUNNING
	try:
		if context.cancelled():
			raise AutoError("Run cancelled", FailureKind.CANCELLED)
		if "memory" not in state:
			state["memory"] = context.runtime.prepareRun(run, state)
			context = context.derive(inputs=state["inputs"])
			saveRun(context, path, run, state)
		if answer is not UNSET:
			state["request"]["answer"] = Value(context, answer, context.plan)
		if "resolvedInputs" not in state:
			# Keep only what resolution changed: the inputs are already in the state.
			resolved = Renderer(context, task.script()).bindings()["inputs"]
			state["resolvedInputs"] = {key: value for key, value in resolved.items() if key not in state["inputs"] or state["inputs"][key] != value}
		context.resolvedInputs = resolvedInputs(state)
		phases = ("Pre", "Process", "Post", "Apply", "Evaluation")
		index = 0
		while index < len(phases):
			phase = phases[index]
			index += 1
			if context.cancelled():
				raise AutoError("Run cancelled", FailureKind.CANCELLED)
			if phase in state["completed"]:
				continue
			if phase == "Evaluation" and context.runScope is not None and not state.get("scopePost"):
				context.runScope.check(context, path, run, state)
				state["scopePost"] = True
				saveRun(context, path, run, state)
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
							request["prompt"] = decisionsPrompt(state.get("decisions")) + request["prompt"] + repairPrompt(state.get("repair"))
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
				saveRun(context, path, run, state)
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
				if not value["pass"] and phase == "Post" and state.get("repairs", 0) < repairLimit(task):
					# A corrective attempt: run the process again with the rejected output and its issues.
					state["repairs"] = state.get("repairs", 0) + 1
					state["repair"] = {"output": outputText(context.variables.get("result")), "reason": value["reason"]}
					state["completed"] = [item for item in state["completed"] if item not in {"Process", "Post"}]
					emit(path, {"type": Event.TASK_REPAIRING, "attempt": state["repairs"], "reason": value["reason"]})
					saveRun(context, path, run, state)
					index = phases.index("Process")
					continue
				if not value["pass"]:
					run["status"] = RunStatus.SKIPPED if phase == "Pre" else RunStatus.FAILED
					run["failure"] = {"kind": "input" if phase == "Pre" else "system" if phase == "Apply" else "output", "reason": value["reason"]}
					break
			saveRun(context, path, run, state)
		limits.check()
		if run["status"] == RunStatus.RUNNING:
			if context.cancelled():
				raise AutoError("Run cancelled", FailureKind.CANCELLED)
			if context.runScope is not None:
				context.runScope.finish(context, path, run, state)
				limits.check()
			run["status"] = RunStatus.SUCCEEDED
			if context.runScope is None and task.meta.get("hints", {}).get("gate") == "approval":
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
		saveRun(context, path, run, state)
	return run


def RunExecute(context, path, run, state):
	"""Execute a prepared run using its scope's storage and application contract."""
	with locked(path):
		return _continue(context, path, run, state)


def reconcileRunWorkers(context, plan):
	"""Fail interrupted plan workers only after their execution lock is free."""
	context = context.derive(plan=plan, runScope=None)
	repaired = []
	for path in runDirectories(plan):
		run = readReceipt(path)
		if run["status"] not in {RunStatus.PENDING, RunStatus.RUNNING} and not (path / "assessment.json").exists():
			continue
		with ExitStack() as owner:
			try:
				owner.enter_context(exclusive(path / ".lock", "This run"))
			except AutoError:
				continue
			with locked(plan):
				run = readReceipt(path)
				assessment = readJson(path / "assessment.json")
				if assessment:
					finishAssessment(context, path, assessment, interrupted=True)
				if run["status"] not in {RunStatus.PENDING, RunStatus.RUNNING}:
					continue
				run.update(status=RunStatus.FAILED, ended=timestamp(),
					failure={"kind": FailureKind.SYSTEM, "reason": "Lifecycle worker was interrupted before a terminal receipt"})
				emit(path, {"type": Event.RUN_ENDED, "status": RunStatus.FAILED})
				saveRun(context, path, run)
				repaired.append(run)
	return repaired


def assessmentProgress(context, path, assessment, usage):
	"""Checkpoint cumulative measured usage while holding the run owner lock."""
	with locked(context.plan):
		assessment["usage"] = {key: max(amount, usage.get(key, amount)) for key, amount in assessment["usage"].items()}
		atomicWrite(path / "assessment.json", jsonText(assessment))


def finishAssessment(context, path, assessment, *, interrupted=False):
	"""Charge one assessment once; a terminal receipt is never rewritten."""
	with locked(context.plan):
		events = [json.loads(line) for line in (path / "journal.jsonl").read_text().splitlines()] if (path / "journal.jsonl").exists() else []
		if not any(event.get("assessment") == assessment["id"] and event["type"] == Event.ASSESSMENT_USAGE for event in events):
			emit(path, {"type": Event.ASSESSMENT_USAGE, "assessment": assessment["id"],
				"usage": {key: max(0, amount - assessment["base_usage"].get(key, 0)) for key, amount in assessment["usage"].items()},
				**({"interrupted": True} if interrupted else {})})
		(path / "assessment.json").unlink(missing_ok=True)


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
						currentRule = runtimeRule(context, TaskGet(context, value["needed_by"][0]), policy["effect"])[0] if value.get("needed_by") else "deny"
						grant = any(op.get("op") == RevisionOp.GRANT and op.get("source") == decision["identity"]
							and op.get("effect") == policy["effect"] and op.get("scope") == policy["scope"]
							for row in journal(context.plan) for op in row["operations"])
						try:
							pinned = all(Resolve(context, pin["ref"]).digest == pin["digest"] for pin in value.get("subjects", []))
						except AutoError:
							pinned = False
						answer = bool(value.get("needed_by") and approved and pinned and (currentRule != "deny" or grant))
						if not answer:
							emit(path, {"type": Event.POLICY_DENIED, "effect": policy["effect"], "action": policy["action"]})
					emit(path, {"type": Event.GAP_RESOLVED, "gap": gap})
					state = readJson(path / "state.json")
					return _continue(context, path, run, state, answer)


def _assess(context, run, check, instructions=None):
	"""Evaluate one check against a concluded run, under its budget."""
	context = context.derive(plan=planPath(context))
	reconcileRunWorkers(context, context.plan)
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
		ctx = context.derive(config=state["config"], policyDefaults=context.policyDefaults if context.policyDefaults is not None else context.defaults,
			defaults=state.get("defaults", {}), inputs=state["inputs"],
			resolvedInputs=resolvedInputs(state), limits=limits, variables=variables)
		# Rendering keeps the saved defaults; authorization uses current configuration.
		policyContext = ctx.derive(defaults=context.defaults)
		definition, policy = executionPolicy(policyContext, receipt, state)
		ctx = ctx.derive(allowShell=ctx.allowShell and shellPermitted(policyContext, definition))
		with locked(context.plan):
			limits.budget = context.runtime.planRunBudget(task, receipt["budget"], receipt, assessment=True)
			assessment = {"id": identity("asm"), "budget": limits.budget, "base_usage": dict(receipt["usage"]), "usage": dict(receipt["usage"])}
			atomicWrite(path / "assessment.json", jsonText(assessment))
		stream = None
		try:
			limits.check()
			request, value = checkRequest(ctx, task, check)
			if request:
				request["policy"] = policy
				request = ctx.runtime.prepareRunRequest(receipt, state, request)
				stream = executePrepared(ctx, request, {"kind": "assessment", "phase": check, "attempt": 0, "run": path.name, "task": task.id, "check": check})
				answer = None
				while True:
					event = stream.send(answer)
					assessmentProgress(context, path, assessment, limits.usage)
					answer = None
					if event["type"] == Event.POLICY_REQUEST:
						effect, action = event.get("effect"), event.get("action")
						if not isinstance(action, str) or not action:
							raise AutoError("Runtime policy request requires an action")
						_, covered = runtimeRule(policyContext, definition, effect)
						if not covered:
							emit(path, {"type": Event.POLICY_DENIED, "effect": effect, "action": action})
							raise AutoError(f"Reassessment requires authorization for {effect}; a completed run cannot wait for approval", "input")
						answer = True
					if event["type"] == Event.SCRIPT_ENDED:
						if event["status"] != RunStatus.SUCCEEDED:
							raise AutoError(event.get("failure", {}).get("reason", "Assessment failed"), kindFor(event["status"], event.get("failure")))
						value = event["output"]
						break
					if event["type"] == Event.SCRIPT_QUESTION:
						raise AutoError("Standalone reassessment cannot suspend a completed run")
			limits.check()
			if check == "Evaluation":
				emit(path, {"type": Event.TASK_EVALUATED, "evaluation": value})
			else:
				emit(path, {"type": Event.TASK_CHECKED, "check": check.lower(), "verdict": value})
			return value
		except AutoError:
			if "_policyDeniedAction" in ctx.variables:
				emit(path, {"type": Event.POLICY_DENIED, "effect": "shell.run", "action": ctx.variables.pop("_policyDeniedAction")})
			raise
		finally:
			try:
				if stream:
					stream.close()
			finally:
				limits.usage["time"] = limits.initialTime + time.monotonic() - limits.started
				assessmentProgress(context, path, assessment, limits.usage)
				finishAssessment(context, path, assessment)




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
	id = taskId(identifier(id))
	file = taskFile(path, id)
	if not file.exists():
		raise AutoError(f"Task not found: {id}")
	task = createTaskFromText(file.read_text(), file, id == "root")
	return task if id == "root" else canonicalTask(task)


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
		definition = resolveTask(context, task)
		task = effectiveTask(context, definition)
		identifier(task.id)
		hints = task.meta.get("hints", {})
		if not isinstance(hints, dict):
			raise AutoError("Task hints must be a mapping")
		effects = task.meta.get("effects", [])
		if not isinstance(effects, list) or any(not isinstance(effect, str) or not EFFECT.fullmatch(effect) for effect in effects):
			raise AutoError("effects must be a list of dotted effect names")
		budgetValue(task.meta.get("budget"))
		if task.meta.get("output", {}).get("format", "markdown") not in {"markdown", "text", "json"}:
			raise AutoError("Unsupported output format")
		if task.meta.get("engine"):
			Engine(context, task.meta["engine"])
		if "Process" not in task.sections and task.id != "root" and not aggregate and hints.get("elaborate") != "later":
			raise AutoError("Task requires Process unless it is an aggregate or deferred")
		TaskReferences(context, definition, expandDefinitions=False)
		if graph and context.plan:
			tasks = tasksAt(context.plan)
			tasks[task.id] = definition
			dependencyGraph(context, tasks)
	except (AutoError, ValueError, LiquidError) as error:
		violations.append({"element": "task:" + getattr(task, "id", "unknown"), "message": str(error)})
	return violations


def TaskReferences(context, task, *, definitions=None, expandDefinitions=True) -> dict:
	"""Separate output dependencies from task-definition assets."""
	task = effectiveTask(context, resolveTask(context, task))
	refs = set()
	for section in task.sections or {"Process": ""}:
		refs.update(ScriptInputs(context, task.script(section), definitions=definitions, expandDefinitions=expandDefinitions)["references"])
	if context.plan:
		prefix = "auto:" + createPlan(planPath(context)).id + "/task/"
		refs = {"task:" + ref[len(prefix):] if ref.startswith(prefix) else ref for ref in refs}
	return {"dependencies": sorted({taskId(ref[5:].split("#", 1)[0]) for ref in refs if re.fullmatch(r"task:[^/#]+(?:#.*)?", ref)}),
		"definitions": sorted(ref for ref in refs if re.fullmatch(r"(?:task:|auto:[^/#]+/task/)[^/#]+/definition(?:#.*)?", ref))}


def TaskDependencies(context, task) -> list:
	"""Return same-plan tasks whose succeeded outputs are required."""
	return TaskReferences(context, task)["dependencies"]


def TaskExpand(context, task) -> str:
	"""Render a task process as its prompt."""
	return ScriptExpand(context, effectiveTask(context, resolveTask(context, task)).script())


def TaskReady(context, task) -> bool:
	"""Tell whether every input task has a succeeded run."""
	task = resolveTask(context, task)
	path = planPath(context)
	graph = dependencyGraph(context, tasksAt(path), taskInputs={task.id: context.inputs})
	dependencies = graph[task.id] if task.id in graph else TaskDependencies(context, task)
	return all(latestRun(path, dependency, True) for dependency in dependencies)


def lineage(tasks: dict, task: str) -> list:
	"""Return a task and its ancestors, up to `root`."""
	result = []
	while task and task not in result:
		result.append(task)
		task = None if task == "root" else tasks[task].meta.get("parent", "root") if task in tasks else "root"
	return result


def gapDecisions(context, tasks: list, gaps: list) -> list:
	"""Return the answers of the resolved gaps needed by any of `tasks`, approvals excepted."""
	result = []
	for gap in gaps:
		if gap["status"] == GapState.RESOLVED and gap.get("decision") and set(tasks) & set(gap["needed_by"]) and gap["kind"] != GapKind.APPROVAL:
			decision = Knowledge(context).get(f"{planRef(context.plan)}/decision/{gap['decision']}")
			result.append({"gap": gap["id"], "decision": gap["decision"], "question": gap.get("question", ""),
				"answer": decision.get("answer"), "rationale": decision.get("rationale", "")})
	return result


def repairLimit(task) -> int:
	"""Corrective attempts a task allows after a failed Post (`repair`, default 0)."""
	value = task.meta.get("repair", 0)
	if type(value) is not int or value < 0:
		raise AutoError("repair must be a non-negative integer")
	return value


def repairPrompt(repair: dict | None) -> str:
	"""Ask for a corrected output, given the rejected one and the issues found."""
	if not repair:
		return ""
	# Plain markers rather than a code fence, which models tend to copy into their reply.
	return ("\n\n## Correction\n\nYour previous output was rejected by the checks of this task. Reply with"
		" a complete, corrected output in the same format, addressing every issue below. Reply with the"
		" output itself: do not wrap it in a code block or repeat the markers below.\n\n### Issues\n\n"
		+ str(repair["reason"]).strip() + "\n\n### Rejected output\n\n<<<REJECTED\n" + repair["output"] + "\nREJECTED>>>\n")


def decisionsPrompt(decisions: list | None) -> str:
	"""Render the answers of the gaps a task needed, ahead of its process."""
	if not decisions:
		return ""
	text = "## Decisions\n\nThe following questions were answered for this task. Follow these answers.\n"
	for item in decisions:
		answer = item["answer"] if isinstance(item["answer"], str) else jsonText(item["answer"]).strip()
		text += f"\n- Question: {' '.join(str(item['question']).split())}\n  Answer: {answer}\n"
		if item["rationale"]:
			text += f"  Rationale: {' '.join(str(item['rationale']).split())}\n"
	return text + "\n"


def TaskStart(context: Context, task, inputs: dict | None = None, *, metaTask: bool = False) -> dict:
	"""Run to a terminal or Waiting boundary; each attempt gets a fresh identity."""
	if inputs is not None:
		validateConfig({"inputs": inputs})
	runInputs = merge(context.inputs, inputs or {})
	plan = planPath(context)
	context = context.derive(plan=plan, evidence=[])
	reconcileRunWorkers(context, plan)
	with ExitStack() as execution:
		with locked(plan):
			recover(context, plan)
			repairLocked(context, plan)
			if any(op["op"] == RevisionOp.ABANDON for row in journal(plan) for op in row["operations"]):
				raise AutoError("Cannot start a run in an abandoned plan")
			if planHold(plan):
				raise AutoError("Cannot start a new run while the plan is held")
			if not metaTask and isinstance(task, (Task, dict)):
				# Ordinary runs select the stored plan definition, even when their
				# caller retained an older model or mapping of that definition.
				task = task.id if isinstance(task, Task) else task["id"]
			task = effectiveTask(context, resolveTask(context, task))
			Resolve(context.derive(allowShell=False, variables=dict(context.variables)), str(task.path) if metaTask else "task:" + task.id + "/definition")
			previous = latestRun(plan, task.id)
			if previous and previous["status"] in {RunStatus.PENDING, RunStatus.RUNNING, RunStatus.WAITING}:
				raise AutoError("Task already has an active run")
			stem = "run-" + taskStem(task.id) + "-" + time.strftime("%Y%m%dT%H%M", time.gmtime())
			counter = 1
			while (runsPath(plan) / f"{stem}-{counter:02}").exists():
				counter += 1
			path = runsPath(plan) / f"{stem}-{counter:02}"
			path.mkdir(parents=True)
			execution.enter_context(locked(path))
			budget = budgetValue(task.meta.get("budget"))
			if context.limits:
				for key, amount in context.limits.budget.items():
					remaining = max(0, amount - context.limits.usage.get(key, 0))
					budget[key] = min(budget.get(key, remaining), remaining)
			run = Run(id=path.name, task=task.id, status=RunStatus.PENDING, started=timestamp(),
				runtime={"engine": context.config.get("engine", task.meta.get("engine", context.defaults.get("engine", RUNTIME_DEFAULTS["engine"]))),
					"provider": context.config.get("provider", task.meta.get("provider", context.defaults.get("provider"))),
					"model": context.config.get("model", task.meta.get("model", context.defaults.get("model")))},
				budget=budget, usage={"tokens": 0, "cost": 0, "time": 0}, inputs=context.evidence,
				extra={"author": context.author, **({"continuation": context.variables["continuation"]} if context.variables.get("continuation") else {})}).asDict()
			policyBlocked = taskPolicyBlocked(context, task) if not metaTask else {}
			tasks = tasksAt(plan)
			graph = dependencyGraph(context, tasks, taskInputs={task.id: runInputs})
			# Answers reach a task, the decomposition of its ancestors, and Elaborator runs through their target
			subject = context.variables.get("target") if metaTask else task.id
			gaps = GapList(context, plan) if subject else []
			gapState = taskGapState(context, task.id, tasks, graph.get(task.id, ()), gaps, starting=True) if not metaTask else None
			blocked = not metaTask and (bool(policyBlocked) or any(not latestRun(plan, dependency, True) for dependency in graph[task.id]) or gapState)
			if blocked:
				run.update(status=RunStatus.BLOCKED, ended=timestamp())
				if policyBlocked:
					run["failure"] = {"kind": FailureKind.INPUT, "reason": f"Policy authorization required: {policyBlocked}"}
				saveRun(context, path, run)
				return run
			children = {id: RunOutput(context, latestRun(plan, id, True)) for id, child in tasksAt(plan).items()
				if id != "root" and child.meta.get("parent", "root") == task.id}
			if task.meta.get("hints", {}).get("elaborate") == "later" and not task.sections.get("Process") and not children:
				run.update(status=RunStatus.BLOCKED, ended=timestamp(), failure={"kind": FailureKind.INPUT, "reason": "Deferred task must be decomposed before execution"})
				saveRun(context, path, run)
				return run
			run["children"] = {id: latestRun(plan, id, True)["id"] for id in children}
			for id in children:
				Resolve(context, "task:" + id)
			run["budget"] = context.runtime.planRunBudget(task, budget)
			state = {"task": task.asDict(), "config": context.config, "defaults": context.defaults,
				"inputs": merge(runInputs, {"children": children} if children else {}),
				"params": context.params, "completed": [], "target": context.variables.get("target", "root"),
				"applyElaboration": context.variables.get("applyElaboration", False)}
			decisions = gapDecisions(context, lineage(tasks, subject), gaps) if subject else []
			if decisions:
				state["decisions"] = decisions
			# The harness and shell checks need the same Auto entry point and plan context.
			state["config"] = merge(state["config"], {"env": {"AUTO_PLAN": str(plan)}})
			run["status"] = RunStatus.RUNNING
			saveRun(context, path, run, state)
		return _continue(context, path, run, state, admitted=True)


def RunGet(context, run) -> dict:
	"""Read a run receipt, folding in assessment usage."""
	directory = runDirectory(context, run)
	value = readReceipt(directory)
	if value is None:
		raise AutoError(f"Run not found: {directory.name}")
	if (directory / "receipt.json").exists():
		# Completion appends the charge before deleting this checkpoint. Read it
		# first so a concurrent handoff cannot hide or double-count measured usage.
		assessment = readJson(directory / "assessment.json")
		completed = set()
		for line in (directory / "journal.jsonl").read_text().splitlines() if (directory / "journal.jsonl").exists() else []:
			event = json.loads(line)
			if event["type"] == Event.ASSESSMENT_USAGE:
				if event.get("assessment") in completed:
					continue
				if event.get("assessment"):
					completed.add(event["assessment"])
				for key, amount in event["usage"].items():
					value["usage"][key] = value["usage"].get(key, 0) + amount
		if assessment and assessment["id"] not in completed:
			for key, amount in assessment["usage"].items():
				value["usage"][key] = value["usage"].get(key, 0) + max(0, amount - assessment["base_usage"].get(key, 0))
	return createRunFromDict(value).asDict()


def RunList(context, filter: dict | None = None) -> list:
	"""List runs of the current plan, filtered by field or date."""
	result = [RunGet(context, directory.name) for directory in runDirectories(planPath(context))]
	filter = {key: taskId(value) if key == "task" and value != "elaborator" else value for key, value in (filter or {}).items()}
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
	decision = GapDecide(context, value["gap"], answer, rationale)
	result = resumeGap(context, value["gap"], answer)
	if result:
		applyPending(context, result)
	return decision


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
			saveRun(context, path, value)
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
