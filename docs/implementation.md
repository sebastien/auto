# Implementation decisions

## Structure

`src/py/auto/runtime` owns context, configuration, budgets, statuses and
reference/value resolution. `Context.runtime` is the gateway to integrations;
`runtime/integration.py` defines the engine and knowledge contracts, and
`wiring.py` is the composition root that installs feature reference schemes
without the runtime importing a feature. Features own script, task, plan,
elaboration and orchestration operations in that order, with dataclasses for the specified
entities. `integration` adapts subprocesses, engines and Memo. `app` projects
shared operation metadata into CLI and MCP. V1 informed adapter behaviour,
rather than determining the new architecture.

`contextFor` delegates feature configuration checks to `wiring.validateContext`;
runtime modules do not import feature modules. Resolver registration preserves
explicit Python resolvers, fills missing built-ins, and dispatches the most
specific prefix. Configured external schemes use an argv-based integration,
bounded output and explicit unavailable-reference errors.

Agent definitions receive their specified Memo reference defaults without
mutating configuration. `memory.default_agents` supplies an explicit default
selection, recorded in each new plan. `agent` and `catalogue` remain reserved
plan IDs. An inherited allocation profile retains its original reference
directory; explicit task and template selections take precedence, including
the profile's `memory` setting.

Task templates now have a parameter expansion stage during elaboration
application. Known parameter branches, loops and metadata expand before task
sections are parsed; elaborated fields and sections then override the template.
Runtime inputs and authored transclusions retain their execution-time behavior.
Content-addressed literal bindings preserve parameter data across the second
Liquid render, including data resembling commands or lifecycle headings.
Resolved parameter sources join revision provenance, and supplied template
digests must match. Application preparation runs outside the plan writer lock;
the final revision checks that the plan has not changed. A separate lock for
the originating run makes replay of a committed application idempotent.
Known local and catalogue companions, nested parameter files and output
contracts are stored as source snapshots in the task definition. Their
original references, digests, selectors and source locations survive changes
or deletion of the originals, including staged-revision recovery. Discovery
uses the captured Markdown for inputs and dependencies. Applied parameters
remain fixed in companions; runtime inputs and paths selected by runtime
inputs retain their current values. Checksums and byte/depth bounds constrain
snapshot loading. Source inspection, dry validation and input discovery
disable shell-backed references as well as explicit shell Values. Application
resolves parameter Values in a separate context with current shell policy,
including defaults in nested Process and artifact companions. Those Values
use inherited configuration and source-relative working directories; parent
parameter overrides do not execute unused defaults. Each inclusion stores its
resolved values for execution and committed replay. Authored shell/check
directives remain deferred until execution. Selected profiles and
artifacts also use captured sources, including the inherited plan profile and
owner-resolved framework references. Artifact contract defaults and composed
Post/Evaluation fragments retain their origins and task override precedence;
unused artifact Process content is not composed.

