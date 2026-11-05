# Plan

## Purpose

We want to define how tasks can be organised in a plan. While upfront planning
based on a goal is possible in theory, in practice the initial plan needs to
be refined, and sometimes even changed based on discoveries during task
execution. Our approach to planning, which is the elaboration of tasks and
their sequencing based on dependencies, needs to take the following into
consideration:

- *Elaboration*: given a goal ("I want to do THIS"), propose the tasks to
  deliver it, independently of any workflow.
- *Readiness*: determine if a task is ready to be run, which depends on its
  input availability and open gaps.
- *Budget*: tasks draw from a token, time or monetary budget allocated to the plan.
- *Failure*: tasks can fail, from system or tool errors, from bad input, bad
  output or from running out of budget.
- *Recovery*: a failed task may be retried, or aborted and reworked,
  potentially leading to a replanning.
- *Gaps*: missing inputs, genuine questions (gaps in instructions or data),
  decisions to be made and approvals are tracked until resolved.
- *Learnings*: execution, failures and decisions lead to findings (surprises,
  recurrent questions, edge cases, patterns) that inform planning and
  execution.

The plan holds the structure and state; decisions about sequencing, retries
and budget enforcement belong to Orchestration.

## Conceptual Model

Plan: the scope and state of a set of tasks working towards a goal.
- `id`: identifier
- `goal`: text describing the intended outcome
- `inputs`: data and assets given to the plan, available to its tasks
- `tasks`: tasks, with their plan facet
- `gaps`: Gaps raised within the plan
- `budget.{tokens,cost,time}`: total budget, drawn from by task runs
- `usage.{tokens,cost,time}`: sum of the usage of all runs in the plan
- `learnings`: references to the learnings informing the plan
- `revision`: current revision number
- `journal`: PlanRevisions, append-only
- `status`: `Draft`, `Active`, `Succeeded`, `Failed` or `Abandoned`

Task (plan facet): attributes added to a task within a plan.
- `state`: derived: `Open` (has open gaps), `Blocked` (input tasks not
  succeeded), `Ready`, or the status of its latest run
- `attempts`: number of runs
- `replaces`: id of the task it reworks, if any

Gap: something a task or plan needs and does not have.
- `id`: identifier
- `kind`: `input` (missing input), `information` (gap in instructions or
  data), `decision` (choice to be made) or `approval` (human sign-off on
  a pinned content revision)
- `question`: what is needed
- `options`: possible answers, if any
- `raised_by`: the elaboration, a run (eg. a script question) or a human
- `needed_by`: task ids, or the plan
- `status`: `Open`, `Resolved` or `Dismissed`
- `decision`: the Decision resolving it

Decision: the resolution of a gap.
- `id`: identifier
- `gap`: gap id
- `answer`: a Value (see [Script](spec-002-script.md))
- `rationale`: text
- `author`: a human or a run
- `date`: datetime
- `revision`: the plan revision recording it

PlanRevision: an entry in the plan journal, describing operations rather than
content.
- `revision`: number
- `author`: the elaboration run, or a human
- `date`: datetime
- `reason`: text, and the run, gap or learning that triggered the change
- `operations`: list of
  - `add(task,digest)`, `remove(task)`, `replace(old,new,digest)`
  - `edit(task,digest)`, for tasks without runs
  - `raise(gap)`, `resolve(gap,decision)`, `dismiss(gap)`
  - `update(field)`, for the plan goal, inputs or budget

A `digest` is a hash of the task content, so that pinned references (eg. from
a decision) can be verified.

## Elaboration

Elaboration is a baseline meta-task provided with the plan: an ordinary task
(see [Task](spec-003-task.md)) used both to create and to refine a plan.

| | Creation | Refinement |
|---|---|---|
| Inputs | Goal, plan inputs, learnings, available scripts and assets | Same, plus the plan state (task states, outputs, gaps, failures) and the reason |
| Output | Task definitions and gaps | Tasks added, removed or replaced, and gaps |
| Applied as | Revision 1 | A new revision |

Proposed tasks only reference inputs that are plan inputs, available scripts
and assets, or outputs of other tasks (`task:{id}`). Inputs that cannot be
resolved are raised as gaps rather than invented.

