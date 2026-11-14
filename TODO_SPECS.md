# Specification Implementation Gaps

Review of specs 000–011 against the current source and tests, including existing
uncommitted changes. The initial review used static inspection and read-only
probes. Subsequent fixes and regression checks are recorded below. A complete
596-test suite has now passed with only six opt-in live checks skipped;
targeted checks and
live-provider limits are recorded below. A feature's presence does not establish
complete conformance.

## Coverage

| Specs | Assessment |
|---|---|
| 000 Goal | Execution and planning exist; autonomous progression and learning remain incomplete. |
| 001 Structure | Runtime feature validation now goes through the composition root; broader structural review remains. |
| 002 Script | Namespaced and nested implicit input discovery repaired and tested. |
| 003 Task | Reassessment now enforces shell and model permissions; broader lifecycle review remains. |
| 004 Plan | Substantial implementation; direct decisions and their repair are now present. |
| 005 Memo | Gap and decision storage exists, including gapless decisions. |
| 006 Elaboration | Substantial implementation; some interface/defaulting contracts differ. |
| 007 Interface | Policy naming, usage errors, orchestration and consolidation exposure implemented; real MCP stdio and executable OpenCode bridge checks pass. |
| 008 Policy | Reassessment enforcement and planning grant answers repaired; broader conformance review remains. |
| 009 Orchestration | Core continuation, holds, events, hints and exposure implemented and tested; crash/concurrency/budget hardening remains. |
| 010 Memorisation | Wake/capture context, explicit consolidation, recovery, cursors, allowances and invocation-triggered processing implemented; wider hardening remains. |
| 011 CLI Prompt | Labels, typed/form validation, cancellation and transitive input defaults tested; full interactive/live-engine audit remains. |

## Priority 0 — Policy Enforcement

- [x] **Keep rendered data from becoming executable shell checks.** Check
  execution now collects authored `!?` directives while rendering, including
  selected branches and transcluded Markdown. It no longer parses substituted
  input/parameter data, inline file contents or shell output as commands.
  Fenced/raw examples remain prose; raw/comment whitespace-control tags and
  unmatched raw examples inside fences do not consume later authored checks.
  Regressions exercise Pre/Post/Evaluation, materialized parameter data,
  frontmatter/body command order and reassessment. Files:
  `features/script/templates.py`, `features/task/operations.py`,
  `tests/test_003_02_integration_check_sources.py`. Specs: 002, 003, 008.

- [x] **Apply policy to run reassessment.**
  `RunCheck` and `RunEvaluate` now enforce current shell policy, preserve caller
  restrictions, and pass execution policy to model checks. Covered requests are
  approved; uncovered requests fail before action, since completed runs cannot
  suspend for approval. Tests cover tightened policy, shell/model denial,
  allowed model requests, immutable receipts and appended assessment usage.
  Specs: 003, 008.

## Priority 1 — Existing Contract Gaps

- [x] **Complete the human grant path for planning-time denied effects.**
  `GapDecide` records an explicit structured grant in the same revision as the
  resolution of a planning grant gap. The effect must be covered by the gap,
  the scope must be its plan or an existing needed task, and the author must
  be human. Tests cover valid progression and rejected effect/scope/author.
  Files: `src/py/auto/features/plan/gaps.py`,
  `src/py/auto/features/plan/policy.py`. Spec: 008.

- [x] **Require affirmative approval of output gates.**
  Shared gate checks now cover plan readiness/status and manual task execution.
  Regressions verify the agreed semantics:
  - Approval unblocks dependents for the exact output digest reviewed.
  - Rejection records a resolved decision but keeps dependents blocked; the
    producing run remains `Succeeded`.
  - A retry/rework produces a new output requiring a new approval; the rejected
    output cannot satisfy the gate.
  - Dismissal explicitly waives the gate, with a rationale.
  Test approve, reject, changed output, new output and dismissal.
  Added checks for late dependents, missing gate records and explicit root
  retries. Implementation: `src/py/auto/features/plan/gates.py`.
  Files: `src/py/auto/features/plan/operations.py`,
  `src/py/auto/features/plan/gaps.py`,
  `src/py/auto/features/task/operations.py`. Specs: 004, 006.

- [x] **Always supply the Elaborator's policy input.**
  Custom Elaborators receive `policy` even without a frontmatter declaration.
  Verified with a custom task rendering the Auto-supplied input.
  File: `src/py/auto/features/elaboration/operations.py`. Specs: 006, 008.

- [x] **Include frontmatter checks in TaskGoal.**
  Goal checks include `post`/`eval` commands and section content. A regression
  verifies they reach the Elaborator without executing the target's checks.
  File: `src/py/auto/features/elaboration/operations.py`. Spec: 006.

- [x] **Fix namespaced implicit input discovery.**
  Discovery normalizes namespaced and nested paths to input keys, including
  transitive inputs, and distinguishes reserved roots from similarly named
  inputs. Tests cover discovery and filling/expanding a namespaced CLI input.
  Files: `src/py/auto/features/script/operations.py`,
  `src/py/auto/app/cli/main.py`, `tests/test_002_01_unit_script.py`.
  Specs: 002, 011.

- [x] **Use usage exit status consistently.**
  Preflight context/domain/coercion errors return `2`, with available plan/task
  candidates in recovery. Operation and payload-read failures retain `1`.
  Tests cover missing/unknown context, unknown tasks and malformed budgets.
  File: `src/py/auto/app/cli/main.py`. Specs: 007, 011.

- [x] **Expose `policy show` without renaming the MCP tool.**
  CLI uses `policy show`; shared metadata preserves MCP `policy_resolve`.
  CLI execution and metadata names have regression coverage. A real MCP stdio
  client also calls `policy_resolve` and verifies its structured response.
  File: `src/py/auto/app/operations.py`. Spec: 007.

## Priority 1 — Orchestration

