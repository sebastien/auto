# Auto

Composable agent scripts, persisted tasks and revisioned plans. The contracts
live in [`docs/specs`](docs/specs); implementation notes are in
[`docs/implementation.md`](docs/implementation.md).

## Setup

Requires Python 3.14+ and a configured Pi, OpenCode V2, fx or LLM CLI.

```sh
uv sync --extra mcp
# Install the sibling sebastien/memo checkout for gaps and decisions:
uv pip install -e ../memo
bin/auto help
bin/auto catalogue list
```

Memo is a separate project, not the unrelated packages sharing its name on
package indexes. Install it into Auto's environment. A subsequent exact
`uv sync` can remove it; reinstall it afterwards. Use `.venv/bin/python`
to run the complete integration suite with this installation.

The OpenCode engine also requires Node.js 22+ and its V2 client bridge dependencies:

```sh
npm ci --prefix src/py/auto/integration/opencode
```

For an installed Python package, use its integration directory instead:
`.venv/bin/python -c 'import auto.integration.opencode as m; print(m.__path__[0])'`.
The bridge uses `@opencode/client` for background service discovery and
authentication, and receives request bodies through stdin rather than arguments.

## Scripts

Create `hello.md`:

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
bin/auto script expand ./hello.md --input name=reader
bin/auto script run ./hello.md --engine pi --format json
# Shorthand for script run (also accepts absolute paths):
bin/auto ./hello.md --engine pi --format json
```

Pi is the default engine. Set defaults in `~/.auto/config.toml`,
`$XDG_CONFIG_HOME/auto/config.toml` (normally `~/.config/auto/config.toml`),
or the project's `.auto/config.toml`, in increasing order of precedence:

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

Nested tables are merged and available to features through the runtime context.

Memory agents are configured separately from an engine's harness agent:

```toml
[memory]
default_agents = ["auto:agent/ui-designer"]