The definition of a reference elaboration task will be done in a future
specification document.

## Storage

Operational state is owned by Auto, knowledge and provenance by Memo:

| Element | Stored in | Notes |
|---|---|---|
| Plan, Task, Run | Plan directory | Operational state |
| PlanRevision | Plan directory | Append-only journal |
| Gap, Decision | Memo | Workflow records, referencing plan elements with pinned `{ref,digest}` |
| Learning | Memo | See Learning |

Memo is the default storage integration (see [Structure](spec-001-structure.md)),
not part of the model. Auto accesses Memo through the integration only, never
by writing its files directly.

## Format

A plan is a directory, acting as the scope of its tasks:

```
{plan}/
  plan.md             # Goal, inputs and budget (frontmatter), description
  tasks/{task}.md     # Task definitions (see Task)
  runs/{run}/         # Output, journal and receipt (see Task)
  journal.jsonl       # PlanRevisions, one per line
```

```markdown
---
id: release-notes
goal: Publish release notes for version 2.0
budget: {tokens: 500000, cost: 5.00, time: 2h}
inputs:
  changelog: ./CHANGELOG.md
---
# Release notes

Optional plan description.
```

- Task ids are unique within the plan, and `task:{id}` resolves within it.
- `plan:{name}` is a `PATH` scheme resolving to a plan input.
- Tasks may reference scripts and assets outside the plan by `PATH`.

## Operations

Plan:
- `PlanCreate(context,goal,inputs?,budget?)`: creates the plan and runs the
  elaboration, producing revision 1.
- `PlanGet(context,id)`: returns a plan.
- `PlanList(context,filter?)`: lists plans, filtered by status.
- `PlanRefine(context,plan,reason)`: runs the elaboration on the current
  state, applying the result as a new revision.
- `PlanRevise(context,plan,changes,reason)`: applies a manual revision.
- `PlanReady(context,plan)`: returns the tasks ready to run, extending
  `TaskReady` with the open gaps check.
- `PlanStatus(context,plan)`: returns progress, open gaps, budget and failures.
- `PlanJournal(context,plan,since?)`: returns the revisions, optionally since
  a given revision.
- `PlanAbandon(context,plan,reason)`: ends the plan as `Abandoned`.

Gap:
- `GapRaise(context,plan,gap)`: raises a gap, returning its id.
- `GapList(context,plan,filter?)`: lists gaps, filtered by status, kind or task.
- `GapDecide(context,gap,answer,rationale)`: resolves a gap, returning
  a Decision.
- `GapDismiss(context,gap,rationale)`: dismisses a gap as no longer relevant.

Events, added to the journal of the run concerned, if any:
- `GapRaised(gap)`: a gap was raised.
- `GapResolved(gap,decision)`: a gap was resolved.

## Rules

- *Scope*: a task belongs to a single plan, which holds its state and runs.
  Running a task (`TaskStart`) is done in the context of its plan.
- *Readiness*: a task is `Ready` when it has no open gaps and all its input
  tasks have a succeeded run. Preconditions are checked when the run starts
  (see Task lifecycle).
- *Gaps*: a question raised during a run (`ScriptQuestion`) is raised as
  a gap; the run is `Waiting` until a decision is made, and the answer is
  passed back to the run.
- *Revisions*: every change to the plan, its tasks or its gaps appends
  a revision to the journal. The current revision is the last entry.
- *Immutability*: a task with runs is never edited; changing it is a rework
  (a new task that `replaces` it). Tasks without runs may be edited in place,
  their new digest recorded in the revision. Runs, gaps, decisions and the
  journal are append-only.
- *Recovery*: a retry is a new run of the same task; a rework is a new task
  that `replaces` the failed one; a replan is a refinement. All are recorded
  as revisions, except retries which are recorded as runs.
- *Budget*: the plan usage is the sum of its runs usage, compared to the
  plan budget.
- *Status*: a plan is `Draft` until a task is run, and `Succeeded` when all
  its tasks have succeeded.

## Out of Scope

- Definition of the elaboration task: script, prompts and output schema (separate spec)
- Selecting which ready task to run, retries and budget enforcement (see Orchestration)
- Learning capture, storage and review (separate spec)
- Memo record formats for gaps and decisions (Memo workflow records)