- [x] **Implement continuation.** `PlanContinue` selects ready work in journal
  order, runs aggregates and independent tasks, resumes resolved questions,
  records stop reasons and maps them to interface exit statuses. Failed runs
  are not automatically retried. Regressions cover progression, output/system
  failure, waits, cancellation, budget caps and consecutive timeouts. Spec: 009.

- [x] **Implement durable holds and continuation exclusion.** Human Pause/Stop
  and release revisions are durable; task startup honors holds. Stable-inode
  process locks exclude another continuation of the plan and writing plans
  sharing a working tree. Stream closure cancels its run before unlocking.
  Regression tests cover these controls and interrupted continuation records.
  Cross-process crash stress testing remains in hardening. Spec: 009.

- [x] **Implement the plan event log.** Revisions, gaps, decisions, run state
  transitions, holds, plan endings and continuations publish durable events.
  `since`/`follow` and repair are implemented. Sequence reservations never reuse
  cursors, and status projection checkpoints retain historical end transitions.
  Tests cover concurrent appends, live followers and interrupted publication.
  Specs: 004, 009.

- [x] **Act on refinement hints.** Continuation dispatches deferred elaboration
  and after-success refinement through `PlanRefine`; regressions check both
  paths and aggregation after decomposition. After-success dispatch now uses
  the shared result path, including tasks resumed after a question in the same
  or a later continuation. A refiner system failure still stops before unrelated
  work. Three further regressions cover these cases. Specs: 006, 009.

- [x] **Add orchestration exposure.** CLI: `plan continue`, `plan hold`,
  `plan release`, `plan events`. MCP: `plan_events`. CLI commands and cursor
  output have integration coverage. MCP handler tests cover empty event streams
  and progress results with inline dispatch; real stdio also verifies the empty
  `plan_events` result without inline dispatch.
  File: `src/py/auto/app/operations.py`. Specs: 007, 009.

- [ ] **Finish orchestration hardening.** Interrupted active workers now become
  durable system failures only after their run lock can be acquired. Startup
  owns that lock before publishing Running, and retains it across the writer
  lock handoff. Repair, continuation and explicit task starts reconcile stale
  workers without retrying their effects. Known usage is preserved, terminal
  receipts stay unchanged on repeat repair, and interrupted continuation
  records recover missing run IDs from receipts. Tests kill real manual and
  continuation processes, race two independent repair processes, and exercise
  startup publication and reentrant lock bookkeeping. Wider phase/cancellation
  crash windows still need coverage. Two further real-process checks now kill
  a worker after its durable question checkpoint and during question resumption.
  Waiting work retains its gap/session and resumes without repeating Pre;
  interrupted resumption retains the decision and measured usage, becomes a
  system failure, and is not automatically retried or applied. Tests:
  `tests/test_009_08_integration_question_crashes.py`.
  Exact measured limits now succeed; exceeding limits still fails with measured
  overspend retained. A fresh engine request cannot start with no remaining
  tokens/cost, while shell-only checks can finish an existing run at its exact
  token cap. Empty root/nested aggregates can assemble outputs with zero
  remaining tokens/cost. Tests also cover explicit resumed allocations after
  unrelated work and shrinking implicit caps without counting usage twice.
  Start/resume admission now runs under the plan writer lock, subtracting all
  measured usage and other Running/Pending reservations. The active receipt's
  remaining budget is its durable reservation; Waiting runs re-admit on resume.
  Unbounded active workers reserve available capacity if a limit is introduced.
  Tests exercise independent manual processes and a manual spend between
  continuation selection and startup. Reassessments now reserve capacity under
  the same writer lock and checkpoint known usage separately from immutable
  receipts. Admission includes their unspent reservations; reads include their
  measured progress. Completion publishes an identified journal charge before
  releasing the reservation. Repair charges known interrupted usage once after
  proving the worker lock is free. Tests cover concurrent checks, independent
  checking processes, manual startup contention, killed checks and interruption
  between journal publication and checkpoint deletion. Live budget reductions
  and increases, token/cost/time resumption, explicit retries retaining successful
  siblings, failed-attempt charges and nested aggregate reassessments now have
  regressions in `tests/test_009_07_integration_budget_combinations.py`.
  Core fixture coverage is not proof of these wider invariants.

## Priority 2 — Memorisation

- [x] **Complete agent defaults and configuration semantics.** Missing Memo
  references use the specified namespace/PascalCase defaults. Definitions remain
  in `memory.agents`; the clarified `memory.default_agents` list is snapshotted
  at plan creation and explicit `agents` overrides it, including `[]`.
  `agent` and `catalogue` are reserved. Regression tests cover defaults,
  duplicates, validation and creation without side effects on invalid input.
  Files: `src/py/auto/features/memory/config.py`,
  `src/py/auto/features/elaboration/operations.py`. Spec: 010.

- [x] **Implement run wake and capture context.** Memo snapshots supply bounded
  layers by task profile (`full` for Elaborators), with shared multi-agent world
  and recent budgets and explicit failure for insufficient minimums. Layer
  order and literal learning text survive rendering and resume. Capture
  instructions use canonical instance/source references and current policy;
  Auto never auto-appends observations. Receipts record byte/revision metadata
  and accept validated explicit adapter cache measurements. Elaborators gain
  agent model/peer context and default learning metadata. Fixture tests cover
  lifecycle resumes; real Memo tests verify frozen material and layer minimums.
  Live-provider prompt/cache behavior and interface-owned capture remain
  unverified. Files: `features/memory/operations.py`, `integration/memo/memory.py`,
  `tests/test_010_02_integration_wake.py`. Spec: 010.

