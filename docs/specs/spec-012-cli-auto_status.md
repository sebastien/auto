# CLI Status and Watch

## Purpose

`auto status` gives a concise view of current work; `auto status --watch`
follows its updates. The commands are read-only projections of existing
operations and state. They do not start, resume, repair, or otherwise change
work.

This specification distinguishes established contracts from decisions that
remain open. It does not define a new event store or promise that every scope
has a live event source.

## Established behavior

- `PlanStatus(context,plan)` returns plan progress, open gaps, budget and
  failures. Plan status is derived from plan/task/run state; `PlanEnded` is a
  transition event, not a complete status snapshot. See
  [Plan](spec-004-plan.md).
- Plans have `Draft`, `Active`, `Succeeded`, `Failed` and `Abandoned` statuses.
  Task states are `Open`, `Blocked`, `Ready`, or the latest run status. Runs
  have `Pending`, `Running`, `Waiting`, `Succeeded`, `Failed`, `TimedOut`,
  `Cancelled`, `Blocked` and `Skipped` statuses. See
  [Plan](spec-004-plan.md) and [Task](spec-003-task.md).
- A plan event log records durable changes in append order with monotonically
  increasing `seq`. Consumers resume from a sequence cursor; delivery is at
  least once. `PlanEvents(since,follow)` returns later events in order and,
  with `follow`, streams appended events until interrupted. See
  [Orchestration](spec-009-orchestration.md).
- `RunJournal` exposes run events, while plan events also project run start,
  waiting and end. `ScriptRun` streams until `ScriptEnded`,
  then keeps the run's receipt and output under `.auto/runs/`; it is not
  resumable. See
  [Task](spec-003-task.md) and [Agent Script](spec-002-script.md).
- CLI streams preserve event order: text by default and JSON Lines with
  `--format json`. Results go to stdout and diagnostics to stderr.
  `SIGINT` cancels a running stream. Exit statuses are defined by
  [Interface](spec-007-interface.md).
- Plan context resolution follows explicit `--plan`, `AUTO_PLAN`, working
  directory containment, current plan, then the project's only plan. See
  [Interface](spec-007-interface.md).

## Command contract

`auto status` is the everyday CLI exposure of `PlanStatus`; bare `auto` has the
same status-view behavior, or explains how to start a plan when none resolves.
It uses normal context resolution and does not prompt merely to show status.
An explicitly requested but unresolved/ambiguous context follows the CLI's
usual usage and interaction rules.

`auto status --watch` requests a continuing view rather than a one-time
snapshot. It is read-only and must not be implemented by starting a
continuation. Watching a plan observes its event stream; it does not imply
that the plan is progressing.

The status view concerns the selected plan and its tasks/runs, rather than
silently aggregating every plan in the project. Project-wide plan discovery
remains `PlanList` (`auto plans`). A caller can select another plan using the
usual plan context options.

### Scope and source of updates

| Scope | One-time status | Established update source |
|---|---|---|
| Plan | `PlanStatus` | `PlanEvents` |
| Task/run within a plan | Included in plan progress where returned by `PlanStatus`; detailed run state is available through run operations | Run journal (`RunJournal`) and plan events that project run transitions |
| Standalone script | A receipt once ended (`ScriptList`); `ScriptRun` returns a live event stream | Its own `ScriptRun` stream only; no later subscription is defined |

The command must not present a script as a durable, resumable object. Nor
should it imply that memory-agent housekeeping is a live subscriber: triggers
may be queued from plan events and processed at later invocation boundaries
(see [Memorisation](spec-010-memorisation.md)).

### Output

In text mode, status is a human-readable snapshot; watch renders observed
updates in event order. In JSON mode, snapshots use the serialized
`PlanStatus` model, and watch emits one serialized event per JSON Lines record,
as required for streams by the Interface specification. No extra JSON hints,
progress prose, or synthesized event kinds are part of the contract.

Once a plan has ended (`Succeeded`, `Failed` or `Abandoned`), the text
snapshot also shows its outcome, as the guided run's summary does: the result
of each task that did the work (not of parents that only gather their
children's), the files its runs recorded writing, tokens and cost per model with
estimates marked `≈`, and the commands that show more. The JSON snapshot is
unchanged.

The underlying result remains authoritative: event delivery is at least once,
so consumers must use event sequence/cursors rather than assume exactly-once
delivery. A status snapshot is not itself an event and must not be represented
as one.

### End and exit behavior

A one-time status command ends after returning the snapshot. A watch continues
while its selected source can provide updates and ends when that source ends
or the user interrupts it. Interrupting a watch uses the existing stream
interruption behavior and exit status `130`; it does not cancel the plan,
task, or run being observed.

For a finite status result, exit status follows the Interface contract: `0`
for a successful operation, `1` for operation failure, `2` for usage errors.
Pending plan states are not themselves operation failures: status is
read-only, and the run/continuation pending exit mappings apply to operations
that run or continue work, not automatically to a status snapshot.

## Decisions not established by current specifications

The following require an explicit product/implementation decision before
being treated as a more detailed contract:

- Whether `--watch` selects only a plan event stream, multiplexes plan events
  and run journals, or accepts explicit task/run/script selectors. Existing
  operations expose these streams separately; no common subscription exists.
- Whether watch begins with an initial status snapshot, and how a consumer
  obtains a consistent snapshot-plus-events boundary without missing or
  duplicating changes.
- Whether watch exits automatically when the plan ends, when its continuation
  stops, or remains open for later revisions/events. A plan can receive
  decisions after ending, but no watch termination rule is specified.
- How task/run or standalone-script status is selected from the top-level
  command. In particular, standalone scripts are ephemeral, so attaching a
  later `status --watch` to one is not currently supported by the model.
- The exact human-readable layout, event summaries, filtering options,
  reconnect/backoff policy, and event-repair behavior during observation.
  The event log's ordering and at-least-once resumption rules still apply;
  transport details do not.
- Whether a project-wide watch across multiple plans is desired. `PlanList`
  is established for listing plans, but no cross-plan event cursor or
  ordering is specified.

These are intentionally left open rather than inferred from polling details,
implementation timing, or memory-trigger behavior.

## References

- [Interface](spec-007-interface.md)
- [CLI Prompt](spec-011-cli-prompt.md)
- [Plan](spec-004-plan.md)
- [Task](spec-003-task.md)
- [Agent Script](spec-002-script.md)
- [Orchestration](spec-009-orchestration.md)
- [Memorisation](spec-010-memorisation.md)
