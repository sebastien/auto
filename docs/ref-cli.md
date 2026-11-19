# CLI

`auto` has a handful of *main commands* for the everyday loop (plan, run,
answer, change, inspect) and *internals* under `auto x` for inspection,
recovery and agents. Both are projections of the same operations
(`auto.app.operations`), which the MCP server exposes too.

Terms used below:

- *Current plan*: the plan created or selected last; main commands act on it.
- *Guided* commands (`auto "GOAL"`, `go`, `resume`) stream progress and end
  with a summary, the *receipt*.
- *Script run*: a Markdown script run without a plan, kept under
  `.auto/runs/`.

## Main commands

| Command | What it does |
|---|---|
| `auto "GOAL"` | Plan a goal, confirm it, run it and summarise (`auto new "GOAL"`) |
| `auto` | Where the current plan stands and what needs you (`auto status`) |
| `auto go` | Continue the plan until it ends or needs you; `--task ID` runs a task again |
| `auto answer` | Answer the open questions one by one, or `auto answer 1 "markdown"` |
| `auto change "WHAT"` | Change the plan, or one task with `--task ID` |
| `auto show TASK` | A task, its latest attempt and its output |
| `auto run FILE.md` | Run a Markdown script without a plan (`auto FILE.md`) |
| `auto pause` / `auto stop` | Pause once the current task finishes, or stop it now |
| `auto resume` | Release a paused or stopped plan and continue it |
| `auto log` | The plan's history, run by run |
| `auto plans` / `auto use PLAN` | List the project's plans, choose the current one |
| `auto decide "QUESTION" "ANSWER"` | Record a decision made outside a question |
| `auto learned` | What memory agents have learned and what awaits them |
| `auto help "QUESTION"` | Ask about Auto without starting a plan |
| `auto init` | Choose an engine and write `.auto/config.toml` |

`auto help` lists the commands; `auto help COMMAND` shows a command's options.

### Options

- `--plan ID` selects another plan for one command (an ID or its number, eg.
  `--plan 000`); `AUTO_PLAN` does the same for a shell.
- `--engine`, `--provider`, `--model`, `--agent` override the configuration
  (see [ref-configuration](ref-configuration.md)).
- `--format json` (or `markdown`) gives structured output.
- `--yes` runs a new plan without asking; `--route plan` plans the goal even
  when triage would run it directly, `--route direct` skips planning.
- `--input NAME=VALUE` gives a script or goal input, as a YAML value.
- `auto log --level info|debug|trace` chooses the detail (default `debug`,
  or `[log] level`, or `AUTO_LOG_LEVEL`): `info` keeps the plan history,
  `debug` adds tools, checks, repairs and denials, `trace` adds agent
  messages and usage. `--watch` (or `--follow`) keeps watching.

Without a terminal, or with `--no-input`, Auto never prompts: a plan that
needs an answer stops and waits for `auto answer NUMBER ANSWER`.

### Exit statuses

`0` success, `1` failure, `2` invalid usage, `3` waiting on you (a question, a
pause, the budget), `124` timeout, `130` interrupted.

## Scripts

A script is a Markdown prompt with front matter: inputs, output format, and
optionally engine settings.

```markdown
---
inputs:
  name: world
output:
  format: text
---
Greet {{ name }} in one sentence.
```

```sh
auto ./hello.md --input name=reader
```

Each run is kept under `.auto/runs/`: `auto x script list` lists them, and
`auto x script output ID` (or the reference `auto:runs/ID`) gives an output.

A script can specialise a catalogue template with `from`. Its sections fill
the template's inputs, and the rest of its body is the brief:

```markdown
---
from: task/audit
inputs:
  facet: documentation structure
---
Keep `docs/` to specifications, references and use cases.

## Scope

- docs/

## Criteria

- DOC-01: `docs/` only holds specifications, references and use cases
```

The audit runs read-only, and the next audit of the same scope reports what
changed since. `task/audit-resolve` fixes the findings of a report
(`--input audit=auto:runs/ID`). To let plans use your scripts, list them
under `[catalogue] actions` (see [ref-configuration](ref-configuration.md)).

## Internals

`auto x NOUN VERB` holds the operations behind the main commands:

- Plans: `auto x plan create|continue|hold|release`
- Tasks and runs: `auto x task run`, `auto x run get|output|journal`
- Questions: `auto x gap list|decide`
- Scripts: `auto x script expand|list|output`
- Policy and catalogue: `auto x policy show`, `auto x catalogue list|get`
- Validation: `auto x elaboration validate`, `auto x audit validate`,
  `auto x resolution validate`
- Memory: `auto x consolidation run|status`
- Tooling: `auto x config`, `auto x usage`, `auto x mcp`

`auto x mcp` serves stdio MCP: script runs, plan status, events, plans, tasks,
gaps, policy, learning, catalogue reads and validation. The Python operations
under `auto.features` additionally expose lifecycle, revision, repair and
reassessment operations; the CLI and MCP delegate to them.

## Consolidation

```sh
auto x consolidation status auto:agent/ui-designer
auto x consolidation run auto:agent/ui-designer --phases '[verify, derive]'
```

- The configured Memo agent and its private observation log must already
  exist with retained identities; consolidation creates no learning stores.
- A consolidation is an agent-scoped task run. Its change set is validated
  through Memo in dry mode, and application is staged for recovery after an
  interruption. Concurrent requests coalesce.
- Project and agent allowances reserve capacity before execution and charge
  measured usage separately from plan budgets. `--override-allowance`
  overrides project caps while keeping user restrictions and the per-run
  budget. Allowance-blocked requests stay pending.
- Decision inputs use event cursors that advance only after application.
- Agents can request consolidation on plan completion, relevant decisions or
  a `backlog:N` threshold checked after task completion.

Housekeeping runs after a successful command has printed its result, at most
one pending pass per agent. Failures are kept in the agent's maintenance
status and never replace the command's result. There is no daemon.