- [x] **Implement consolidation against Memo's available operations.** Add the built-in
  Memoriser, agent-scoped runs, validation/dry-run, staged application,
  reconciliation and `ConsolidationStart`/`ConsolidationStatus`. Preserve the
  availability rule: do not claim runnable consolidation before Memo's required
  operations and partial-application reconciliation contract exist. Spec: 010.
  The current sibling Memo checkout contains `agentWakeLayers`, `agentDue` and
  `agentConsolidate`, including retained consolidation records. Their contract
  is now exercised through Auto; wider integration and hardening remain.
  Progress: the
  built-in Memoriser, JSON artifact, agent-scoped ordinary task lifecycle,
  mandatory dry Post, policy checks, durable coalescing, staged application,
  partial-publication replay and read/status operations are implemented.
  Real Memo tests inject publication failure and stale state, verify unique
  corrections/reports, immutable terminal receipts and custom task check order.
  Decision inputs/cursors and window allowance reservations are now integrated.
  Triggers and invocation draining are also integrated. Real-process tests now
  kill workers during inference, before staging, after staging, after Memo
  publication and after receipt publication. Recovery does not rerun the model
  or duplicate corrections/reports, and preserves terminal receipt bytes.
  Further checks exercise Memo's own identity lock held by an independent
  process, all seven requested phases under read-only engine policy, and
  cancellation after mandatory validation. The latter exposed and repaired a
  shared lifecycle cancellation defect. This covers the named implementation
  gaps, not every possible phase/policy interleaving.
  Allowance-blocked requests remain pending;
  existing staged applications can still be reconciled. Files: `features/memory/consolidation.py`,
  `integration/memo/consolidation.py`, `tests/test_010_03_integration_consolidation.py`,
  `tests/test_010_06_integration_consolidation_crashes.py`.

- [x] **Implement decision inputs and cursors.** Repaired plan events provide
  pinned decision references with concern filtering, including decisions on
  ended plans. Cursor snapshots advance only after completed application;
  recovery repairs a missing cursor publication. Tests retain decisions after
  failure and preserve decisions arriving during the model run. Spec: 010.
  Files: `features/memory/decisions.py`, `tests/test_010_03_integration_consolidation.py`.

- [x] **Implement allowance accounting and reservations.** Project and agent
  UTC day/week/month constraints reserve remaining run allocations under a
  project writer lock. Measured usage, failures, interrupted workers and window
  rollover are accounted separately from plans. Receipts recover missed ledger
  updates; corrupt totals cannot reset capacity. Earlier user caps/periods and
  agent policy restrictions survive project overrides. Explicit human project
  overrides preserve user restrictions and per-run limits. Tests include two
  independent processes contending for shared capacity. Spec: 010.
  Files: `features/memory/accounting.py`, `tests/test_010_04_unit_accounting.py`.

- [x] **Implement consolidation triggers and invocation draining.** Durable plan
  events queue plan-ended, backlog and concern-filtered decision requests without
  running inference under plan writer locks. Request/run copies retain queue
  cursors across interrupted cache publication. CLI output is flushed before a
  bounded housekeeping pass; MCP schedules it independently. Invocation checks
  repair missed events and cursor publication, reconcile incomplete applications,
  and retry allowance-deferred work. At most one pass per agent runs in one
  invocation; concurrent arrivals remain pending. Maintenance errors remain in
  status instead of replacing the primary result. Tests exercise these cases
  with real Memo, including unavailable contracts and identity-less legacy stores.
  Spec: 010. Files: `features/memory/scheduler.py`, `tests/test_010_05_integration_scheduler.py`.

- [x] **Implement configured external reference resolvers.** Owner commands run
  as argv with the reference and byte budget, without shell interpolation.
  Missing executables and exit `3` raise explicit unavailable-reference errors;
  invalid/mismatched JSON, oversized output, timeout and other nonzero statuses
  are errors. Tests exercise real fixture commands. Python custom resolvers now
  coexist with built-ins and dispatch honors the most specific prefix.
  Files: `src/py/auto/wiring.py`, `src/py/auto/runtime/model.py`. Spec: 010.

- [x] **Add consolidation exposure.** CLI: `consolidation run`,
  `consolidation status`. MCP: `consolidation_status`.
  File: `src/py/auto/app/operations.py`. The target Auto agent is kept separate
  from the engine's harness agent in CLI and MCP projection. Tests exercise
  the CLI status command and the MCP status handler. Spec: 007.

## Priority 2 — Other Partial Contracts

- [x] **Complete prompt descriptors and form fields.** Descriptors support
  labels; questions use their kinds, schemas, numeric/string constraints,
  multiline/path entry and typed values. Backend `key` and common `id` fields,
  optional/hidden/conditional fields and multi-select values are supported.
  Gap records retain fields and cancellation stops the waiting engine session.
  Regression tests exercise these behaviors; native external-action fields
  explicitly require the originating interface.
  Files: `src/py/auto/app/operations.py`, `src/py/auto/app/cli/prompt.py`.
  Spec: 011.

- [x] **Validate structured prompts beyond YAML syntax.** Budget prompts use
  runtime budget validation; collection arguments validate their domain shape.
  A regression enters a YAML scalar budget and corrects it before acceptance.
  Files: `src/py/auto/app/cli/main.py`, `src/py/auto/app/operations.py`.
  Spec: 011.