Markdown companions compile per inclusion scope. Their task-owned references
(`auto:{plan}/task/{id}/source/{hash}`) identify compiled documents with literal
bindings and captured outer locals, preserving unrolled iterations and parent
loop counters without assigning into the caller. Runtime inputs and assignments
remain deferred; even late-selected descendants inherit the captured scope.
Discovery distinguishes identical bodies with different metadata and treats
source references as definition assets rather than output dependencies. The
compiled sources remain resolvable from archived task definitions. Materialized
templates also compile their inherited artifact Post/Evaluation fragments,
frontmatter commands and used contract defaults. The selected artifact identity
is preserved, with its compiled checks bound to the captured original revision.
Unused overridden contract defaults are not expanded. Ordinary tasks continue
to use runtime parameter overrides. Generated template source/binding/scope
metadata is produced internally rather than accepted from elaboration output.
Before compiling companion bodies, task templates and companions pass their
winning input/output defaults to descendants with source-relative references
relocated to their authoring directory. Parent and explicit task contracts mask
unused child file references; native schema mappings retain unoverridden child
fields. Dry inspection propagates unresolved contract overrides without reading
potentially unused descendant defaults. Input discovery applies the same caller
and parent precedence, resolves explicit caller Values from the project, and
keeps materialized parameters frozen. Materialization also inspects the selected
artifact's used contract before compiling Process companions, matching execution
precedence. This early pass skips artifact checks, so their parameter Values
still resolve once in the later check-compilation stage. Deferred artifact
selections and contract fields postpone potentially unused descendant reads;
the concrete selection must resolve used references before commit. Native
artifact schema fields compile individually around task overrides. Guaranteed
sibling inclusions now contribute otherwise unset runtime/output defaults in
order, including contributions from descendants. Deferred runtime branches are
inspected without contributing to the caller, preserving later fallback
contracts. Input and parameter defaults remain local to their inclusion.
Task lifecycle sections and separate artifact check fragments have independent
composition contexts. Section selection follows headings actually reached by
parameter control flow, so skipped headings and comment/capture contents do not
break sibling inheritance. Selected parameterized headings start independent
contexts. Shell-selected boundaries defer validation until their Values resolve;
used references must then resolve before commit. Discovery follows source order
for guaranteed siblings and treats inline Markdown as verbatim data. Sibling
ordering within deferred runtime blocks has parity regressions, including
selected/skipped branches and source
deletion. Runtime-input-dependent cross-section structure remains rejected.

Materialized artifacts select parameter-dependent section structure before
splitting Post and Evaluation. A first pass expands controls that govern
headings while retaining body expressions for a second pass over the selected
check fragments. Parameter-known `break` and `continue` participate in that
structural pass, including exits controlled by loop locals and counters.
Runtime-controlled exits stay inside their loops; controls using names assigned
by the loop body remain conservatively deferred.
Unused Process bodies do not resolve files or missing body
parameters. Deferred source sites preserve loop locals and snapshot native loop
counters without reading files until the selected fragments are known. These
sites are replaced by task-owned captured sources during body compilation.
Shell-dependent structure remains effect-free during dry validation and is
checked again before commit; selected duplicate sections and missing files
fail. Runtime-selected paths inside structural loops now use task-owned sources
that carry captured lexical values while leaving the path and inputs deferred.
Known inline/non-Markdown sources use the same carriers to preserve literal path
data. Inline carriers render the authored insertion without reparsing inserted
content. Carriers preserve semantic transclusion depth and track their own
recursion separately. Input discovery inspects expressions in file arguments;
inline file/shell markers accept embedded Liquid expressions. Task sections are
fixed during parameter materialization; runtime inputs can select branches
within those sections. Runtime-dependent cross-section structure is rejected
before parameter effects. Elaboration validation reports Liquid syntax failures
from dependency discovery as violations instead of leaking parser exceptions.
Dry validation omits metadata leaves whose values depend on unevaluated shell
parameters, while checking known siblings. This permits shell-derived budget
dimensions, output formats and framework selections without executing them
during preflight. Application validates the concrete result again before the
revision is committed, including budget fit and selected framework resolution.
Only string Values in input bindings and output schema/template bindings are
escaped; output formats and strings inside native objects remain ordinary data.
Dry body compilation also omits blocks that depend on unresolved parameters,
keeping complete known Liquid fragments for section and dependency checks.
A potentially supplied Process is checked after parameter resolution; known
missing Process sections, invalid headings, duplicate sections and invalid
sources outside unresolved blocks still fail before parameter commands run.
Selected structures and references are fully checked before commit. This also
prevents inspection of files in unselected companion branches.

Root and companion parameter resolution now shares an ordered configuration
stage. Literals and configuration-independent references resolve first, then
known execution settings expand before explicit shell Values run. Shell-backed
references retain their resolver context and current shell authorization. An
explicit shell Value requiring its own unresolved result for `cwd` or `env`
reports a configuration cycle. Parent, explicit task and context overrides keep
their precedence, with per-key environment merging and source-relative working
directories (including bare relative names). Already-compiled inherited values
remain data. Native input/output mappings from parameters escape only their
direct string Value slots; metadata inspection does not render compiled strings
again.

