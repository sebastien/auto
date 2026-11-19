# Configuration

Auto reads its settings from TOML (or YAML) files, merges them with script
metadata, environment variables and flags, and hands the result to an
*engine*: the agent harness that runs a prompt. This reference covers the
configuration files, the engines, usage and cost accounting, and memory
agents.

Terms used below:

- *Engine*: the external agent CLI Auto drives: `pi`, `opencode`, `fx` or `llm`.
- *Role*: what an engine run is for (planning, tasks, checks, …), which can
  carry its own defaults.
- *Profile*: a catalogue entry that fixes engine and model for a kind of
  judgement, such as `profile/decision`.
- *Usage journal*: `.auto/usage.jsonl`, one line per engine run.

## Files and precedence

Settings are read from these files, in increasing order of precedence:

1. `~/.auto/config.toml`
2. `$XDG_CONFIG_HOME/auto/config.toml` (normally `~/.config/auto/config.toml`)
3. the project's `.auto/config.toml`

`config.yaml` files remain supported; TOML takes precedence over YAML at the
same location. `auto init` writes a project file with the chosen engine.
`auto x config` prints the effective configuration and the source of each
value, secrets included: take care when sharing it.

```toml
[runtime]
engine = "opencode"
provider = "openrouter"
model = "deepseek/deepseek-chat"
isolated = false
temperature = 0.2

# Defaults that apply only when that engine runs.
[runtime.engines.pi]
provider = "openai"
model = "gpt-6-luna"

[runtime.engines.opencode]
provider = "openrouter"
model = "deepseek/deepseek-chat"

# Defaults per role; a role may select the engine as well.
[runtime.roles.planning]
engine = "opencode"
model = "anthropic/claude-opus-5-5"

[features.example]
enabled = true

[log]
level = "trace"   # auto log detail: info, debug (default) or trace
```

## Runtime settings

- Execution settings belong under `[runtime]` (`runtime:` in YAML): `engine`,
  `provider`, `model`, `agent`, `temperature`, `cwd`, `env`, `prompt`,
  `isolated`, `tools` and `access`. Runtime settings at the top level and
  unknown keys within `[runtime]` are rejected.
- `[runtime.engines.<engine>]` gives defaults used only when that engine
  runs. It cannot set `engine` itself (the plural `engines` avoids colliding
  with the `engine` key).
- `[runtime.roles.<role>]` gives defaults per role: `planning` (goal triage
  and elaboration), `tasks` (a task's Process), `checks` (Pre, Post and
  Evaluation), `repairs` (corrective attempts), `memory` (memory agent runs)
  and `script` (standalone scripts). A role may select the `engine`, whose
  defaults then fill what the role leaves unset.
- Other tables (such as `[features.*]`) are merged and available to features
  through the runtime context.

Increasing precedence: built-in defaults, `[runtime]`, engine defaults, role
defaults, script/task/profile metadata, `AUTO_*` variables (`AUTO_ENGINE`,
`AUTO_PROVIDER`, `AUTO_MODEL`, `AUTO_AGENT`), then flags. Only the per-run
keys reach an engine; the `engines` and `roles` tables are configuration.

When no engine is configured and Pi is not installed, Auto uses the first
installed engine and says so; `auto init` saves the choice.

## Engines

*Pi* is the default. Auto keeps up to two Pi RPC workers hot within one Auto
process, reusing a worker only when its directory, model, tool settings and
environment match. Each invocation starts a fresh conversation: tasks do not
inherit each other's messages. Errors or interruptions discard the worker;
exiting Auto closes all workers. Separate CLI commands still start cold. Pi
takes `provider` and `model` separately.

*OpenCode* (V2) needs Node.js 22+ and the bridge dependencies
(`npm ci --prefix src/py/auto/integration/opencode`). The bridge uses
`@opencode/client` for background service discovery and authentication, and
receives request bodies through stdin. For an installed package, find the
bridge directory with
`.venv/bin/python -c 'import auto.integration.opencode as m; print(m.__path__[0])'`.
OpenCode accepts `model` in the `provider/model` form when `provider` is
unset. It rejects non-empty tool restrictions; `tools: []` denies all tools.
It always adds the user's global instructions, so runs are never fully
isolated.