- [ ] **Finish template materialisation.** Application now expands parameter
  branches, loops and typed metadata before task section parsing and explicit
  overrides. Runtime inputs and shell/file transclusions remain deferred;
  stored literal bindings keep parameter data from becoming Liquid, shell,
  lifecycle headings or Markdown fences on the second render. Captures,
  assignments and whitespace controls have two-stage parity regressions.
  File and shell parameter Values resolve at application, with shell policy
  checked against current configuration. Dry validation executes no shell
  Values. Revision context records resolved sources; supplied template digests
  are verified. A per-originating-run lock excludes duplicate committed
  applications, while parameter effects run outside the plan writer lock and
  commit checks the starting revision. Known local/catalogue companions,
  nested parameter files and output contracts are now captured with original
  digests and source locations in the same task definition/revision. Tests
  cover changed/deleted files, selectors, symlinks, quoted paths, checksum/size
  checks, dependency discovery and staged-revision recovery without originals.
  Applied parameters remain fixed in companions; runtime inputs and paths
  selected by runtime inputs remain live. Snapshot inspection and input
  discovery cannot execute shell-backed references; actual parameter
  resolution enforces shell policy even through `git:` or nested references.
  Selected profiles and artifacts now use captured sources, including inherited
  plan profiles, owner-resolved framework references, artifact output contracts
  and composed Post/Evaluation fragments. Regression tests delete the originals
  and execute the task, verifying allocation settings, check order and evaluation.
  Task overrides skip unused template framework selections and overridden
  contracts; an artifact's unused Process is not treated as task instructions.
  Markdown companions now compile separately for each inclusion scope. The
  reproduced unrolled-loop failure is fixed: loop locals, parent-loop counters
  and known parameter branches survive composition, while runtime assignments
  and late-selected files retain their scope. Typed companion metadata and
  distinct dependencies of repeated inclusions have regressions. Task-owned
  source references resolve compiled bytes independently of task outputs and
  remain available through archived definitions after task removal.
  Artifact Post/Evaluation fragments and frontmatter checks now compile for
  materialized templates, with captured companion scopes, literal bindings and
  shell quoting. Runtime input Values stay live; ordinary tasks retain runtime
  parameter overrides. The artifact identity used by methodology validation is
  unchanged, and compiled checks are bound to that artifact's original revision.
  Elaboration output cannot inject generated source/binding/scope metadata.
  Tests cover deleted originals, reassessment, check order, dead branches,
  unused contract overrides and mismatched compiled sources.
  Nested shell parameter Values now resolve during application in a separate
  policy-controlled context, including defaults inside artifact companions.
  Source inspection and dry validation remain free of shell effects. Nested
  Values inherit configuration with the same precedence and source-relative
  working directories as rendering; parent parameter overrides skip unused
  effectful defaults. Tests verify per-inclusion resolution, committed replay,
  denied policies with an unparameterized root, shell-backed references,
  inherited environment/cwd and literal shell results. Authored shell/check
  directives remain deferred until execution.
  Dry metadata proposals now omit unresolved shell-dependent leaves while
  preserving known siblings for static validation. Application checks the
  concrete result before commit. Regressions cover dynamic budgets, unknown
  ancestors/indices, profile/artifact selection and frozen snapshots, invalid
  resolved titles/formats/references, and budgets exceeding the plan. Known
  invalid budgets, output formats and effect names still fail before parameter
  commands run. Unresolved profile/contract overrides do not inspect unused
  inherited defaults. Output formats and native nested metadata strings are no
  longer escaped as Values; input and schema/template string Values remain literal.
  Dry task bodies now project known fragments instead of splitting unresolved
  parameter controls across lifecycle sections. A possible conditional Process
  is checked after resolution. Regressions cover shell-selected sections,
  dynamic headings, if/unless/case/loop controls, assignment scopes, explicit
  overrides, deferred authored checks and committed replay. Independent known
  missing/invalid/duplicate sections, missing files and dependency cycles still
  fail without shell execution. Invalid resolved structures and references
  cannot be committed. Companion branches skip files that may be unselected.
  Root and nested parameters now share ordered configuration resolution:
  literal/reference Values resolve before configured explicit shell Values,
  and known settings expand before command execution. Self-dependent shell
  cwd/env settings report a cycle; winning caller settings can remove unused
  dependencies. Regressions cover declaration order, typed settings and env
  mappings, source-relative cwd (including bare names), parent/task/context
  precedence, per-key merging, shell-backed reference owners and policy denial,
  invalid known/resolved settings, committed replay, and inherited literal data.
  Native whole input/output mappings now preserve literal strings at direct
  Value slots without escaping nested data. Compiled metadata is not expanded
  again during snapshot inspection, and malformed mapping shapes return
  validation violations.
  Methodology preflight now tracks unresolved metadata paths separately from
  persisted task definitions. Known phase/gate/artifact/order violations still
  fail before parameter effects, while value-dependent constraints are checked
  after resolution. Candidate matching requires distinct producers for missing
  phases and artifact types; one deferred task cannot satisfy multiple types
  or phases. Explicit overrides take precedence even below a deferred mapping.
  Tests cover all deferred fields, whole hints, malformed sources and refs,
  invalid resolved constraints, enough/insufficient producers and real output
  approval creation. Relative required artifact refs use the methodology's
  origin and retained snapshots. Criteria are pinned before effects and reused
  for concrete validation, their digest joins revision context, and a changed
  methodology aborts publication. Source inspection is read-only. Non-mapping
  task hints now produce validation violations.
  Whole deferred output/budget mappings now preserve override precedence against
  artifact/profile defaults. Dry tasks retain a transient profile-default
  projection across validation calls; original snapshot bytes and persisted
  definitions remain unchanged. Known sibling defaults and independent artifact
  checks still fail before effects. Used defaults are validated after resolution.
  Tests cover invalid inherited values replaced by deferred mappings, unused
  template/output references, explicit overrides, malformed artifact contracts,
  source origins, literal strings, sibling allocation fit and projection isolation.
  Output references are captured after explicit task overrides have been merged.
  Allocation now resolves task/profile budgets before deriving missing dimensions,
  and persists the concrete caps. The confirmed profile-default probe is repaired:
  with 100 tokens available, a 5-token profile retains 5 tokens and an unallocated
  peer receives 95. Tests cover per-dimension explicit/profile precedence, zero
  caps, duration normalization, null/empty mappings, shell-resolved null budgets,
  invalid null amounts, profile caps exceeding the plan, non-root parent defaults,
  retained profile caps with measured usage, and runtime use after source deletion.
  Root allocation profiles do not create a global plan cap. The Elaborator now
  receives resolved parent limits. A shared validator rereads measured usage at
  preparation and under the writer lock before commit, with a regression for
  usage arriving at the final lock boundary. Invalid resolved budgets retain
  output failure classification.
  Nested Process companions now receive winning input/output defaults as well
  as runtime settings before their bodies compile. Parent and explicit task
  contracts mask unused descendant references; surviving child contracts keep
  their original source paths through grandchildren and source deletion. Native
  schema defaults merge by field. Unresolved whole/partial contracts defer
  potentially unused descendant reads during effect-free validation. Input
  discovery skips overridden input/parameter/contract Values, respects caller
  project origins, and preserves frozen materialized parameters. Eleven new
  regressions cover these cases.
  Artifact output contracts are now available before Process companions compile,
  matching execution precedence. Explicit artifact selection and source-relative
  contract snapshots survive source deletion. Dry validation defers descendant
  contract reads beneath unresolved artifact selections/fields; concrete used
  references remain required before commit. The early contract pass skips
  artifact checks, whose parameter Values are still evaluated once. Native
  artifact schemas compile unshadowed fields around partial task overrides.
  Seven further regressions cover these cases and failed concrete resolution.
  Guaranteed siblings now pass otherwise unset runtime/output defaults in source
  order, including nested contributions and configuration for later parameter
  Values. Frozen runtime branches do not contribute defaults to their caller;
  later fallback contracts remain available when the branch is skipped. Input
  and parameter defaults remain local. Task lifecycle sections and separate
  artifact check fragments retain independent composition contexts. Discovery
  follows ordered, unconditional inclusions, and inline Markdown is read as
  verbatim data without inspecting its frontmatter or nested directives. Eleven
  further regressions cover these cases, branch selection and source deletion.
  Composition contexts now follow headings reached by selected parameter control
  flow rather than all authored headings. Skipped if/unless/case headings keep
  adjacent companions in the same section; selected and parameterized headings
  separate their defaults. Comment/capture contents do not change the active
  section. Shell-selected boundaries preserve effect-free validation, while
  concrete selections still require used references before commit. Seven further
  regressions cover these cases, including a reproduced pre-fix failure where a
  skipped Post heading caused a read of an overridden, missing contract file.
  Materialized artifacts now select parameter-dependent section structure before
  splitting Post and Evaluation. Structural if/case/for controls and dynamic
  headings can span sections; body expressions compile only in selected check
  fragments. Unused artifact Process files and missing body expressions remain
  ignored. Deferred source sites carry lexical locals and snapshot advancing
  native loop counters, with source reads postponed until section selection.
  Selected duplicates and unresolved used references fail before commit; dry
  validation runs no parameter effects. Nine further regressions cover these
  cases, discarded branches, successful/failing shell selections, assigned
  headings, literal inline data, source deletion and opaque owner revision IDs.
  Runtime-selected paths inside artifact structural loops now retain captured
  locals/counters through task-owned scope sources without reading future files.
  Runtime inputs and selected paths stay live after application. Inline carriers
  preserve verbatim insertion; filename data resembling Liquid is rendered as
  data once. Carriers preserve semantic depth limits and reject recursive carrier
  selection independently. Discovery inspects expressions in file arguments, and
  inline markers now parse embedded Liquid expressions for files and shell.
  Eight further regressions cover these cases, including reproduced failures
  for lost loop locals, inner-expression parsing and implicit path inputs.
  Methodology artifact identity now matches catalogue references and local aliases
  of the selected file, including retained snapshots after deletion in both
  directions. Alias requirements deduplicate before matching deferred producers.
  Different files/selectors, unselected layers and distinct owner identities do
  not collapse; retained canonical identities keep their original selection after
  layer overrides. Eight further regressions cover positive and negative cases.
  An audit of spec 006's parameter materialization boundary confirms that runtime
  inputs select branches within fixed sections; cross-section structure that
  depends on runtime inputs is rejected before effects. Validation now catches
  Liquid syntax errors raised during dependency discovery and returns violations.
  Two further regressions cover rejection in task/artifact templates and accepted
  runtime branches within a check. Dynamic runtime section generation is not an
  additional feature required by the task-template contract.
  Further parity checks cover stateful `cycle`, increment/decrement and
  `ifchanged`, assignments/captures, loop offsets/reversal and table rows against
  ordinary one-stage rendering. Deferred sibling contracts inside a runtime
  branch preserve source ordering and fallback selection, including execution
  after original source deletion. Tests:
  `tests/test_006_15_integration_stateful_parity.py`.
  Two additional process-kill checks stop after parameter effects but before
  staging. Explicit repair and continuation repeat those uncheckpointed effects,
  while preserving the model receipt and publishing only one revision.
  This documents the recovery limit rather than claiming exactly-once effects.
  Six additional artifact-alias regressions cover zero-padded line selectors,
  deduplication before deferred-producer matching, distinct ranges with equal
  bytes, canonical custom-owner aliases with opaque revisions, retained owner
  selections after the owner disappears and archived definition artifact types.
  Equivalent numeric selections now use the same resource identity in methodology
  validation and source-snapshot lookup. This repairs false missing-artifact and
  distinct-producer errors without collapsing different resources or selections.
  Files: `features/elaboration/methodology.py`, `features/script/sources.py`,
  `tests/test_006_16_integration_artifact_aliases.py`.
  Remaining: wider structural parity, reference alias parity (including other
  resolver schemes and historical definitions), and wider contract/default
  composition (including sibling ordering inside deferred runtime blocks and
  lifecycle boundaries controlled by runtime inputs). Runtime-input-dependent
  cross-section structure remains intentionally rejected, as noted above.
  Failed or interrupted applications do not guarantee exactly-once shell effects
  before durable staging; effectful parameter commands must be idempotent or
  use external deduplication.
  Files: `features/elaboration/materialize.py`, `features/elaboration/parameters.py`,
  `features/elaboration/templates.py`, `features/elaboration/methodology.py`,
  `features/elaboration/metadata.py`, `features/elaboration/allocation.py`,
  `features/elaboration/sources.py`, `features/script/sources.py`,
  `tests/test_006_04_integration_materialization.py`,
  `tests/test_006_05_integration_parameter_config.py`,
  `tests/test_006_06_integration_methodology.py`,
  `tests/test_006_07_integration_deferred_defaults.py`,
  `tests/test_006_08_integration_budget_allocation.py`,
  `tests/test_006_09_integration_composition.py`,
  `tests/test_006_10_integration_artifact_structure.py`. Spec: 006.