Methodology validation records deferred metadata paths outside task documents.
Artifact requirements match catalogue references and local aliases of the same
selected file, including captured originals after deletion. Alias requirements
count once when checking deferred producers. Selectors, different files,
unselected catalogue layers and distinct custom-owner references remain distinct;
captured canonical catalogue identities retain their original selection when a
new layer appears.
It checks known phase membership, dependency ordering and approval gates, and
requires enough distinct candidate tasks to cover unresolved phase/artifact
requirements. Explicit fields override deferred template ancestors. Required
artifact references keep the methodology's source origin and compare against
the tasks' captured artifact identities, including retained snapshots after
source deletion. Application uses the same pinned methodology criteria for
preflight and resolved validation, records its digest as revision context, and
aborts if the source changes before commit. Methodology inspection cannot run
shell commands.

Dry tasks carry a temporary profile-default projection outside frontmatter.
Deferred output and budget mappings can replace inherited defaults without
premature validation of values whose use depends on resolution. Known siblings
and independent artifact checks still validate. The projection survives nested
validation calls, preserves captured profile bytes, and is absent from stored
tasks. Output references are captured after explicit task overrides, so unused
template contracts do not force missing-file reads. Selected references keep
their origins and snapshots.

Elaboration allocation and validation now use the same resolved task/profile
budgets. Declared dimensions are deducted before unspecified dimensions receive
a share; profile defaults survive as concrete allocations. Null or absent task
budgets leave dimensions unspecified, while individual amounts must remain
nonnegative finite numbers. Retained profile allocations reserve only their
unspent portion, and measured plan usage is counted once. Non-root parent limits
use their resolved budgets; the root's default profile does not add a global
plan cap. The Elaborator receives the same resolved parent limits. Allocation
validation rereads usage under the plan writer lock immediately before revision
publication. Resolved budget violations remain output failures. Wider structural
and reference parity and transitive contract/default composition audits remain
incomplete; see `TODO_SPECS.md`.

Stateful Liquid parity checks cover counters, cycle groups, `ifchanged`,
assignments/captures, loop offsets/reversal and table rows. Real worker-kill
checks explicitly demonstrate the pre-staging recovery limit: repair can repeat
parameter shell effects that completed before their results became durable.
Commands with external effects must therefore be idempotent or deduplicate
externally; only staged/committed application replay avoids repeating them.

Methodology artifact matching and captured-source lookup normalize numeric line
selector aliases (`L1-1` and `L01-001`) consistently with script resource identity.
Canonical custom-owner references keep opaque owner revisions and remain usable
from retained snapshots. Different line ranges, owner identities or resources do
not become aliases merely because their selected bytes or origins match.

Runs of plans with agents capture Memo wake layers once, before resolving task
inputs. Memo material and its clock remain fixed during multi-agent allocation;
world and recent allowances are shared, preserving each layer's admission
minimum. A profile whose shared allowance cannot cover those minimums fails
explicitly. Agent prompts precede world and recent context, followed by current
capture instructions and the rendered task. Memory text is never rendered as
Liquid. Resumes and reassessments reuse the saved layers while capture and
execution authorization use current configuration. Capture is performer-owned:
Auto supplies the Memo command, instance/source reference and policy; it does
not append observations or enforce a shell backend's individual tool calls.

Receipts retain per-agent layer revisions, loaded UTF-8 bytes, allowances and
omission metadata. An adapter may emit `ScriptMeta` named `memory_cache`, with
`value = {agent, layer, cached_bytes}`, to record its latest explicit measurement
for that layer. Unknown agents/layers and measurements outside loaded bytes
are rejected. Global provider cache totals are never attributed to individual
layers. Existing provider adapters do not yet emit this optional measurement.
Elaborators use `full` memory and gain Memo model views (including peers) as
context. Default `learnings` retains plan learnings plus agent layer metadata;
the actual wake text appears once in the common prefix.

