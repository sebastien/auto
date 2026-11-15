# Orchestration

## Purpose

We want plans to progress on their own: once elaborated, a plan runs its
ready tasks until it needs a human, fails, runs out of budget, is held or
ends. Until now, tasks are run by hand (`TaskStart`). This specification is
the first increment of the Orchestration layer (see
[Goal](spec-000-goal.md)), and needs to support:

- *Continuation*: run the ready tasks of a plan, in order, until a stop
- *Holds*: let a human pause or stop the progression, and release it
- *Events*: an ordered, resumable record of everything happening to a plan,
  whatever its source (CLI, MCP, continuation, other interfaces)
- *Supervision*: interfaces follow and react to a plan through its events,
  without polling its state

Selecting between concurrent strategies, automatic retries, parallel runs and
workflows remain out of scope (see Out of Scope).

## Conceptual Model

Plan (orchestration facet): attributes added to a plan.
- `hold`: the current Hold, if any
- `continuation`: the current Continuation, if any
- `events`: PlanEvents, append-only

Hold: a human-requested suspension of the progression of a plan.
- `mode`: `Pause` (no new run starts) or `Stop` (the current run is
  cancelled, and no new run starts)
- `author`: a human
- `reason`: text
- `revision`: the PlanRevision recording it

Continuation: an execution of `PlanContinue` on a plan.
- `id`: `{timestamp}-{random}`
- `started`, `ended`: datetimes
- `runs`: the runs it started
- `stop`: the Stop ending it

Stop: why a continuation ended.

| Reason | Meaning |
|---|---|
| `waiting` | No task is ready, and remaining tasks need open gaps resolved |
| `failed` | No task is ready, and remaining tasks depend on failed runs; a run failed with a `system` failure; or runs timed out repeatedly |
| `budget` | The plan budget cannot cover the next run |
| `held` | A Hold was set |
| `ended` | The plan is `Succeeded`, `Failed` or `Abandoned` |
| `blocked` | No task is ready, and none is waiting on a gap (eg. inputs not succeeded) |
| `cancelled` | The continuation itself was interrupted |

PlanEvent: an entry of the plan event log.
- `seq`: position in the log, assigned at append
- `date`: datetime
- `kind`: the event kind (see Events)
- `ref`: canonical reference of the element concerned (see Plan references)
- `data`: event-specific fields
- `author`: a human, a run or a continuation, when the event records an
  action

## Events

The event log records what happens to a plan, after it happened:

| Kind | Data | Recorded after |
|---|---|---|
| `PlanRevised` | revision, operations summary | The journal append |
| `RunStarted` | run, task | The run is created |
| `RunWaiting` | run, gap | The run is `Waiting` |
| `RunEnded` | run, status, usage, failure | The receipt is written |
| `GapRaised` | gap, kind, needed_by | The `raise` revision |
| `GapResolved` | gap, decision | The `resolve` revision |
| `DecisionRecorded` | decision | The `record` revision |
| `GapDismissed` | gap | The `dismiss` revision |
| `PlanHeld` | mode, reason | The `hold` revision |
| `PlanReleased` | reason | The `release` revision |
| `ContinuationStarted` | continuation | The continuation lock is taken |
| `ContinuationStopped` | continuation, stop | Before the lock is released |
| `PlanEnded` | status | The plan status changes to an end status |

Events are recorded whatever the operation causing them: a gap decided
through the CLI, a task run by hand or a refinement by an interface appear in
the log like those of a continuation.

## Format

Plan directory additions (see [Plan](spec-004-plan.md)):

```
.auto/{plan}/
  events.jsonl        # PlanEvents, one per line
  continue.lock       # Held by the running continuation, if any
```

```json
{"seq": 42, "date": "2026-11-07T10:12:30Z", "kind": "RunEnded", "ref": "auto:pln-00a-release-notes/run/run-summarize-changes-20261107T1010-01", "data": {"status": "Succeeded", "usage": {"tokens": 18200, "cost": 0.18, "time": "2m40s"}}}
```

Orchestration settings are configured under `[orchestration]` (see
Configuration in the README), and may be overridden in the root task (`tsk/root.md`):

| Key | Default | Purpose |
|---|---|---|
| `timeouts` | `2` | Consecutive `TimedOut` runs stopping a continuation |

Holds are recorded in the plan journal as revision operations,
`hold(mode,reason)` and `release(reason)`, extending the PlanRevision
operations (see Plan).

## Continuation

`PlanContinue` repeats the following, one run at a time:

1. *Check*: stop when the plan has a Hold (`held`), has ended (`ended`), or
   cannot cover the next run with its remaining budget (`budget`).
2. *Select*: take the first ready task, in plan order (dependencies first,
   then journal order of addition). An aggregate, including `root`, is ready
   once its children have succeeded (see Plan aggregates).