- [x] **Implement or clarify the plan default allocation profile.** The root's
  profile supplies the plan default during materialisation. Template and
  explicit task selections take precedence, source-relative paths keep their
  origin, and the profile's memory setting survives effective task resolution.
  Tests cover precedence, source directories and invalid reference types.
  Files: `src/py/auto/features/elaboration/materialize.py`,
  `src/py/auto/features/elaboration/operations.py`. Spec: 006.

- [x] **Restore the downward dependency boundary.**
  Feature validation now lives in `wiring.validateContext`, with runtime
  configuration calling the composition boundary. An architecture regression
  checks that runtime modules do not directly import features.
  File: `src/py/auto/runtime/config.py`. Spec: 001.

## Agreed Specification Correction

- [x] **Align spec 008 with explicit goal-effect requests.** Retain the safer
  implemented behaviour: human authorization is given explicitly through
  `--effects`, never inferred from goal prose. Update Explicit Requests and any
  affected operation documentation to describe the explicit request channel.
  Keep human-only provenance and definition-digest expiry of goal grants.
  This is a specification correction, not a request to add inference.
  Files: `docs/specs/spec-008-plan-policy.md`,
  `src/py/auto/features/elaboration/operations.py` (existing behaviour).
  Explicit Requests and the PlanCreate signature now document `effects` and
  definition-digest expiry. Existing goal-grant regression tests still pass.