Agent consolidation reuses the task lifecycle through `RunScopeIntegration`:
the scope owns storage, variables, tool authorization, mandatory Post validation
and final application. It creates no synthetic plan or human gaps. The built-in
Memoriser and MemoryConsolidation artifact are ordinary catalogue entries;
custom Memorisers retain model checks and evaluation. Auto's shell transclusions
are disabled in this scope; engine permission requests allow only authorized
read effects. Native tools in backends without permission enforcement remain
advisory and are reported as unenforceable.

An agent lock serializes consolidation across processes; a separate short
request lock coalesces at most one pending request. Before consuming a request,
Auto publishes its run and saves the request in that run. The adapter compacts
the observation log, captures all pages of due work with consistent revisions,
and supplies only cited record references, not their contents. A mandatory dry
Memo check runs after task Post and before Evaluation. Changes must retain the
run ID and captured log revision, and satisfy current memory policy. Confirmed
effects belong in proposals; denied effects cannot be proposed.

Auto stages the exact validated change set before publication. Memo retains
its frozen unit plan before applying it and publishes the keyed outcome last.
After interruption, Auto retries that same pass before starting another model;
completed appends and reports remain unique. Partial progress is saved in
`application.json`. Terminal receipts stay immutable; reconciliation appends
to the run journal and updates the application projection used by status. A
fresh pass rejected as stale before any publication fails as output and can be
retried with fresh inputs. A no-op is complete without claiming publication.

Decision inputs are pinned references selected from repaired `GapResolved` and
`DecisionRecorded` plan events. Agent concerns filter labelled decisions;
unlabelled decisions reach every selected agent. Inputs capture each plan's
event high-water mark before the model runs. Successful application advances
`cursors.json`, including after recovery of a completed application whose
cursor publication was interrupted. Failed or rejected changes do not consume
decisions, and decisions arriving during execution remain for the next pass.

The project housekeeping ledger stores cumulative known run usage and UTC
calendar day, Monday-start week and month buckets, separately from plan usage.
Measured deltas are charged to the window in which they are reported. Active
runs reserve their remaining budget across window boundaries. A short project
writer lock admits reservations atomically across agents and processes; a free
agent kernel lock, never a timeout, permits releasing an interrupted worker's
unused reservation. Durable receipts recover known usage if ledger publication
was interrupted. Inconsistent totals fail closed instead of resetting capacity.

Earlier configuration allowance caps and periods remain independent constraints;
project settings cannot loosen user restrictions. Explicit human overrides may
waive project/agent caps from project configuration, while user caps and per-run
budgets still apply. An allocation exceeding available capacity becomes Blocked
and its request remains pending. Status reports usage, reservations and remaining
capacity. Staged application recovery proceeds without requiring a new model
allocation.

Durable plan event publication calls a composition gateway that queues optional
`plan-ended`, concern-filtered `decision` and `backlog:N` triggers. It never runs
a model while holding the plan writer lock. Queue cursors are distinct from
decision-consumption cursors. Pending requests and their durable run copies
retain trigger high-water marks, closing the crash window between queue and
cursor publication. Before claiming runnable work, the Memo adapter checks for
the retained application contract, usable stores and an agent identity.

Invocation checks repair source events, retry unavailable triggers and reconcile
incomplete applications, then process at most one pending request per agent.
CLI operations flush their primary result first; MCP operations schedule an
independent task rather than awaiting housekeeping. Neither path replaces the
main result with a maintenance failure. Agent status includes retained maintenance
diagnostics. Background passes use the housekeeping author, never waive caps,
and set `AUTO_HOUSEKEEPING=1` for child Auto commands to prevent recursive drains.
New requests arriving during a pass wait for the next invocation. No daemon is
created, and deferred requests are retried when a later invocation finds capacity.

Fixtures exercise real Memo publication and recovery, and independent processes
contend for one project allowance. Scheduler tests cover queued triggers,
coalescing, unavailable Memo contracts, lost cursor publication, allowance rollover,
writer-lock release before inference, CLI flush ordering and independent MCP
scheduling. Live-provider execution and wider process-crash/cancellation
conformance, including contention on Memo's own maintenance locks, remain unverified.

