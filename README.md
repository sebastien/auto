# Auto

Auto turns a goal into a plan of tasks, runs them with an agent, asks you when
it needs a decision, and learns from what happened.

```
GOAL → PLAN → TASKS → OUTPUTS → LEARNING
         ↑                         │
         └──── refines the plan ───┘
```

## Install

Requires Python 3.14+ and one agent engine: Pi (the default), OpenCode V2,
Vercel's [fx](https://fx.sh) or the [llm](https://llm.datasette.io) CLI.

```sh
# Installs the sibling sebastien/memo checkout (../memo), which stores plans:
uv sync --extra mcp --extra interactive
bin/auto init   # optional: pick an installed engine and save it
```

Memo is a separate project, not the unrelated packages sharing its name on
package indexes: `uv sync` installs it from the sibling `../memo` checkout.
The OpenCode engine also needs Node.js 22+ and its bridge dependencies:
`npm ci --prefix src/py/auto/integration/opencode`.

## Start with a goal

```sh
$ auto "Document the project architecture"
  › Planning
Plan pln-000-document-project-architecture  Document the project architecture
   1 tsk-survey-modules    Map packages and their responsibilities
   2 tsk-draft-overview    Write the overview  ← tsk-survey-modules
   3 tsk-review            Review the document  ← tsk-draft-overview
Run it? [Y]es / [c]hange / [n]o ›
● tsk-survey-modules
✓ tsk-survey-modules  41s · $0.08
● tsk-draft-overview
Which diagram format should the overview use?
Answer › mermaid
✓ tsk-draft-overview  1m50s · $0.31
● tsk-review
✓ tsk-review  38s · $0.07

Done · 3/3 tasks · 3m09s · $0.46 · 1 decision
Results:
  ✓ tsk-survey-modules  Six packages: app, features, integration, runtime, utils…
  ✓ tsk-draft-overview  Wrote docs/architecture.md
  ✓ tsk-review          The overview matches the code.
Changed 2 files:
  + docs/architecture.md
  ~ README.md
Next: auto change "…" to refine it, or auto log to review it
Tip: auto show TASK prints a task's full output
```

Auto plans the goal, shows you the plan, and runs it once you agree. Agreeing
approves what its tasks declare they will do and policy would otherwise
confirm one by one, such as changing project files; denied effects still need
a decision. When a task needs an answer, Auto asks you and carries on. Choose
`c` to say what should change before anything runs, or `n` to keep the plan
for later. In a git project, the summary lists the files the run added (`+`),
modified (`~`) or deleted (`-`); each plan that succeeds ends with a tip about
one more command.

Most goals need no plan: a quick, cheap model first decides whether the goal
can run directly, as one task, and Auto then proposes just that:

```
Plan pln-001-add-gitignore  Add a .gitignore for this Python project
  Runs directly, as one task (repo.write)
  Adding a single .gitignore file is a focused change one session can complete.
Run it? [Y]es / [c]hange / [n]o ›
```

`--route plan` plans the goal anyway, `--route direct` skips planning. Changing
a direct goal plans it. The quick model is set by the `decision` profile (Pi
with `openai/gpt-6-luna`); when it is unavailable, goals are
planned. To use another, add `.auto/catalogue/profile/decision.md` with your
`engine`, `provider` and `model`.

Plans are kept in `.auto/` at the root of the project, which Auto creates the
first time. The plan you created or selected last is the *current plan*: the
commands below act on it.

## Where am I?

```sh
$ auto
pln-000-document-project-architecture  Document the project architecture
Active · 1/3 tasks · 41s · $0.08

  ✓ tsk-survey-modules  Map packages and their responsibilities
  ? tsk-draft-overview  Write the overview
  ○ tsk-review          Review the document  ← tsk-draft-overview

Needs you:
  1. Which diagram format should the overview use?

Next: auto answer
```

Every view ends with the command you most likely want next.

## The commands

Every day:

| Command | What it does |
|---|---|
| `auto "GOAL"` | Plan a goal, confirm it, run it and summarise (same as `auto new "GOAL"`) |
| `auto` | Show where the current plan stands and what needs you (same as `auto status`) |
| `auto go` | Continue the plan until it ends or needs you; `--task ID` runs a task again |
| `auto answer` | Answer the open questions, one by one; or `auto answer 1 "markdown"` |
| `auto change "WHAT"` | Change the plan, or one task with `--task ID` |
| `auto show TASK` | A task, its latest attempt and its output |

