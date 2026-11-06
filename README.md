# Auto

Composable agent scripts, persisted tasks and revisioned plans. The contracts
live in [`docs/specs`](docs/specs); implementation notes are in
[`docs/implementation.md`](docs/implementation.md).

## Setup

Requires Python 3.14+ and a configured Pi, OpenCode V2 or LLM CLI.

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
```

Pi is the default engine. Set defaults in `~/.auto/config.toml`,
`$XDG_CONFIG_HOME/auto/config.toml` (normally `~/.config/auto/config.toml`),
or the project's `.auto/config.toml`, in increasing order of precedence:

```toml
engine = "opencode"
provider = "openrouter"
model = "deepseek/deepseek-chat"
isolated = false
temperature = 0.2

[features.example]
enabled = true
```

Nested tables are merged and available to features through the runtime context.
Existing `config.yaml` files remain supported; TOML takes precedence over YAML
at the same location. Script metadata overrides file defaults; `AUTO_ENGINE`,
`AUTO_PROVIDER`, `AUTO_MODEL`, `AUTO_AGENT`, and corresponding command flags
override script metadata (flags take priority over environment variables).
OpenCode also accepts the existing `provider/model` form when `provider` is
unset. Separate provider selection is not supported by the Pi or llm integrations.
Scripts execute without creating plan/run storage.

Run `auto config` to print the effective configuration and the source of each
value. Terminal output is shown as a table; set `NO_COLOR` to disable color.
This includes configured values such as secrets, so take care when sharing its
output. Script-specific metadata is not included.

## Plans

```sh
bin/auto plan create 'Document the project architecture' --engine pi
bin/auto plan status --plan document-the-project-architecture
bin/auto task run TASK_ID --plan document-the-project-architecture
bin/auto gap list --plan document-the-project-architecture
bin/auto gap decide GAP_ID 'answer' 'Reason for the choice' --plan document-the-project-architecture
bin/auto plan refine 'Address the review findings' --plan document-the-project-architecture
```

Plans live under `.auto/plans/`; gaps and decisions are Memo records under
`.memo/`. `AUTO_PLAN` selects a plan for subsequent commands. Plan creation
and refinement execute the built-in Elaborator and its checks. A Waiting
result requires a decision before execution can resume. Run ready tasks
explicitly, then run their aggregate parent (including `root`).

`--format json` provides structured output. Exit statuses: `0` success,
`1` failure, `2` invalid CLI usage, `3` Waiting/Blocked/Skipped, `124` timeout,
`130` cancellation. Each command has `--help`.

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