Runtime vocabulary is declared once, in `runtime/model.py` (event names, run
states, failure kinds) and in `features/plan/model.py` (plan, gap and revision
vocabularies). Features, interfaces and adapters reference those symbols
instead of repeating literals. Deferred imports are reserved for the
runtime-to-adapter boundary, the composition root, interface startup, and the
plan/task and plan storage/event projection cycles; other imports stay at module top. Task runs belong to plans
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

Every engine invocation (script, task, elaboration, assessment or memory run)
appends one line to `.auto/usage.jsonl` when the project has an `.auto`
directory: `ts`, `project`, `plan`, `kind`, `run`, `task`, `engine`, the
`provider` and `model` reported by the engine (else configured), `status`,
`failure`, `duration_s`, and the invocation's `tokens` and `cost` delta.
Usage is `null` when the engine reported none; `usage_complete` is true only
for engines that report full usage (OpenCode, Pi). Task runs add the lifecycle
`phase` and the repair `attempt`. Engines may report a `tokens` ScriptMeta
breakdown (`runtime.model.DETAIL`: `turns`, `input`, `output`, `reasoning`,
`cache_read`, `cache_write`), recorded as is; when no cost was reported and the
model has a price (`[prices]`, else built in), `cost_estimate` is added.
`UsageStages` totals a plan's lines by stage (planning, tasks, checks, repairs,
memory) for the guided summary's `spending`. A resumed run logs one line
per invocation under the same `run`. Set `journal = false` to disable it.

Plan mutations hold a filesystem writer lock and stage `.pending.json` before
publishing files, Memo records and a revision. `PlanRepair` rolls staged work
forward, reconciles orphan Memo records, repairs plan events and applies
successful pending Elaborators. Elaborator application is idempotent by run ID.
Memo retains ownership of its metadata and optimistic revision checks.
Continuation also recovers staged revisions and applies successful pending
Elaborators before selecting work. Its event cursor includes recovery changes,
so their revisions are delivered before runs of the recovered tasks. An already
committed application is skipped before reading its output again. Process-kill
tests cover success before application, staging before journal publication and
journal publication before staging cleanup, through both repair and continuation.
They preserve terminal receipts and measured usage and do not repeat the model
or parameter effects already captured by a durable staged revision. Interruption
before staging still cannot guarantee exactly-once arbitrary external effects.
Application checks cancellation before preparation, parameter effects and
commit. Continuation distinguishes cancellation from application failure and
checks again after an operation finishes, before selecting the next run.
Stop holds cancel its operation worker even when a successful Elaborator has
left only application work in progress. The successful receipt stays immutable;
an interrupted, uncommitted application remains available for explicit recovery.
Lifecycle crash tests also kill workers immediately before Apply, after its
effects but before the checkpoint, after the checkpoint, and around output-gate
staging, creation and successful receipt publication. Interrupted active runs
become system failures without retrying effects; successful gated receipts keep
their approval requirement. The tests use durable Memo records and preserve
known usage and terminal receipts across repeated repair and continuation.
An Evaluation question resumed after Apply also leaves its effects unchanged.

Run creation, waiting and completion publish plan events after their state or
receipt is durable. Revisions publish their events after the journal append.
`events.jsonl` is serialized by the plan writer lock; a sequence reservation
file prevents repaired events from reusing cursors. A durable status projection
checkpoint retains plan end transitions across later retries and revisions.
`PlanRepair` reconstructs missing events from the source records and these
checkpoints without executing resolved waiting runs.

`PlanContinue` owns a nonblocking `continue.lock`, runs one operation at a time,
and streams durable events while the operation runs in a worker thread.
Separate continuation records preserve stop reasons for repair. A writing plan
also reserves its Git working tree. Declared `repo.write` effects and dotted
descendants, and grants at plan or active-task scope, require this reservation.
Unrelated names such as `repo.writer` and grants scoped to removed tasks do
not. New write grants are checked before the next task starts. Locks retain
their inode; an interrupted
continuation is marked cancelled only after its process lock can be acquired.
Pause/Stop and release are human-authored revisions; task startup honors holds.
Closing a continuation stream propagates cancellation to the active run before
releasing its locks. Failed runs are never automatically retried.