[memory.agents.ui-designer]
# agent defaults to "agent-ui-designer/agent:UiDesigner"
# triggers = ["plan-ended", "decision", "backlog:24"]
```

Plan creation snapshots `default_agents` unless `--agents` is given;
`--agents '[]'` selects none. Defining an agent alone does not enable it by
default. Plan runs receive bounded Memo wake layers and performer-owned capture
instructions. The configured Memo agent and its private observation log must
already exist with retained identities; consolidation creates no learning stores
implicitly.

```sh
auto consolidation status auto:agent/ui-designer
auto consolidation run auto:agent/ui-designer --phases '[verify, derive]'
```

Consolidation uses an agent-scoped task run, validates its change set through
Memo in dry mode, and stages application for recovery after interruption.
Concurrent requests coalesce. Project and agent allowances reserve capacity
before execution and charge measured usage separately from plan budgets. An
explicit `--overrideAllowance` can override project caps while preserving user
restrictions and the per-run budget. Allowance-blocked requests stay pending.
Decision inputs use event cursors that advance only after application; existing
staged applications are reconciled before a new run. Optional `triggers` default
to `[]`. Selected agents can request consolidation on plan completion, relevant
decisions or a `backlog:N` threshold checked after task completion.

Successful CLI operations flush their primary output before checking and
processing housekeeping. MCP checks run in a separate task after the tool
result is prepared. Each invocation processes at most one pending pass per
agent; requests arriving during a pass remain pending for another invocation.
Failures are retained in agent maintenance status and do not replace the
command's result. There is no housekeeping daemon.

Project owners can configure interface record resolvers:

```toml
[resolvers]
auto-agent = "auto-agent excerpt"
```

Auto runs the command with `REFERENCE --budget BYTES` as separate arguments,
without a shell, from the project directory. It expects a JSON excerpt object.
Exit `3` or a missing executable means unavailable; other errors fail resolution.
Commands and output remain subject to execution and byte limits.

Execution defaults belong under `[runtime]` (or `runtime:` in YAML); flat
runtime settings and unknown keys within that section are rejected. Script
frontmatter and CLI flags remain flat.
Existing `config.yaml` files remain supported; TOML takes precedence over YAML
at the same location. Script metadata overrides file defaults; `AUTO_ENGINE`,
`AUTO_PROVIDER`, `AUTO_MODEL`, `AUTO_AGENT`, and corresponding command flags
override script metadata (flags take priority over environment variables).
OpenCode also accepts the existing `provider/model` form when `provider` is
unset. Separate provider selection is not supported by the Pi or llm integrations.
Scripts execute without creating plan/run storage.

Vercel's [fx CLI](https://fx.sh/docs/using-fx/fx-ask) is available with
`--engine fx`. Install fx separately and authenticate with `fx login` or set
`AI_GATEWAY_API_KEY`. Auto runs `fx ask --json --no-save`, preserving fx's
permission checks and ambient project instructions, skills and MCP configuration.
`provider` selects an fx connection (`gateway`, `codex`, `grok` or a configured
custom connection); `model` selects its model. System-prompt overrides are
supported, but named agents, temperature overrides and `isolated: true` are not.
Output, tool summaries and main-agent token counts arrive only when fx exits;
cost is not reported. Token/cost budgets are rejected; timeouts and cancellation
remain supported. Sessions are not saved, so fx subagents are unavailable.
The adapter does not bridge live permission approvals or enforce Auto effect
policies. It never enables fx's full-access mode on its own.

Run `auto config` to print the effective configuration and the source of each
value. Terminal output is shown as a table; set `NO_COLOR` to disable color.
This includes configured values such as secrets, so take care when sharing its
output. Script-specific metadata is not included.

## Plans

```sh
bin/auto plan create 'Document the project architecture' --engine pi
bin/auto plan status --plan document-the-project-architecture
bin/auto plan continue --plan document-the-project-architecture
bin/auto plan events --plan document-the-project-architecture --since 0 --follow --format json
bin/auto plan hold Pause 'Review the current result' --plan document-the-project-architecture
bin/auto plan release 'Ready to proceed' --plan document-the-project-architecture
bin/auto task run TASK_ID --plan document-the-project-architecture
bin/auto gap list --plan document-the-project-architecture
bin/auto gap decide GAP_ID 'answer' 'Reason for the choice' --plan document-the-project-architecture
bin/auto decision record 'Which document format?' 'markdown' 'Easy to review' --concerns '[documentation]' --source 'conversation:review' --plan document-the-project-architecture
bin/auto plan refine 'Address the review findings' --plan document-the-project-architecture
```

Plans live under `.auto/plans/`; gaps and decisions are Memo records under
`.memo/`. `AUTO_PLAN` selects a plan for subsequent commands. Plan creation
and refinement execute the built-in Elaborator and its checks. A Waiting
result requires a decision before execution can resume. `plan continue` runs
ready tasks and aggregates (including `root`) sequentially until the plan ends,
needs a decision, fails, reaches its budget or is held. Failed runs are not
retried automatically. `task run` remains available for explicit attempts.

Deciding a gap records the answer without starting execution. A running
continuation resumes the waiting run at its next selection; otherwise run
`plan continue` when ready. The Python `RunAnswer` operation explicitly answers
and resumes one run, and terminal question prompts use it.

`Pause` lets the current run finish; `Stop` cancels it. Holds persist, and
releasing one starts no work. Plan events cover manual operations as well as
continuations and can be followed or resumed from their sequence cursor.

Use `decision record` for choices made outside a gap. It records the question,
answer, rationale, author and optional concerns and source in Memo and the plan
journal. Decisions can be recorded after a plan ends without reopening it.

`--format json` provides structured output. Exit statuses: `0` success,
`1` failure, `2` invalid CLI usage, `3` Waiting/Blocked/Skipped, `124` timeout,
`130` cancellation. Each command has `--help`.
Continuation stops other than an ended or failed plan use pending status `3`.

In a terminal, execution shows a transient activity line on stderr with elapsed
time and reported cost; task budgets are shown when configured. Cost and token
usage depend on what the selected engine reports. Redirected output stays plain;
`NO_COLOR` disables color and `TERM=dumb` disables terminal progress.

## MCP and Python

`bin/auto mcp` serves stdio MCP. It exposes script execution, plan status,
gap listing, catalogue reads and elaboration validation. The Python operations
under `auto.features` additionally expose lifecycle, revision, repair and
reassessment operations; CLI and MCP delegate to those operations.

## Verification

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
ruff check --select F src/py tests
```

Tests use deterministic engine fixtures and exercise real Memo when installed.
They do not make paid model calls.