3. *Hints*: a task with `hints.elaborate: later` is decomposed
   (`PlanRefine` on the task) instead of run, and the continuation proceeds
   with the new revision.
4. *Run*: start the task (`TaskStart`). A run ending `Waiting` is set aside
   until its gap is resolved, and selection proceeds with other ready tasks.
5. *After*: a run ending `Failed` or `TimedOut` blocks the tasks depending
   on it (see Plan readiness), and selection proceeds with unrelated ready
   tasks. A `system` failure (engine or tool error) stops the continuation
   (`failed`), as it is likely to affect the following runs. So do repeated
   timeouts: `orchestration.timeouts` consecutive `TimedOut` runs (default
   `2`) within the continuation, which suggest a wider problem (eg. an
   unresponsive engine, undersized budgets). A task with
   `hints.refine: after` that succeeded refines the plan (`PlanRefine`)
   before the next selection.
6. *Idle*: when no task is ready and no run is active, stop as `waiting`
   when open gaps are needed by remaining tasks, `failed` when remaining
   tasks depend on failed runs, `blocked` otherwise. When both gaps and
   failures hold remaining tasks, `failed` takes precedence.

A run set aside as `Waiting` resumes when its gap is resolved, within the
same continuation if it is still running; otherwise the run stays `Waiting`
until the next continuation resumes it.

A continuation ends with `ContinuationStopped`, giving the Stop. Its status
maps to the interface exit status as: `ended` with the plan `Succeeded` is
success, `failed` and `ended` otherwise are failure, the other stops are
pending (see [Interface](spec-007-interface.md)).

## Holds

- `Pause`: the current run, if any, completes; no further run starts. The
  continuation stops as `held` after the current run ends.
- `Stop`: the current run is cancelled, ending `Cancelled`; the continuation
  stops as `held`.
- Holds are set and released by humans. A Hold persists across continuations:
  `PlanContinue` on a held plan stops immediately as `held`.
- Releasing a Hold does not start a continuation; the caller decides (eg. an
  interface releases then continues).

## Operations

- `PlanContinue(context,plan)`: runs the plan until a Stop, streaming the
  PlanEvents it causes and ending with `ContinuationStopped`. Interrupting the
  stream ends the continuation as `cancelled` and cancels its current run.
- `PlanHold(context,plan,mode,reason)`: sets a Hold, acted upon by the
  running continuation, if any.
- `PlanRelease(context,plan,reason)`: releases the Hold.
- `PlanEvents(context,plan,since?,follow?)`: returns the PlanEvents after
  the `since` sequence number, in order. With `follow`, streams new events as
  they are appended, until interrupted.

## Rules

- *Single continuation*: a plan has at most one running continuation, held
  by `continue.lock`. `PlanContinue` on a plan with a running continuation
  fails without effect, reporting the running continuation; callers follow it
  with `PlanEvents`.
- *Sequential*: a continuation has at most one active run at a time. Runs of
  a plan started by hand while a continuation runs are recorded, and taken
  into account at the next selection.
- *Decisions*: deciding a gap does not start a continuation. A running
  continuation observes the resolution and resumes the waiting run or task;
  otherwise the caller decides whether to continue (see
  [Interface](spec-007-interface.md)).
- *Event order*: an event is appended after the change it records is
  durable (journal, receipt or Memo record), under the plan single-writer
  lock. The log order is authoritative for consumers, and `seq` is never
  reused.
- *Resumption*: consumers resume from the last `seq` they processed;
  delivery is at least once.
- *Repair*: the event log is derived from the journal, the runs, the gap
  records and the decisions recorded without a gap. `PlanRepair` appends the events missing for changes that were made
  durable without their event, in the order of their records.
- *Workspace*: continuations of plans sharing a working tree, and writing to
  it (see [Plan Policy](spec-008-plan-policy.md) `repo.write`), are not run
  concurrently; the second fails like a running continuation.
- *Policy*: Checkpoints and grant gaps (see Plan Policy) stop or set aside
  tasks like any other open gap.
- *Failure scope*: a failed run only holds back the tasks depending on it;
  unrelated tasks keep running. Failures are visible as they happen through
  `RunEnded`, so a human can intervene (eg. hold, refine) before the
  continuation stops.
- *No retries*: failed runs are not retried automatically; retrying,
  reworking or refining is a human or interface decision. A task's `repair`
  attempts (see Task) happen within its run, before it fails.

## Out of Scope

- Automatic retries and recovery strategies
- Parallel runs within a plan
- Workflows: rules acting on a plan (see [Goal](spec-000-goal.md))
- Scheduling across plans, and time-based triggers
- Acting on `hints.gate` beyond the approval gap raised by elaboration (see
  [Elaboration](spec-006-elaboration.md))
- Creating isolated workspaces for plans (eg. worktrees)