Task startup takes the run's lifecycle lock before publishing Running and
retains it through execution. Reentrant lock bookkeeping preserves that
ownership when the plan writer lock is released first. Repair, continuation
and explicit task starts probe active run locks without blocking: a free lock
proves the lifecycle worker has stopped, so its last persisted usage is retained
in a Failed/system receipt. Live and Waiting runs are left alone. Discovery of
an interrupted worker stops the continuation before further work; this does
not replay incomplete phases or infer that external effects were undone.
Interrupted continuation records recover missing run IDs from the receipts.
Tests use killed manual/continuation processes and competing repair processes;
wider phase and cancellation crash windows still need verification.

Measured usage equal to a limit is permitted; exceeding it fails and retains
the measured usage. A new engine request requires remaining token/cost
capacity, so finishing at an exact limit does not admit another model check.
Shell checks can still run within the time limit. An empty aggregate with no
lifecycle checks requires no tokens/cost and can assemble outputs after its
children exhaust those dimensions, including nested aggregates.

The composition root connects task start/resume to the plan's budget
admission. Under the plan writer lock, admission subtracts measured usage and
the unspent budgets of other Running/Pending runs, then publishes the admitted
budget in the active receipt before releasing the lock. Waiting runs release
their reservation and re-admit on resume. Explicit remaining allocations must
fit; unspecified dimensions use remaining capacity without charging prior
usage twice. An active worker without a bound in a newly limited dimension
reserves the available capacity. Races between continuation selection and
manual spending stop as budget-pending before another engine starts.

Reassessments reserve their remaining capacity in a run-local `assessment.json`
under the same writer lock. Run reads project checkpointed usage while the
original terminal receipt remains unchanged. Completion appends an identified
`AssessmentUsage` charge before deleting the reservation; readers deduplicate
that handoff. A free lifecycle lock permits repair to charge known interrupted
usage and release unused capacity, without replaying the check. Tests cover
competing checks in threads and processes, manual startup contention, killed
checkers and interrupted checkpoint deletion. Additional combinations cover
budget reductions/increases while a worker is live, cumulative token/cost/time
resumption, explicit retries retaining successful siblings, failed-attempt
charges and nested aggregate reassessment at the exact plan cap. Revising a
plan cap changes future admissions, not a live worker's already-issued budget.

The shared lifecycle checks cancellation before input resolution, each phase
and agent-scoped application. It does not rely solely on an engine adapter to
notice cancellation. Cancellation at model completion skips subsequent checks
and Apply; cancellation after mandatory Memoriser validation prevents staging
and Memo publication. Already-published external effects are not undone.

Standalone scripts also reject cancelled input/loop resolution and check
cancellation before advancing an engine or accepting its terminal result.
An answer gathered during cancellation is not delivered to the engine.
Subprocess admission checks cancellation and the time budget before launching;
an expired deadline cannot strand a newly created process outside cleanup.
Question crash checks preserve a durable Waiting session for explicit resumption
without replaying Pre. A worker killed while resuming instead becomes a durable
system failure with its decision and measured usage retained, without replaying
the model or Apply.

Successful resumed Elaborators apply their output through the continuation's
supervised worker, so Stop holds can interrupt parameter effects even though the
model run has already ended. Their successful receipts remain immutable and the
pending output can be applied after release. After-success refinement hints also
use shared result handling: they run for a task resumed after a question, not
only for a task completing in its initial invocation.

`GapDecide` records a decision without resuming work. Continuation handles
resolved waiting runs at its next selection, and caps execution with the plan's
remaining budget. `RunAnswer` explicitly answers and resumes one run, including
from interactive task prompts. Deferred elaboration and after-success
refinement hints are dispatched by continuation through `PlanRefine`.

Decisions made without a gap use `DecisionRecord` and a `record` revision.
They omit the `gap` property in Memo, retain their question and source, and may
be recorded on ended plans. Repair reconciles these records by decision ID,
independently of gap resolution. Decisions resolving a gap inherit its concerns.

