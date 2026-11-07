# Implementation decisions

## Structure

`src/py/auto/runtime` owns context, configuration, budgets, statuses and
reference/value resolution. `Context.runtime` is the gateway to integrations;
`runtime/integration.py` defines the engine and knowledge contracts, and
`wiring.py` is the composition root that installs feature reference schemes
without the runtime importing a feature. Features own script, task, plan and
elaboration operations in that order, with dataclasses for the specified
entities. `integration` adapts subprocesses, engines and Memo. `app` projects
shared operation metadata into CLI and MCP. V1 informed adapter behaviour,
rather than determining the new architecture.

YAML uses YAML 1.2 semantics; templates use Python Liquid with lazy Auto
transclusions; JSON output is checked with JSON Schema. Shell interpolation
quotes values as data. Interpolation inside escapes, heredocs and shell
substitutions is rejected because the renderer cannot quote it reliably.

## Persistence and recovery

Scripts are ephemeral. Tasks create unique run directories, journals, saved
execution state and immutable terminal receipts. Later reassessments append
usage to the journal; reads project that usage without rewriting receipts.

Plan mutations hold a filesystem writer lock and stage `.pending.json` before
publishing files, Memo records and a revision. `PlanRepair` rolls staged work
forward, reconciles orphan Memo records, resumes resolved questions and applies
successful pending Elaborators. Elaborator application is idempotent by run ID.
Memo retains ownership of its metadata and optimistic revision checks.

Aggregate successes pin their child run identities. Changed membership or
child runs require a new aggregate attempt. Changed input content is reported
as stale; it does not rewrite historical success. Approval decisions verify
the pinned subject digest before acceptance.

## Engine capabilities

| Engine | Behaviour |
| --- | --- |
| Pi | Default; JSONL execution, optional ambient-context isolation, tools and reported token/cost usage. Named agents are rejected; temperature overrides are ignored with a warning. |
| OpenCode V2 | Authenticated `opencode api` transport; durable sessions and forms. Isolation and system-prompt overrides cannot be provided by this transport; temperature overrides are ignored with a warning. |
| LLM CLI | Ephemeral text execution. Named agents and token/cost budgets are rejected because the adapter lacks live usage reporting. |

Usage limits are checked when the engine reports usage; they cannot guarantee
that a provider stops before an in-flight generation exceeds a limit. Time
limits and cancellation terminate subprocess groups; OpenCode also receives
a server-side interrupt.

## Scope and verification

There is no automatic scheduler, automatic retry policy, learning capture or
refinement-hint executor. Plan creation/refinement can return a nested Waiting
run; resolve its gap with `gap decide`. Interactive task and script commands
can ask directly in a terminal.

The tests cover deterministic execution, the shipped Elaborator, durable
questions, revision recovery, approvals, engine wire fixtures, CLI and stdio
MCP. Live provider execution remains unverified.
