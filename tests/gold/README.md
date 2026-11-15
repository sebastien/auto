# Gold tests

End-to-end scenarios that drive the `auto` CLI against a real engine. They
cost tokens and time, so neither `kame test` nor unittest discovery runs them.

```sh
kame gold                         # all scenarios
tests/gold/run.sh T001-01            # one scenario
GOLD_REFINE=0 tests/gold/run.sh      # skip the refinement phase (about half the cost)
GOLD_MODEL=gpt-6-luna GOLD_PROVIDER=openai tests/gold/run.sh
```

Requirements: `jq`, `python3`, `git`, Memo installed in Auto's environment
(`uv sync` installs the sibling `../memo` checkout), and the selected
engine set up as for normal use (eg. `opencode` and the bridge dependencies,
see the main README).

## What they check

Scenarios only use the CLI (arguments, exit statuses, `--format json`) and the
files a plan produces, so they survive internal refactors. Model output varies:
assertions check outcomes and invariants, never exact text. Checks on model
behaviour too variable to require are reported as `WARN` without failing.

| Scenario | Covers |
|---|---|
| `T001-01-plan-static-site` | Script run, plan creation and elaboration, a goal gap whose answer (`GOLD_ACCENT`) must reach the stylesheet, a recorded decision, reuse of a project catalogue template whose `post` runs `tools/check-site.sh`, execution to success, refinement, usage accounting |

## Settings

| Variable | Default | Purpose |
|---|---|---|
| `GOLD_ENGINE` | `opencode` | Engine written to the sandbox's `.auto/config.toml` |
| `GOLD_PROVIDER` | `openai` | Provider; empty to leave unset |
| `GOLD_MODEL` | `gpt-6-luna` | Model; empty to leave unset |
| `GOLD_REFINE` | `1` | Run the refinement phase |
| `GOLD_BUDGET` | `{cost: 3, time: 3600}` | Plan budget |
| `GOLD_COST_WARN` | `1` | Warn when the plan cost exceeds it |
| `GOLD_TIMEOUT` | `1800` | Seconds allowed per command |
| `GOLD_MAX_ROUNDS` | `8` | `plan continue` attempts before failing |
| `GOLD_ACCENT` | `#3a7d44` | Answer to the accent colour gap |
| `GOLD_DECLINE` | `^engine\.` | Runtime policy approvals to decline (regex on the effect), eg. the user's ambient OpenCode skills; other approvals fail the test |
| `GOLD_KEEP` | `0` | Keep the sandbox even when the test passes |
| `GOLD_ROOT` | `$TMPDIR/auto-gold` | Where sandboxes are created, outside the checkout |
| `KAME_TEST_VERBOSITY` | `compact` | `verbose` lists every assertion and step |

## Reading a failure

A failed scenario keeps its sandbox and prints its path. It holds `project/`,
the plan's project (with its plans in `.auto/`), and `log/`, with one
`NN-step.{cmd,out,err,status}` set per command, in order.

## Writing a scenario

Name it `TNNN-NN-description.sh` and source `lib-auto.sh`, which builds on
`lib-testing.sh` (copied from Kame's test library). Use `auto-step` for
commands, `auto-assert`/`auto-expect-status` for required outcomes, and
override `gold-answer` to answer the gaps the scenario provokes.