Dependency discovery treats canonical references to tasks in the current plan
like their `task:` shorthand, including references inside transcluded scripts.
Discovery defers reading task outputs until execution, so proposed dependencies
can be validated before their first run.
`TaskReady` and `TaskStart` evaluate the selected task with its caller inputs;
explicit `TaskStart` inputs take precedence over context inputs. The same
winning values are saved for execution and question resumption. New task-output
inputs block until their producers succeed and satisfy output gates; replaced
defaults no longer create unused dependencies. The plan's definition graph
does not apply these caller values to unrelated tasks, preventing artificial
self-dependencies during status recording. Runtime dependency cycles remain
invalid, and hierarchy edges still participate in readiness.
Definition references are assets rather than output dependency edges. Graph
validation accepts definitions staged in the same revision and verifies other
definition references against active or retained storage. Removed definitions
resolve through both shorthand and canonical references with their original
bytes, digest and relative-reference origin. New ordinary runs pin the canonical
definition rather than a file path that disappears on removal. Ordinary startup
uses the stored active definition even if the caller supplies an old task model;
removed tasks cannot be restarted or have their ids reused.
Definition pinning and policy inspection disable shell-backed resolvers.
Full `@task:.../definition` and canonical definition transclusions are inspected
as scripts, discovering their implicit inputs and nested output dependencies.
Scalar definition inputs and inline insertions remain verbatim data. Graph
validation supplies a temporary view of new or changed definitions so tasks
staged together can be inspected before writes. The view is computed on demand
without serializing unrelated tasks. Unchanged definitions retain exact stored
bytes for line selectors and use their registered owners; captured
source snapshots retain precedence. The temporary view does not add synthetic
owner pins to provenance. Shared content selection validates Markdown and line
selectors for staged definitions. Recursive source expansion, nested dependency
cycles and invalid selectors fail before commit. Shorthand companion-source
references now discover the same inputs as canonical references.
Cycle checks use selected-resource identity rather than the origin directory or
file alone. Different sections/ranges can compose; repeated selectors still
fail, including filesystem/catalogue aliases and equivalent numeric line ranges.
Opaque owner resources and compiled inclusion variants keep their canonical
identities even when they share a relative-reference origin. Task phases retain
their selected identity, and depth limits still bound finite chains. Capture and
discovery use the same rules. Plain file Values do not add transclusion frames.
Ignored child lifecycle keys are removed before validation or parameter expansion;
composed `each` remains invalid. YAML serialization avoids wrapping escaped
scalars so nested source snapshots retain their exact text and checksums.
Frontmatter and Markdown section selection accept LF and CRLF delimiters using
the same boundary parser. Selection excludes headings inside YAML and rejects
unclosed frontmatter; retained header bytes, source checksums and relative
origins remain intact. Frontmatter must be a mapping, including when its YAML
value is false, zero, an empty list or an empty string.
Owned task-template lifecycle values and artifact `post`/`eval` values must be
strings or lists of strings before compilation or composition. Mappings,
booleans, numbers, null and mixed lists are rejected rather than converted or
silently skipped. Artifact commands retain their order before task commands.

Aggregate successes pin their child run identities. Changed membership or
child runs require a new aggregate attempt. Changed input content is reported
as stale; it does not rewrite historical success. Approval decisions verify
the pinned subject digest before acceptance.

Output gates remain effective after a rejection resolves their gap: dependents
stay blocked while the producer's receipt remains successful. Readiness checks
the latest succeeded output, including for newly added dependents, and requires
an affirmative human decision whose pins still match. Each replacement output
requires a new review. A human can explicitly waive an open or resolved output
gate with `GapDismiss` and a rationale. Missing gate records do not bypass review.

`auto 'Write notes and commit them' --effects '[vcs.commit]'`
records the human's explicit effect request against the root definition. The
grant stops applying if that definition changes. Free-form goal text is not
automatically classified as an authorization: ambiguous language must not
silently grant a consequential effect.

