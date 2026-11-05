# Task

## Purpose

We want to define the conceptual model, format and operations for a *task*.
Where a script is standalone and ephemeral (run it, get events and an output), a task
wraps a script into an identified unit of work that can be referenced, is
constrained and assessed, and can be part of a plan. Tasks need to support:

- *Identity*: a stable identifier, so tasks can be referenced
- *Inputs*: files, data or the outputs of other tasks, implying dependencies
- *Process*: the instructions producing the output, transcluding a script
- *Output constraints*: format and schema the output must conform to
- *Preconditions* (optional): define if/when the task can be executed
- *Postconditions* (optional): define if/when the task was successful
- *Evaluation* (optional): criteria to assess the quality of the output
- *Budget* (optional): tokens, cost and time allocated to a run

## Conceptual Model

Task: an identified script with a contract.
- `id`: identifier, from the frontmatter or derived from the file path
- `title`, `description`: human-readable label and summary
- `inputs`: script inputs, which can reference other tasks (see Format)
- `budget.{tokens,cost,time}`: allocated budget, if any
- `process`: template transcluding the script, run as a script
- `output`: output constraints, overriding those of the script
- `pre`, `post`: TaskChecks producing a TaskVerdict
- `evaluation`: TaskCheck producing a TaskEvaluation

TaskCheck: instructions assessing a task run, evaluated as a script.
- `content`: instructions, or a transcluded script

TaskVerdict: result of a precondition or postcondition.
- `pass`: boolean
- `reason`: text

TaskEvaluation: result of the output evaluation.
- `score`: 0 to 1
- `feedback`: text

Run: the stored execution of a task, extended by the layers above.
- `id`: run identifier, composed of the task id, datetime and counter
  (eg. `summarize-changes-20261105T1012-01`)
- `task`: task id
- `status`: `Pending`, `Running`, `Waiting` (on a question), `Succeeded`,
  `Failed`, `TimedOut`, `Cancelled`, `Blocked` (input tasks not succeeded) or
  `Skipped` (precondition failed)
- `started`, `ended`: datetimes
- `runtime`: engine, provider and model
- `budget`: allocated budget
- `usage.{tokens,cost,time}`: resources used, compared to `budget`
- `journal`: the script events (see [Script](spec-002-script.md)) and task events
- `output`: the output data
- `pre`, `post`: TaskVerdicts
- `evaluation`: TaskEvaluation
- `receipt`: the run metadata (status, timing, runtime, usage, verdicts,
  evaluation, failure reason)

The journal only tracks runtime events; each engine may keep more granular logs
(eg. full transcript).

## Format

A task is a markdown file with frontmatter. Its frontmatter uses the script
keys (see [Script](spec-002-script.md)) plus the task keys below, and its body
uses consistently named second level sections:

```markdown
---
id: summarize-changes
title: Summarize changes
budget: {tokens: 50000, cost: 0.50, time: 10m}
inputs:
  changes: task:collect-changes
  guidelines: ./docs/guidelines.md
output:
  format: json
  schema: ./schemas/summary.json
---
# Summarize changes

Optional task description.

## Pre

Precondition instructions.

## Process

Instructions framing the script.

@./scripts/summarize.md

## Post

Postcondition instructions.

## Evaluation

Output evaluation criteria.
```

| Property | Type | Purpose |
|---|---|---|
| `id` | string | Task identifier; defaults to the file path without extension |
| `budget.tokens` | number | Token budget per run |
| `budget.cost` | number | Cost budget per run |
| `budget.time` | duration | Time budget per run |

- `task:{id}` is a `PATH` scheme resolving to the output of the latest
  succeeded run of a task.
- Output instructions are given with `output.template` (see Script).
- All sections are optional except `Process`.

### Run Storage

A run is stored by the runtime as files covering:
- The output, as a text or JSON file
- The journal, as a structured log file
- The receipt, as structured data

## Operations

Task:
- `TaskCreate(context,task)`: creates a task, returning its id.
- `TaskGet(context,id)`: returns a task.
- `TaskUpdate(context,id,changes)`: updates a task.
- `TaskDelete(context,id)`: deletes a task (its runs are kept).
- `TaskList(context,filter?)`: lists tasks, filtered by id prefix or input task.
- `TaskValidate(context,task)`: checks the task is well-formed (frontmatter,
  resolvable transclusions and inputs, no dependency cycle).
- `TaskDependencies(context,task)`: returns the tasks referenced by the task
  (see `ScriptInputs`).
- `TaskExpand(context,task)`: renders the process as a prompt (see `ScriptExpand`).
- `TaskReady(context,task)`: tells if all input tasks have a succeeded run.

Run:
- `TaskStart(context,task,inputs?)`: starts a run, returning a Run.
- `RunGet(context,run)`: returns the run, including its status and receipt.
- `RunList(context,filter?)`: lists runs, filtered by task, status or date.
- `RunJournal(context,run,since?)`: streams the journal events, live while
  the run is in progress.
- `RunOutput(context,run)`: returns the output.
- `RunAnswer(context,run,question,answer)`: answers a pending question,
  resuming a `Waiting` run.
- `RunCancel(context,run)`: stops the run, ending it as `Cancelled`.
- `RunCheck(context,run,check)`: evaluates a precondition or postcondition,
  returning a TaskVerdict.
- `RunEvaluate(context,run)`: evaluates the output, returning a TaskEvaluation.

Status transitions (succeed, fail, time out) are internal to the runtime and
recorded in the receipt.

Events, added to the run journal after the script events:
- `TaskChecked(check,verdict)`: a precondition or postcondition was evaluated.
- `TaskEvaluated(evaluation)`: the output was evaluated.

## Rules

- *Dependencies*: a task depends on every task it references with `task:`,
  in its inputs or transclusions. Dependencies are implied, never declared.
- *Lifecycle*: `TaskStart` runs the following steps, stopping at the first
  failure:
  1. *Inputs*: input tasks without a succeeded run end the run as `Blocked`
  2. *Pre*: a failed precondition ends the run as `Skipped`
  3. *Process*: the process is run with `ScriptStart`, its events recorded in
     the journal; `ScriptEnded` gives the status and output
  4. *Post*: a failed postcondition ends the run as `Failed`
  5. *Evaluation*: records a TaskEvaluation, without changing the status
- *Composition*: the process transcludes the script, so task values take
  precedence over the script values (see Script composition). `Pre`, `Post`
  and `Evaluation` are not part of the process prompt.
- *Checks*: each check is run as a script with a TaskVerdict or TaskEvaluation
  output schema, and is given the task inputs and, for `post` and
  `evaluation`, the run output.
- *Budget*: allocated per run and passed to the runtime as limits (see Script
  limits). Checks count towards the budget.

## Out of Scope

- Plans: composition, scheduling and refinement of tasks (see Planning)
- Budget tracking across runs, retries and sequencing (see Orchestration)
- Effects of a run (eg. file system changes), captured by Planning and Orchestration
- Learning capture from evaluations and failures (see Learning)

## References

- [agenttasks.dev](https://agenttasks.dev/) defines a [task file format](https://agenttasks.dev/docs/spec/task-file)
  for managing tasks given to agents.