## Addressed in the Current Working Tree

These findings were present during the initial review but have since been
addressed. Their regression suites were included in the latest targeted run.

- [x] **Direct decisions end to end.** `DecisionRecord`, optional-gap decision
  construction, question/source/concerns persistence, the `record` revision,
  orphan-decision repair and CLI-only `decision record` exposure are present.
  Specs: 004, 005, 007.
- [x] **Preserve gap concerns in resolving decisions.** `GapDecide` now copies
  `concerns` from the gap. Specs: 004, 010.
- [x] **Canonical same-plan task dependencies.** Discovery recognizes canonical
  references, defers reading future outputs and normalizes same-plan task
  references for dependency/cycle validation. Specs: 003, 004.

- [x] **Retain historical definition references and reassessment.** Both task
  definition reference forms resolve removed-task bytes without changing their
  digest or relative origin. Root and archived companion-source lookups share
  definition selection, and missing sources produce Auto errors. Completed runs
  can be checked/evaluated after removal using saved checks/sources and current
  policy; checkpoints, caller restrictions and immutable receipts retain their
  existing guarantees. New ordinary runs pin canonical definitions and select
  the stored active task even when given an old model. Removed tasks cannot be
  restarted or have their ids reused. Definition inputs are assets rather than
  output dependencies; graph validation accepts definitions staged together,
  resolves retained assets and rejects missing ones. Definition pin/policy reads
  disable shell-backed resolvers. Missing cross-plan definitions and plan roots
  return domain errors rather than filesystem exceptions. Fourteen new regressions
  cover these cases and self-definition inputs without output cycles. Wider
  historical alias/selector and definition-transclusion parity remains to audit.
  Files: `features/plan/references.py`, `features/plan/policy.py`,
  `features/plan/operations.py`, `features/task/operations.py`,
  `tests/test_004_02_integration_history.py`. Specs: 002, 003, 004, 008.

  Six further historical-selector checks cover archived chains through nested
  headings and shorthand/canonical aliases, relative input origins, exact-byte
  line ranges with implicit dependencies, malformed/ambiguous selectors,
  cyclic imported archives, selected compiled companion sources after deletion
  and materialized consumers of archived definitions. Discovery, rendering and
  startup agree; definition sources remain assets rather than output dependencies.
  File: `tests/test_004_05_integration_historical_selectors.py`.

- [x] **Inspect task-definition transclusions as scripts.** Full definition
  transclusions now discover implicit inputs and nested output dependencies;
  definition data/inline values remain inert. Graph validation uses an ephemeral
  view of new/changed definitions to inspect same-revision sources before writes;
  this view is computed on demand without serializing unrelated tasks.
  Unchanged definitions preserve exact bytes and registered owners; captured
  snapshots win over staged updates. Generated validation bytes do not create
  synthetic provenance pins. Shared selectors validate staged Markdown/line
  references. Recursive expansion, nested output cycles and missing selectors
  fail before writes. Source shorthand aliases also expand for input discovery.
  Fourteen regressions cover readiness, staged/elaborated/archived sources,
  overrides, inline data, exact line ranges, owner restrictions and snapshot
  precedence. Wider nested-selector and dynamic-source parity remains to audit.
  Files: `features/script/operations.py`, `features/task/operations.py`,
  `features/plan/operations.py`, `runtime/operations.py`,
  `tests/test_004_03_integration_definition_sources.py`. Specs: 002, 003, 004.

