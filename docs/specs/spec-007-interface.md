# Interface

## Purpose

We want to define how operations are exposed to humans and agents. The
operations defined by each specification are the contract; interfaces project
them without adding behaviour. Interfaces need to be:

- *Minimal*: only operations with a real need are exposed, and each exposed
  operation states its need
- *Consistent*: the same mapping rules apply to all operations and interfaces,
  so that the reference can be generated from the code
- *Scriptable*: stable structured output and exit status, so that interfaces
  can be used by scripts, shell checks and agents
- *Non-interactive safe*: an interface never blocks on input it cannot get
- *Self-discoverable*: when a human is present, the CLI asks for what is
  missing instead of failing (see [CLI Prompt](spec-011-cli-prompt.md))

## Conceptual Model

Operation: `Operation(context,arguments...)` (see [Structure](spec-001-structure.md)),
named `{Noun}{Verb}` (eg. `PlanCreate`, `ElaborationValidate`).

Interface: a projection of a subset of operations.
- *CLI*: commands, for humans, scripts and shell checks
- *MCP*: tools, for agents, served by the CLI (`auto x mcp`)

Exposure: the operations exposed by each interface, with the need they serve
(see Exposure).

## Mapping Rules

These rules apply to all interfaces:
- *Naming*: an operation is named after its noun and verb. `*Start`
  operations, which run a script or task, use the verb `run`.
- *Arguments*: required arguments are given in order, optional arguments by
  name. Structured payloads (eg. a TaskElaboration, a task definition) are
  given as a file or as content.
- *Values*: arguments taking inputs use the Value syntax (see
  [Script](spec-002-script.md)), eg. `changes=task:collect-changes` or
  `log="!git log -5"`.
- *Context*: the operation context is resolved by decreasing precedence from
  explicit arguments (eg. `plan`, `engine`, `model`), the environment (eg.
  `AUTO_PLAN`), the working directory (within a plan directory), then the
  configuration.
- *Results*: results are the operation results, serialised as described by
  the conceptual model of the specification defining them.
- *Streams*: operations returning events (eg. `ScriptStart`, `RunJournal`)
  stream them in order, ending with the final event (eg. `ScriptEnded`).
- *Questions*: a question raised by a run is answered interactively only when
  the interface allows it. Otherwise, within a plan, it is raised as a gap and
  the run is `Waiting` (see [Plan](spec-004-plan.md)); outside a plan, the run
  fails as `input`.

## CLI

```
auto {command} [ARGUMENT…] [--{name} VALUE…]          main commands
auto x {noun} {verb} [ARGUMENT…] [--{name} VALUE…]    internals
```

The CLI is for people first: its main commands follow the goal-to-result loop
(see [Goal](spec-000-goal.md)) and act on the current plan, while every other
operation remains available, by noun and verb, under `auto x`.

- *Naming*: a main command is one lowercase verb (eg. `auto go`,
  `auto answer`), stated by its exposure; an internal one is `x`, then its
  noun and verb (eg. `auto x run get`, `auto x elaboration validate`).
- *Shorthands*: `auto PATH.md [OPTIONS]` is `auto run PATH.md [OPTIONS]`;
  paths resolve from the working directory, and execution context discovery is
  unchanged by the script's location. `auto "GOAL" [OPTIONS]`, a quoted goal
  (one argument containing a space), is `auto new "GOAL" [OPTIONS]`; any
  other unknown word is a usage error, so that a mistyped command never
  starts a plan (an internal noun points to `auto x`). `auto` alone
  shows the current plan (`auto status`), or how to start one when there is
  none.
- *Help*: `auto help` and `auto help COMMAND` show static reference without an
  engine. `auto help "QUESTION"` runs the bundled `auto:catalogue/task/help`
  preset once, with the shipped reference and no tools. It does not create or
  update a plan or save a task. Use `auto help -- "COMMAND"` to ask about a
  command rather than show its static help. Freeform help needs Pi or LLM.
