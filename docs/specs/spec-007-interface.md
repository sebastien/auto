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
- *MCP*: tools, for agents, served by the CLI (`auto mcp`)

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
auto {noun} {verb} [ARGUMENT…] [--{name} VALUE…]
```

- *Naming*: nouns and verbs are lowercase, multi-word names separated by `-`
  (eg. `auto script run`, `auto elaboration validate`).
- *Shorthand*: `auto PATH.md [OPTIONS]` is equivalent to
  `auto script run PATH.md [OPTIONS]`. Paths resolve from the working directory;
  execution context discovery is unchanged by the script's location.
- *Arguments*: required arguments are positional, optional arguments are
  `--{name}` flags. Repeated arguments repeat the flag (eg.
  `--input a=./a.md --input b=./b.md`). A payload is given as a file path, or
  `-` for stdin.
- *Context*: `--plan`, `--engine`, `--model` and `--agent` are available on
  every command where they apply, with `AUTO_PLAN`, `AUTO_ENGINE`,
  `AUTO_MODEL` and `AUTO_AGENT` as environment equivalents. A plan is given
  by id, resolved as `.auto/{plan}/` in the project (see
  [Plan](spec-004-plan.md)), or by path.
- *Output*: results are written on stdout, diagnostics on stderr. `--format`
  selects `text` (default, for humans), `markdown` (for inclusion in prompts)
  or `json` (stable, following the model). Streams are rendered as text, or
  as JSON lines with `--format json`, one event per line.
- *Interaction*: in an interactive session, missing verbs, arguments, context
  and inputs are prompted for instead of failing, and questions are prompted;
  otherwise they are usage errors and non-interactive questions (see Mapping
  Rules). `auto` alone prints the reference (see
  [CLI Prompt](spec-011-cli-prompt.md)).
- *Interruption*: `SIGINT` cancels the running script or run, ending it as
  `Cancelled`.
- *Help*: `auto help`, `auto {noun} --help` and `auto {noun} {verb} --help`
  are generated from the operations, and are the command reference.

Exit status:

| Status | Meaning |
|---|---|
| `0` | Success: operation succeeded, run `Succeeded`, check passed, no violations |
| `1` | Failure: operation failed, run `Failed`, check failed, violations found |
| `2` | Usage: invalid command, arguments or flags |
| `3` | Pending: run `Waiting`, `Blocked` or `Skipped`, which may be resumed or retried later |
| `124` | Run `TimedOut` |
| `130` | Run `Cancelled` |

## MCP

`auto mcp` serves the MCP tools over stdio, with the working directory and
flags (eg. `--plan`) as the default context.

- *Naming*: tools are named `{noun}_{verb}` (eg. `script_run`,
  `elaboration_validate`).
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

| Operation | CLI | MCP | Need |
|---|---|---|---|
| `ScriptStart` | `script run` | `script_run` | Run a script standalone |
| `ScriptExpand` | `script expand` | — | Inspect the prompt of a script while authoring it |
| `PlanCreate` | `plan create` | — | Start a plan from a goal |
| `PlanRefine` | `plan refine` | — | Refine a plan, or decompose a task, on demand |
| `PlanList` | `plan list` | `plan_list` | Find the plans of a project, and pick one to follow |
| `PlanStatus` | `plan status` | `plan_status` | Follow a plan; give agents the plan state |
| `PlanContinue` | `plan continue` | — | Run a plan until it needs a human (see [Orchestration](spec-009-orchestration.md)) |
| `PlanHold` | `plan hold` | — | Pause or stop a running plan |
| `PlanRelease` | `plan release` | — | Release a held plan |
| `PlanEvents` | `plan events` | `plan_events` | Follow a plan, and resume following it, from any interface |
| `PolicyResolve` | `policy show` | `policy_resolve` | Know what a plan or task may do without confirmation (see [Plan Policy](spec-008-plan-policy.md)) |
| `TaskStart` | `task run` | — | Run a task by hand, outside a continuation |
| `RunJournal` | `run journal` | — | Follow a run in progress |
| `GapList` | `gap list` | `gap_list` | Find what a plan needs |
| `GapDecide` | `gap decide` | — | Answer a gap; decisions are made by humans through the CLI |
| `DecisionRecord` | `decision record` | — | Record a decision made without a gap (eg. in conversation), so that none is lost (see [Plan](spec-004-plan.md)) |
| `ConsolidationStart` | `consolidation run` | — | Request a consolidation of an agent's memory (see [Memorisation](spec-010-memorisation.md)) |
| `ConsolidationStatus` | `consolidation status` | `consolidation_status` | Know an agent's due work, open questions and pending proposals |
| `CatalogueList` | `catalogue list` | `catalogue_list` | List the framework, as an input of Elaborators |
| `CatalogueGet` | `catalogue get` | `catalogue_get` | Read a framework entry by identity |
| `ElaborationValidate` | `elaboration validate` | `elaboration_validate` | Post check of Elaborators, and self-check before answering |
| `AuditValidate` | `audit validate` | `audit_validate` | Post check of audit reports, and self-check before answering |

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