- [x] **Distinguish selected resources in source-cycle checks.** Runtime,
  discovery and capture now retain the source reference/selector separately from
  its relative origin. Finite chains through different sections of one file or
  definition work; repeated selected identities still fail through local,
  catalogue and line-range aliases. Compiled variants and opaque owner resources
  remain distinct even at the same origin. Depth bounds remain enforced. File
  Values are data rather than transclusion frames. Ignored child lifecycle keys
  are dropped before validation/expansion, while `each` remains forbidden.
  A reproduced YAML wrapping defect changed backslashes in nested source text
  and invalidated its checksum; scalar wrapping is now disabled for serialization.
  Twelve regressions cover selector chains, resource aliases, owned origins,
  source deletion, own-source Values, ignored commands and nested checksums.
  Files: `features/script/model.py`, `features/script/config.py`,
  `features/script/templates.py`, `features/script/operations.py`,
  `features/task/model.py`, `features/elaboration/sources.py`, `utils/data.py`,
  `tests/test_002_01_unit_script.py`,
  `tests/test_004_03_integration_definition_sources.py`,
  `tests/test_006_11_integration_selector_sources.py`. Specs: 002, 003, 006.

## Verification and Documentation Gaps

The final full-suite run completed 596 tests in 532.849 seconds: 590 passed,
and only the six opt-in live checks were skipped. All four OpenCode live checks
were also run explicitly and passed in 72.547 seconds: text generation, API-seeded
controls, model-generated questions/resumption/cancellation, and model-requested
permission approval/rejection across independent Python processes.
`testClientWireContract` and `testMcpStdio` both passed. The sibling Memo checkout
must be supplied using an **absolute** `PYTHONPATH`: subprocess tests change
working directories, so relative paths incorrectly hide Memo from CLI children.
The final targeted run passed 68 tests across engines, cancellation, application
recovery, stateful templates, CLI/MCP, budget combinations, consolidation crash
recovery and prompting. Ruff's F checks, Python compilation and
`git diff --check` passed.

Follow-up cancellation audit: standalone scripts now check cancellation before
resolving loop/input Values and before advancing engine streams, including
answer delivery and late terminal events. Cancelled or already-expired subprocess
requests are rejected before process creation; deadline calculation no longer
leaves a just-launched process outside the cleanup guard when it raises.
Six regressions cover these boundaries and cancellation at all four standalone
lifecycle command types. Together with the historical-selector and question-crash
checks, the follow-up targeted run passed 66 tests. Files:
`features/script/operations.py`, `integration/process.py`,
`tests/test_002_04_unit_cancellation.py`. The completed follow-up full-suite
result is recorded above.

Eight lifecycle regressions now cover seven process-kill boundaries around
Apply and output-gate publication, plus Evaluation question resumption after
Apply. The crash checks use real Memo storage, retain known usage and immutable
terminal receipts, verify live workers are not repaired as interrupted, and
show repair/continuation do not replay effects. Partially published gates recover
as one gap, and dependents cannot consume a successful gated output until its
explicit approval. Existing implementation passed these checks without changes.
File: `tests/test_009_06_integration_lifecycle_crashes.py`. Specs: 003, 004, 009.

Elaborator application now observes cancellation before preparation, parameter
effects and commit. Continuation records a cancelled application as `cancelled`
and stops before starting the next task when cancellation arrives after commit.
A Stop hold also interrupts an operation worker without an active task run.
Four regressions cover a real parameter subprocess, an already-cancelled call,
the post-commit selection boundary and a live Stop hold. They preserve the
successful Elaborator receipt and verify later explicit continuation can apply
its pending output without rerunning the model.
Files: `features/elaboration/operations.py`,
`features/orchestration/operations.py`,
`tests/test_009_05_integration_application_cancellation.py`. Specs: 006, 009.

A fifth regression covers an Elaborator that first waits for an answer and
then completes while continuation resumes it. Its application previously ran
outside the supervised worker, so a Stop hold could not interrupt a parameter
subprocess. Resumed application now follows the same supervised, cancellable
path as other pending applications. The check preserves the successful receipt,
prevents task publication while held, and verifies explicit release/continuation
later applies one revision without rerunning the Elaborator.

Continuation now recovers staged plan revisions and applies successful pending
Elaborators before selecting tasks. Previously it could reject a staged commit
or finish an empty root while the successful Elaborator's tasks remained
unapplied. Recovery changes are included in its event stream. Already committed
applications skip output rereads. Six process-kill regressions cover success
before application, staging before journal append and journal append before
cleanup, through both `PlanRepair` and `PlanContinue`. They verify one revision,
one model invocation, retained usage and immutable receipts, source deletion
after commit, and no repetition of parameter effects captured by staging.
Files: `features/orchestration/operations.py`, `features/plan/gaps.py`,
`tests/test_006_14_integration_application_recovery.py`. Specs: 004, 006, 009.

Workspace exclusion now recognizes `repo.write` and dotted descendants in both
declared effects and human grants. Similarly named independent effects do not
claim the workspace, and retained grants scoped to removed tasks do not make
active work writable. Five concurrency regressions cover declared/granted
subeffects, lookalikes, removed scopes and a grant added after continuation
startup; a newly conflicting write grant stops before task execution.
Files: `features/orchestration/operations.py`,
`tests/test_009_04_integration_workspace_effects.py`. Specs: 008, 009.

Run input overrides now drive selected-task readiness, output gates and
execution consistently. Explicit `TaskStart` inputs win over context inputs;
the plan's stored-definition graph does not inject caller values into unrelated
tasks. Added references block before execution and real cycles remain invalid;
replacement values suppress unused default dependencies. Eight regressions
cover direct/context precedence, transcluded definitions, canonical provenance,
approval gates, question resumption without repeated shell values and malformed
input arguments. Files: `features/plan/operations.py`,
`features/task/operations.py`, `tests/test_004_04_integration_run_inputs.py`.
Specs: 002, 003, 004.

Parameter-known loop exits now participate in task and artifact materialization.
`break` and `continue` no longer force an otherwise parameter-known loop to
remain at runtime, and artifact structure selection executes exits even when
their branch has no headings of its own. Runtime-controlled exits retain their
loops; controls using body-assigned names remain conservatively deferred.
Six regressions cover task/artifact sections, skipped source capture, nested
loop counters and parity for runtime conditions, alternatives, assignments and
captures. Files: `features/elaboration/templates.py`,
`tests/test_006_13_integration_loop_controls.py`. Specs: 002, 006.