*fx* ([Vercel's CLI](https://fx.sh/docs/using-fx/fx-ask)) is selected with
`--engine fx`. Authenticate with `fx login` or set `AI_GATEWAY_API_KEY`. Auto
runs `fx ask --json --no-save`, keeping fx's permission checks and ambient
project instructions, skills and MCP configuration.

- `provider` selects an fx connection (`gateway`, `codex`, `grok` or a custom
  one); `model` selects its model.
- System-prompt overrides work; named agents, temperature overrides and
  `isolated: true` do not.
- Output, tool summaries and main-agent token counts arrive only when fx
  exits; cost is not reported. Token and cost budgets are rejected; timeouts
  and cancellation work.
- The adapter does not bridge live permission approvals or enforce Auto
  effect policies, and never enables fx's full-access mode on its own.

*llm* ([Simon Willison's CLI](https://llm.datasette.io)) takes `model` only,
not a separate provider, and reports incomplete usage.

### The decision profile

Goal triage (run directly or plan first) uses the `decision` profile: Pi with
`openai/gpt-6-luna`, one turn, no tools. When it is unavailable, goals are
planned. To use another engine or model, add
`.auto/catalogue/profile/decision.md` with `engine`, `provider` and `model`
in its front matter.

### Freeform help

`auto help "QUESTION"` runs a bundled help task over the README, the
reference docs and the interface spec, with no project tools. It accepts Pi,
llm and OpenCode (`--engine` chooses). OpenCode runs it with the tool-less
`auto-help` agent but still includes your global instructions.

## Usage and cost

Every engine run in a project with an `.auto` directory appends a line to
`.auto/usage.jsonl`: engine, provider, model, status, duration, tokens and
cost. Set `journal = false` at the top level of the configuration to disable
it.

```sh
auto x usage                          # totals by engine, provider and model
auto x usage --by day,model --since 7d
auto x usage --where plan=pln-000 --entries
auto x usage --format tsv > usage.tsv # or --format json
```

- Groups and filters accept `day`, `month`, `project`, `plan`, `kind`,
  `phase`, `attempt`, `run`, `task`, `engine`, `provider`, `model` and
  `status`.
- Tokens and cost show `-` when no run in a group reported usage. `PARTIAL`
  counts runs whose usage is missing or incomplete (`llm`, `fx`), so their
  totals are lower bounds.
- Engines that report it add model `TURNS` and tokens by kind (`INPUT`,
  `OUTPUT`, `CACHED` reads); `TOKENS` counts them all, as budgets do.

When an engine reports no cost (eg. a subscription), `≈COST` estimates it
from a price table, in dollars per million tokens. Auto knows a few models;
add or override others, by model or `provider/model`:

```toml
[prices."openai/gpt-6-luna"]
input = 0.10
output = 0.50
cache_read = 0.01   # defaults to input; cache_write too
```

A guided plan's summary lists usage per model and in total, then per stage
(planning, tasks, checks, repairs and memory), eg.
`by stage: planning 140k tokens 10 turns ≈$0.02`.

## Memory agents

Memory agents observe plans and consolidate learnings in Memo (see
[spec-010](specs/spec-010-memorisation.md)). Enable them per project:

```toml
[memory]
default_agents = ["auto:agent/ui-designer"]

[memory.agents.ui-designer]
# agent defaults to "agent-ui-designer/agent:UiDesigner"
# triggers = ["plan-ended", "decision", "backlog:24"]
```

New plans take `default_agents` unless `--agents` is given (`--agents '[]'`
selects none). Consolidation itself is described in
[ref-cli](ref-cli.md#consolidation).

## Interface resolvers

```toml
[resolvers]
auto-agent = "auto-agent excerpt"
```

Auto runs the command with `REFERENCE --budget BYTES` as separate arguments,
without a shell, from the project directory, and expects a JSON excerpt
object. Exit `3` or a missing executable means unavailable; other errors fail
resolution. Commands and output remain subject to execution and byte limits.

## Script catalogue

To let plans use your own scripts as actions, list them:

```toml
[catalogue]
actions = ["action-*.md"]
```