- *Arguments*: required arguments are positional, then optional positional
  arguments (eg. `auto answer [NUMBER] [ANSWER]`); other optional arguments
  are `--{name}` flags. Repeated arguments repeat the flag (eg.
  `--input a=./a.md --input b=./b.md`). A payload is given as a file path, or
  `-` for stdin.
- *Context*: `--plan`, `--engine`, `--model` and `--agent` are available on
  every command where they apply, with `AUTO_PLAN`, `AUTO_ENGINE`,
  `AUTO_MODEL` and `AUTO_AGENT` as environment equivalents. A plan is given
  by id or number, resolved as `.auto/{plan}/` in the project (see
  [Plan](spec-004-plan.md)), or by path. Without one, the plan is the one
  whose directory contains the working directory, then the project's
  *current plan* (the last created, or selected with `auto use`), then the
  project's only plan.
- *Guidance*: human results end with the next command the person most likely
  needs (eg. `Next: auto answer`), derived from the plan state. A guided plan
  that succeeds also ends with one tip, chosen by the plan's number, so that
  each new plan of a project introduces one more command. Hints and tips are
  never written in `json` output.
- *Engine*: when no engine is configured and the default one is not
  installed, operations with effects use the first installed engine, saying
  so on stderr; `auto init` saves an engine in the project configuration.
- *Output*: results are written on stdout, diagnostics on stderr. `--format`
  selects `text` (default, for humans), `markdown` (for inclusion in prompts:
  fenced YAML, or one line per entry for catalogue lists) or `json` (stable,
  following the model). Streams are rendered as text, or
  as JSON lines with `--format json`, one event per line.
- *Interaction*: in an interactive session, missing verbs, arguments, context
  and inputs are prompted for instead of failing, and questions are prompted;
  otherwise they are usage errors and non-interactive questions (see Mapping
  Rules). A guided plan (`auto new`, `auto go`, `auto resume`) asks the
  plan's open questions as it reaches them (see
  [CLI Prompt](spec-011-cli-prompt.md)).
- *Interruption*: `SIGINT` cancels the running script or run, ending it as
  `Cancelled`.
- *Help*: `auto help`, `auto {command} --help`, `auto x --help` and
  `auto x {noun} {verb} --help` are generated from the operations, and are
  the command reference. `auto help` lists the everyday commands of the loop
  first (`new`, `status`, `go`, `answer`, `change`, `show`, by their
  shorthands where they have one), then the others under "More", then `x`.

Exit status:

| Status | Meaning |
|---|---|
| `0` | Success: operation succeeded, run `Succeeded`, check passed, no violations |
| `1` | Failure: operation failed, run `Failed`, check failed, violations found |
| `2` | Usage: invalid command, arguments or flags |
| `3` | Pending: run `Waiting`, `Blocked` or `Skipped`, or a plan stopped waiting for answers, held or out of budget, which may be resumed or retried later |
| `124` | Run `TimedOut` |
| `130` | Run `Cancelled`, or a guided plan interrupted |

A guided plan that the person chose not to run yet ends with `0`.

## MCP

`auto x mcp` serves the MCP tools over stdio, with the working directory and
flags (eg. `--plan`) as the default context.

- *Naming*: tools are named `{noun}_{verb}` after the operation, whatever its
  CLI command (eg. `script_run`, `plan_status`, `elaboration_validate`).
- *Authority*: tools act with the server's author. Operations through which a
  person decides (answering, running a guided plan) are not exposed, so that
  an agent cannot decide for them.
- *Arguments*: a JSON object, with one property per argument and a JSON
  Schema derived from the operation. Payloads are given as content or as
  a `PATH`.
- *Results*: the JSON result as structured content, with its `markdown`
  rendering as text content.
- *Streams*: tools returning events run until the final event, relaying
  events as progress notifications, and return the final event.
- *Questions*: handled as non-interactive (see Mapping Rules).
- *Errors*: operation failures (eg. a run `Failed`, violations found) are
  results, not protocol errors; protocol errors are reserved for invalid
  calls and system failures.

## Exposure

Main commands, in the order of the reference:

| Operation | CLI | MCP | Need |
|---|---|---|---|
| `PlanDo` | `new` (and `auto "GOAL"`) | — | Go from a goal to a result: plan it, confirm it, run it asking what it needs, and summarise |
| `PlanStatus` | `status` (and `auto`) | `plan_status` | Know where the plan stands, what needs you and what to do next; give agents the plan state |
| `PlanGo` | `go` | — | Continue a plan, or run a task again, answering its questions as they come |
| `QuestionAnswer` | `answer` | — | Answer the plan's open questions, by number or one after the other |
| `PlanRefine` | `change` | — | Change a plan, or decompose a task, on demand |
| `PlanPause` | `pause` | — | Pause a plan once its current task finishes |
| `PlanStop` | `stop` | — | Stop a plan now, cancelling its current task |
| `PlanResume` | `resume` | — | Release a held plan and continue it |
| `PlanLog` | `log` | `plan_events` | Review a plan's history, and follow it (`--follow`/`--watch`), from any interface; `level` (`info`, `debug`, `trace`; default `AUTO_LOG_LEVEL`, then `[log] level`, then `debug`) adds each run's journal events |
| `TaskShow` | `show` | `task_show` | Know what a task does, how its latest attempt went and what it produced |
| `PlanList` | `plans` | `plan_list` | Find the plans of a project, and pick one to follow |
| `PlanUse` | `use` | — | Choose the plan later commands act on |
| `DecisionRecord` | `decide` | — | Record a decision made without a gap (eg. in conversation), so that none is lost (see [Plan](spec-004-plan.md)) |
| `LearningStatus` | `learned` | `learning_status` | Know what memory agents have consolidated and what awaits them (see [Memorisation](spec-010-memorisation.md)) |
| `ScriptRun` | `run` (and `auto PATH.md`) | `script_run` | Run a script standalone, keeping its output (see [Agent Script](spec-002-script.md#recorded-runs)) |

Internals, under `auto x`:

| Operation | CLI | MCP | Need |
|---|---|---|---|
| `ScriptExpand` | `script expand` | — | Inspect the prompt of a script while authoring it |
| `ScriptList` | `script list` | `script_list` | Find the recorded runs of standalone scripts |
| `ScriptOutput` | `script output` | `script_output` | Read the stored output of a script run |
| `PlanCreate` | `plan create` | — | Start a plan from a goal without running it |
| `PlanContinue` | `plan continue` | — | Run a plan until it needs a human, without asking (see [Orchestration](spec-009-orchestration.md)) |
| `PlanHold` | `plan hold` | — | Pause or stop a running plan |
| `PlanRelease` | `plan release` | — | Release a held plan without continuing it |
| `PolicyResolve` | `policy show` | `policy_resolve` | Know what a plan or task may do without confirmation (see [Plan Policy](spec-008-plan-policy.md)) |
| `TaskStart` | `task run` | — | Run a task by hand, outside a continuation |
| `RunGet` | `run get` | — | Inspect a run's status, failure and receipt |
| `RunOutput` | `run output` | — | Read the stored output of a run |
| `RunJournal` | `run journal` | — | Follow a run in progress |
| `GapList` | `gap list` | `gap_list` | Find what a plan needs |
| `GapDecide` | `gap decide` | — | Answer a gap by identity; decisions are made by humans through the CLI |
| `ConsolidationStart` | `consolidation run` | — | Request a consolidation of an agent's memory (see [Memorisation](spec-010-memorisation.md)) |
| `ConsolidationStatus` | `consolidation status` | `consolidation_status` | Know an agent's due work, open questions and pending proposals |
| `CatalogueList` | `catalogue list` | `catalogue_list` | List the framework, as an input of Elaborators |
| `CatalogueGet` | `catalogue get` | `catalogue_get` | Read a framework entry by identity |
| `ElaborationValidate` | `elaboration validate` | `elaboration_validate` | Post check of Elaborators, and self-check before answering |
| `AuditValidate` | `audit validate` | `audit_validate` | Post check of audit reports, and self-check before answering |
| `AuditResolutionValidate` | `resolution validate` | `resolution_validate` | Post check of audit resolutions, and self-check before answering |

`auto x config` (effective configuration and its sources), `auto x usage`
(the usage journal) and `auto x mcp` are interface commands, not operations.

## Guided Plans

The guided operations compose the plan operations into the steps a person
takes; they add no state of their own.

- `PlanDo` creates a plan with `approval: plan` (confirming it approves its
  `confirm` effects, see [Plan Policy](spec-008-plan-policy.md)), triaged
  unless `route` (`direct` or `plan`) is given (see
  [Elaboration](spec-006-elaboration.md)): a direct goal is proposed as one
  task, with its effects and the triage's reason. Otherwise it
  elaborates it, answering the questions of its
  elaboration, then streams a `PlanProposed` event: its tasks, dependencies,
  budget, open questions and memory agents. When a person can be asked, it
  asks a `PlanConfirm` question, answered `run`, `change` (then what should
  change, applied as a refinement before proposing again) or `no`. It then
  continues the plan as `PlanGo` does. With `yes`, or no person to ask, it
  runs without confirmation. When its planning fails, it stops `unplanned`,
  with the reason of the failure (validation violations by their messages).
  A plan whose creation fails before anything was recorded or run (eg. a
  budget too small to plan) is removed: the current plan and the plan
  counter are as they were.
- `PlanGo` asks the open questions, then runs continuations (see
  [Orchestration](spec-009-orchestration.md)): when one stops `waiting` and a
  person can be asked, it asks every open question, recording each answer as
  a decision, and continues. `task` first runs that task again, eg. after a
  failure.
- Both end with a `PlanSummary` event: status, why it stopped (a continuation
  stop, `declined` or `unplanned`, which exits 1) and its `reason`, tasks done, usage, budget, decisions, open questions,
  failed tasks, the first line of each succeeded task's output, the plan's
  memory agents and the files changed while it ran. Its exit status follows
  the stop, as a `ContinuationStopped` event's does.
- *Changed files*: when the project is in a git working tree, the summary
  lists the files added, modified or deleted between the start of the
  continuation (after confirmation, for `PlanDo`) and its end, committed or
  not; files that differed from the commit beforehand count only if they
  changed again, and ignored files and `.auto/` are left out. Outside git,
  `changed` is null. Changes made by anyone else meanwhile are included.
- *Spending*: the summary's `spending` totals the plan's usage journal by
  stage (`planning`, `tasks`, `checks`, `repairs`, `memory`): runs, duration,
  model turns, tokens by kind, cost and `cost_estimate` (the reported cost, or
  an estimate from the model's price where none was reported).
- *Receipt*: the summary's `decided` lists the plan's decisions in the order
  they were made, each with its `question`, `answer` and `author`
  (`decisions` counts them); `models` totals the plan's usage journal per
  engine, provider and model as `spending` does per stage, with a `total`
  row (`{"models": [], "total": null}` before any usage). The text view
  renders them as a receipt, then a `Details:` line naming `auto show TASK`,
  `auto log --level debug` and `auto x usage --where plan=ID`; a declined
  plan has none.
- A runtime policy request answered yes becomes the grant its rule requires
  (see [Plan Policy](spec-008-plan-policy.md)).

## Rules

- *Need*: an operation is exposed only with a need, recorded in the Exposure
  table. Operations without a need are not exposed, and remain available
  through the runtime.
- *No behaviour*: an interface only parses arguments, resolves the context,
  calls the operation and renders its result. Behaviour shared by interfaces
  belongs to the operation.
- *Stability*: the `json` format and exit statuses are a stable contract,
  relied on by shell checks (see [Task](spec-003-task.md)) and catalogue
  entries (see [Elaboration](spec-006-elaboration.md)).
- *Reference*: the command and tool reference is generated from the
  operations, and is not duplicated in specifications. A specification only
  documents an interface where it departs from the Mapping Rules.

## Out of Scope

- A Python API, which is the runtime itself (see [Structure](spec-001-structure.md))
- Other interfaces (eg. HTTP), until there is a need
- Authentication and permissions of the MCP server, delegated to the client
  harness

## References

- [Model Context Protocol](https://modelcontextprotocol.io/) defines tools,
  structured content and progress notifications.