Frontmatter and section selection now share LF/CRLF boundary handling. CRLF
defaults no longer disappear, headings inside YAML cannot become selected body
sections, and unclosed frontmatter fails consistently. False-like nonmapping
YAML values are rejected. Six parser/resolver regressions and one elaboration
regression cover raw bytes, owner digests, relative inputs, selected-source
snapshots and artifact checks after source deletion.
Files: `utils/data.py`, `tests/test_002_03_unit_line_endings.py`,
`tests/test_006_11_integration_selector_sources.py`. Specs: 002, 003, 006.

Lifecycle command validation now runs before task-template compilation and
artifact command compilation/composition. Invalid mappings, booleans, numbers,
null and mixed lists cannot become valid command lists or disappear through
truthiness checks. Four regression tests cover owned templates, ordinary and
materialized artifacts, ignored artifact lifecycle keys and command ordering.
Files: `features/elaboration/materialize.py`, `features/elaboration/sources.py`,
`features/task/operations.py`,
`tests/test_006_12_integration_lifecycle_commands.py`. Specs: 002, 003, 006.

Historical verification before this coverage pass: 539 tests passed with the sibling Memo checkout
on `PYTHONPATH`; lint (`ruff check --no-cache --select F src/py tests`), Python
compilation and `git diff --check` passed. Two subprocess tests were excluded
from that run: `testClientWireContract` failed in the sandbox, and
`testMcpStdio` stalled during the bounded full-suite attempt. The full suite
was therefore unverified at that point. The completed full-suite run above
supersedes those transport exclusions; live-provider limitations remain listed
below.
The suite includes MCP projection checks with inline dispatch and agent,
resolver, profile, form, consolidation, scheduler and template-materialisation
regressions. A direct Node
subprocess probe returned `EPERM`; bridge wire assertions pass in process,
while its executable fixture timed out. No live provider call had been made
at that point; the bounded OpenCode smoke test described below now passes.

- [x] Complete nested-budget, retained-sibling, retry and reassessment coverage.
  Startup/manual-process reservations, exact limits, empty aggregates and
  explicit/implicit resumption have regressions. Six further scenarios cover
  retained successful siblings during explicit retries, prior failed usage,
  live budget changes, cost/time resumption and nested aggregate reassessments.
  Receipt bytes and measured cumulative usage are checked.
  Separate allocation conformance from plan-wide execution enforcement,
  which belongs to orchestration.
- [x] Test crashes during Apply, output-gate creation and Elaborator application.
  Elaborator application now has six process-kill checks at the boundaries
  described above. Seven more process-kill checks now cover Apply before/after
  effects and checkpointing, plus output gates before creation, after staging,
  after creation and after successful receipt publication, using real Memo.
  Persisting completed phases alone does not establish exactly-once external
  effects; inspect and document recovery limits.
- [ ] Exercise live Pi, OpenCode and LLM execution, especially durable questions,
  permissions, resume and cancellation.
  Opt-in smoke checks now live in `tests/test_001_04_live_engines.py`:
  `AUTO_LIVE_ENGINES=1` enables real provider calls; per-engine model overrides
  use `AUTO_LIVE_PI_MODEL`, `AUTO_LIVE_OPENCODE_MODEL` and `AUTO_LIVE_LLM_MODEL`.
  Tests run in empty temporary directories. Smoke/API checks use a 45-second
  budget; provider-driven durable control runs use 90 seconds, with bounded
  subprocess waits.
  OpenCode's real generation passed. Pi 0.71.1 exposed unsupported `--no-mcp`
  and `--no-approve` flags and a missing `agent_end` terminal event mapping;
  the adapter now uses supported isolation flags and accepts both current
  `agent_end` and legacy `agent_settled`, with success/error/aborted regressions.
  Pi provider verification remains blocked by account/model compatibility
  errors, including an explicit `openai-codex/gpt-5.3-codex` attempt.
  LLM remains blocked by its missing OpenAI API key. No credentials or user
  configuration were changed. A real OpenCode check creates native forms and
  permission requests through the API, detaches, JSON-roundtrips the adapter
  checkpoint, and replies through a fresh adapter, without provider inference.
  Two further live checks now exercise model-generated questions and file-read
  permission requests: another Python process answers/cancels the question or
  approves/rejects the permission. Pre executes once, the engine session is
  retained, approved work returns the fixture content, and native pending
  forms/permissions are cleared. All fixture sessions are deleted afterward.
  Permission rejection exposed a terminal assistant error without an `idle`
  marker; the adapter now recognizes terminal assistant errors instead of
  polling until timeout, mapping native `aborted` errors to `Cancelled`.
  Two wire regressions also cover provider errors and an older assistant error
  preceding a new turn. OpenCode's named live control paths now pass; the
  remaining live-engine blockers are Pi account compatibility and LLM credentials.
- [x] Add conformance checks for the confirmed defects above. Shared lifecycle
  cancellation now prevents input shell Values, new phases and unstaged
  consolidation publication after cancellation, independently of engine
  cooperation. Regressions cover an already-cancelled task, cancellation at
  model completion and cancellation after mandatory consolidation validation.
  Existing fixture tests still do not establish complete specification coverage.
- [x] Refresh remaining stale scope statements in `TODO.md` and
  `docs/implementation.md`: policy and prompting are substantially implemented;
  orchestration and memorisation are specified work, not blanket exclusions.
  Preserve the documentation updates already present in the working tree.

## Not Missing in This Increment

Automatic retries, parallel task execution, workflow engines, cross-plan
scheduling and isolated workspace creation remain explicitly out of scope of
spec 009. They should not be counted as conformance defects for that increment.
Capture behavior for specific external interfaces is explicitly outside spec
010's scope; its capture rules belong to those interfaces' specifications.