More, when you need it:

| Command | What it does |
|---|---|
| `auto pause` / `auto stop` | Pause once the current task finishes, or stop it now |
| `auto resume` | Release a paused or stopped plan and continue it |
| `auto log` | The plan's history; `--follow` keeps watching it |
| `auto plans` / `auto use PLAN` | List the project's plans, choose the current one |
| `auto decide "QUESTION" "ANSWER"` | Record a decision made outside a question |
| `auto learned` | What memory agents have learned and what awaits them |
| `auto run FILE.md` | Run a Markdown script on its own (same as `auto FILE.md`) |
| `auto init` | Choose an engine and write `.auto/config.toml` |

Each command has `--help`. `--plan ID` selects another plan for one command
(an ID or its number, eg. `--plan 000`); `AUTO_PLAN` does the same for a
shell. `--format json` gives structured output, and `--yes` runs a new plan
without asking. Without a terminal, or with `--no-input`, Auto never prompts:
a plan that needs an answer stops and waits for `auto answer NUMBER ANSWER`.

Exit statuses: `0` success, `1` failure, `2` invalid usage, `3` waiting on
you (a question, a pause, the budget), `124` timeout, `130` interrupted.

## Scripts

A script is a Markdown prompt with inputs, run without a plan:

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

## Learning

Plans can be watched by *memory agents*: personas whose observations of runs
and decisions are kept in Memo and consolidated into learnings that inform
later plans. Enable one per project:

```toml
[memory]
default_agents = ["auto:agent/ui-designer"]

[memory.agents.ui-designer]
# agent defaults to "agent-ui-designer/agent:UiDesigner"
# triggers = ["plan-ended", "decision", "backlog:24"]
```

New plans take `default_agents` unless `--agents` is given (`--agents '[]'`
selects none). `auto learned` shows what each agent has consolidated and what
awaits it. See [Memorisation](docs/specs/spec-010-memorisation.md).

## How it works

A *plan* is made of *tasks*: Markdown scripts with a contract (dependencies,
checks of their output). Auto's Elaborator writes the plan from the goal and
refines it as tasks complete. A task that needs something it does not have
raises a *question* (a gap); your answer is kept as a *decision*, with its
rationale. Each attempt at a task is a *run*, with its journal and receipt.

`.auto/` is a Memo store: each plan is a namespace whose task definitions
(`tsk/`), questions (`gap/`) and decisions (`dec/`) are Memo documents,
readable with `memo -m .auto`, beside its runs and journals. The contracts are
in [`docs/specs`](docs/specs) and implementation notes in
[`docs/implementation.md`](docs/implementation.md).

## Reference

### Configuration

Settings are read from `~/.auto/config.toml`, `$XDG_CONFIG_HOME/auto/config.toml`
(normally `~/.config/auto/config.toml`) and the project's `.auto/config.toml`,
in increasing order of precedence:

```toml
[runtime]
engine = "opencode"
provider = "openrouter"
model = "deepseek/deepseek-chat"
isolated = false
temperature = 0.2

[features.example]
enabled = true
```

Execution defaults belong under `[runtime]` (or `runtime:` in YAML); flat
runtime settings and unknown keys within that section are rejected. Nested
tables are merged and available to features through the runtime context.
Existing `config.yaml` files remain supported; TOML takes precedence over YAML
at the same location. Script metadata overrides file defaults; `AUTO_ENGINE`,
`AUTO_PROVIDER`, `AUTO_MODEL`, `AUTO_AGENT`, and the corresponding flags
override script metadata (flags take priority over environment variables).
OpenCode also accepts the `provider/model` form when `provider` is unset;
Pi accepts a separate provider; llm does not.

When no engine is configured and Pi is not installed, Auto uses the first
installed engine and says so; `auto init` saves the choice.

`auto x config` prints the effective configuration and the source of each
value, secrets included: take care when sharing it.

### Engines

Pi is the default. Auto keeps up to two Pi RPC workers hot during one Auto
process, reusing workers only when their directory, model, tool settings and
environment match. Each invocation starts a fresh conversation: tasks do not
inherit each other's messages. Errors or interruptions discard the worker;
exiting Auto closes all workers. Separate CLI commands still start cold.