## Engine capabilities

| Engine | Behaviour |
| --- | --- |
| Pi | Default; warm JSONL RPC execution, optional ambient-context isolation, tools and reported token/cost usage. Named agents are rejected. |
| OpenCode V2 | Authenticated `opencode api` transport; durable sessions and forms. Isolation and system-prompt overrides cannot be provided by this transport; temperature overrides are ignored with a warning. |
| LLM CLI | Ephemeral text execution. Named agents and token/cost budgets are rejected because the adapter lacks live usage reporting. |
| fx CLI | `fx ask --json --no-save`; final output and tool summaries, model/provider and system-prompt overrides. No saved sessions or subagents. Named agents, isolation, temperature overrides and token/cost budgets are rejected. Only final main-agent token usage is reported; no cost total. |

Usage limits are checked when the engine reports usage; they cannot guarantee
that a provider stops before an in-flight generation exceeds a limit. Time
limits and cancellation terminate subprocess groups; OpenCode also receives
a server-side interrupt.

Run reassessments use the current plan policy, the original task's effects and
applicable grants, and the caller's shell restrictions. Model checks receive
the same policy payload as lifecycle execution. A covered permission request
is approved; an uncovered one fails the reassessment before the action can run.
A reassessment cannot suspend an already completed run for approval. Its usage
and denied actions append to the run journal without rewriting the receipt.
Reassessment also works after task removal: saved execution state supplies the
checks and captured sources, while current policy is evaluated against the
retained definition. Existing checkpoints remain digest-bound and caller shell
restrictions still apply. Root and removed-task companion references use the
same definition lookup, with missing sources reported as Auto errors.

Shell check commands are collected from authored `!?` directives during
rendering, including selected Liquid branches and composed Markdown. Rendered
input/parameter data, inline file contents, shell output and fenced/raw examples
remain prose even when they contain lines beginning with `!?`. Checks execute
frontmatter commands before the collected body commands; the model receives
the remaining prose and shell evidence. The renderer carries the check
collector through nested composition instead of reparsing expanded strings.

Question fields use the shared descriptors for labels, kinds and validation.
Typed and structured answers are checked before acceptance; multiline, path,
optional and conditional fields retain their semantics. Backend `key` fields
and common `id` fields are both supported, and gap records preserve the form.
Cancelling an interactive run question cancels the waiting engine session.

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
Pi, fx and LLM CLI do not implement this contract. Auto's own shell boundary
separately blocks restricted shell commands; it does not constrain shell
tools launched by an engine.

## Scope and verification

There is no cross-plan scheduler, automatic retry policy or learning capture.
Plan creation/refinement can return a nested Waiting run; resolve its gap with
`gap decide`, then continue the plan. Interactive task and script commands can
ask directly in a terminal.

The tests cover deterministic execution, the shipped Elaborator, durable
questions, revision recovery, approvals, engine wire fixtures, CLI and stdio
MCP. Real MCP stdio covers catalogue listing, policy resolution, empty plan
events and consolidation error responses; the executable OpenCode client bridge
also passes. An opt-in live OpenCode text generation passed. Pi 0.71.1 isolation
flags and its `agent_end` event mapping have regression coverage, but provider
execution is blocked by account/model compatibility. LLM requires an API key
not configured here. A real API-seeded OpenCode check also verifies native form
and permission detachment/checkpoint resumption, including both permission
approval and rejection through a fresh adapter. It does not invoke a provider,
and cleans up its fixture session. Live provider-driven checks also verify
model-generated questions answered/cancelled from another Python process and
model-requested file-read permissions approved/rejected from another process.
They retain the native session, do not repeat Pre, and clear pending native
forms/permissions. A rejected native tool call may end with an assistant
`aborted` error and no idle marker; the adapter now maps that terminal error to
Cancelled rather than waiting until its time budget expires. Other terminal
assistant errors become system failures, while errors preceding a newer user
turn are ignored. `AUTO_LIVE_ENGINES=1` enables these live checks; normal test runs
skip them.
