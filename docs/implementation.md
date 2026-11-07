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

Runtime vocabulary is declared once, in `runtime/model.py` (event names, run
states, failure kinds) and in `features/plan/model.py` (plan, gap and revision
vocabularies). Features, interfaces and adapters reference those symbols
instead of repeating literals. Deferred imports are reserved for the
runtime-to-adapter boundary, the composition root, interface startup, and the
plan/task cycle; other imports stay at module top. Task runs belong to plans
(spec-003), so task operations use plan storage and policy directly.

Model construction goes through `create*` factories in the model modules
(`createPlan`, `createScript`, `createTaskFromText`, `createTaskFromDict`,
`createRunFromDict`, `createGapFromDict`, `createDecisionFromDict`,
`createRevisionFromDict`, `createElaborationFromDict`), which keeps text,
mapping and storage decoding in one predictable place. Run directory discovery
and receipt reads are shared from `plan/references.py` by both plan references
and task operations.

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

`auto plan create 'Write notes and commit them' --effects '[vcs.commit]'`
records the human's explicit effect request against the root definition. The
grant stops applying if that definition changes. Free-form goal text is not
automatically classified as an authorization: ambiguous language must not
silently grant a consequential effect.

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

An engine that enforces policy declares exact effect names in its optional
`enforceableEffects` collection and receives the resolved rules, grants,
declared effects and blocked effects in `request["policy"]`. It may yield a
`PolicyRequest` event with `effect` and `action` before performing an action.
It must wait for the boolean response, and on a detached run read the response
from `request["answer"]` when restarted. Unknown effects default to `confirm`.
Denied effects need a human decision whose answer is
`{"grant": "effect.name", "scope": "auto:plan/task/id"}`; a plain approval is
not a grant. OpenCode sessions ask Auto about tool permissions and disallow
subagents, whose independent rules cannot inherit the session policy. OpenCode
still claims no fully enforced effects: its shell and extensible tools can have
consequences that cannot be inferred reliably from their permission action.
Pi and LLM CLI do not implement this contract. Auto's own shell boundary
separately blocks restricted shell commands; it does not constrain shell
tools launched by an engine.

## Scope and verification

There is no automatic scheduler, automatic retry policy, learning capture or
refinement-hint executor. Plan creation/refinement can return a nested Waiting
run; resolve its gap with `gap decide`. Interactive task and script commands
can ask directly in a terminal.

The tests cover deterministic execution, the shipped Elaborator, durable
questions, revision recovery, approvals, engine wire fixtures, CLI and stdio
MCP. Live provider execution remains unverified.