Vercel's [fx CLI](https://fx.sh/docs/using-fx/fx-ask) is available with
`--engine fx`. Authenticate with `fx login` or set `AI_GATEWAY_API_KEY`. Auto
runs `fx ask --json --no-save`, preserving fx's permission checks and ambient
project instructions, skills and MCP configuration. `provider` selects an fx
connection (`gateway`, `codex`, `grok` or a configured custom connection);
`model` selects its model. System-prompt overrides are supported, but named
agents, temperature overrides and `isolated: true` are not. Output, tool
summaries and main-agent token counts arrive only when fx exits; cost is not
reported. Token/cost budgets are rejected; timeouts and cancellation remain
supported. The adapter does not bridge live permission approvals or enforce
Auto effect policies, and never enables fx's full-access mode on its own.

The OpenCode bridge uses `@opencode/client` for background service discovery
and authentication, and receives request bodies through stdin. For an
installed package, find its directory with
`.venv/bin/python -c 'import auto.integration.opencode as m; print(m.__path__[0])'`.

### Usage and cost

Every engine run in a project with an `.auto` directory appends a line to
`.auto/usage.jsonl`: engine, provider, model, status, duration, tokens and
cost. Set `journal = false` in configuration to disable it.

```sh
auto x usage                          # totals by engine, provider and model
auto x usage --by day,model --since 7d
auto x usage --where plan=pln-000 --entries
auto x usage --format tsv > usage.tsv # or --format json
```

Groups and filters accept `day`, `month`, `project`, `plan`, `kind`, `phase`,
`attempt`, `run`, `task`, `engine`, `provider`, `model` and `status`. Tokens and
cost show `-` when no run in a group reported usage; `PARTIAL` counts runs whose
usage is missing or incomplete (`llm`, `fx`), so their totals are lower bounds.
Engines that report it add model `TURNS` and tokens by kind (`INPUT`, `OUTPUT`,
`CACHED` reads); `TOKENS` counts them all, as budgets do.

When an engine reports no cost (eg. a subscription), `≈COST` estimates it from
a price table, in dollars per million tokens. Auto knows a few models; add or
override others, by model or `provider/model`:

```toml
[prices."openai/gpt-6-luna"]
input = 0.10
output = 0.50
cache_read = 0.01   # defaults to input; cache_write too
```

A guided plan's summary ends with what each stage spent: planning, tasks,
checks, repairs and memory, eg. `Spent: planning 140k tokens 10 turns ≈$0.02`.

### Consolidation

```sh
auto x consolidation status auto:agent/ui-designer
auto x consolidation run auto:agent/ui-designer --phases '[verify, derive]'
```

The configured Memo agent and its private observation log must already exist
with retained identities; consolidation creates no learning stores implicitly.
Consolidation uses an agent-scoped task run, validates its change set through
Memo in dry mode, and stages application for recovery after interruption.
Concurrent requests coalesce. Project and agent allowances reserve capacity
before execution and charge measured usage separately from plan budgets.
`--overrideAllowance` overrides project caps while preserving user
restrictions and the per-run budget. Allowance-blocked requests stay pending.
Decision inputs use event cursors that advance only after application.
Selected agents can request consolidation on plan completion, relevant
decisions or a `backlog:N` threshold checked after task completion.

Housekeeping runs after a successful command has printed its result, at most
one pending pass per agent; failures are kept in the agent's maintenance status
and never replace the command's result. There is no daemon.

### Interface resolvers

```toml
[resolvers]
auto-agent = "auto-agent excerpt"
```

Auto runs the command with `REFERENCE --budget BYTES` as separate arguments,
without a shell, from the project directory, and expects a JSON excerpt object.
Exit `3` or a missing executable means unavailable; other errors fail
resolution. Commands and output remain subject to execution and byte limits.

### Internals

`auto x` holds the operations behind the main commands, for inspection,
recovery and agents: `auto x plan create|continue|hold|release`,
`auto x task run`, `auto x run get|output|journal`, `auto x gap list|decide`,
`auto x policy show`, `auto x catalogue list|get`,
`auto x elaboration validate`, `auto x audit validate`,
`auto x script expand`, `auto x config`, `auto x usage` and `auto x mcp`.

`auto x mcp` serves stdio MCP: script runs, plan status, events, plans, tasks,
gaps, policy, learning, catalogue reads and validation. The Python operations
under `auto.features` additionally expose lifecycle, revision, repair and
reassessment operations; the CLI and MCP delegate to them.

## Verification

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
ruff check --select F src/py tests
```

Tests use deterministic engine fixtures and exercise real Memo when installed.
They do not make paid model calls.
