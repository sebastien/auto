# Memorisation

## Purpose

We want to close the loop of the [Goal](spec-000-goal.md): what happens while
plans run and humans interact should become knowledge that improves the next
elaboration. This specification implements the Learning layer, by running the
memorisation process defined by Memo (see Memo Learning and Memorisation) for
agents that have a persona and a perspective. It needs to support:

- *Agents*: several agents, each with its own focus, observing plans and
  conversations through many instances
- *Capture*: runs and interfaces noting observations as they work
- *Consolidation*: running memorisation as ordinary tasks, with a budget, a
  policy and human oversight
- *Questions*: what memorisation cannot derive is logged for the agent to
  raise with a human when it sees fit, never blocking
- *Actuation*: learnings and world views feeding elaboration
- *Progression*: start with one built-in Memoriser, then specialise

Memorisation is the counterpart of [Elaboration](spec-006-elaboration.md):
elaboration refines a goal into tasks; memorisation sums up what happened into
learnings and a world view. Both are ordinary tasks implementing a well-known
interface, whose structured output Auto validates and applies.

## Conceptual Model

Agent: defined by Auto under a stable, project-local identifier, referenced as
`auto:agent/{id}`. Auto owns its configuration and transient state in `.auto/`;
Memo owns its persona, perspective, observations, learnings and views as
knowledge artifacts (its AgentMemory). The configured `agent` field locates
that AgentMemory entry, by default `agent-{id}/agent:{Name}`, `{Name}` being
the id in PascalCase (`ui-designer` gives `UiDesigner`), so the Auto id and
the Memo private namespace match (eg. `designer` and `agent-designer/`).
The project's `[memory] default_agents` lists the default agent references of
plans created without `agents`; it defaults to an empty list. Agent definitions
remain under `[memory.agents.{id}]`. A plan's explicit `agents` overrides the
default selection, and `agents: []` disables it. Creation records the resolved
selection, so later configuration changes do not change existing plans.
`agent` is a reserved plan id.

Agent (runtime facet): how Auto runs memorisation for an agent, from
configuration.
- `agent`: reference to the Memo agent entry; two Auto agents may not point to
  the same Memo agent in a project
- `memoriser`: the Memoriser task template, defaulting to the built-in one
- `profile`: allocation profile of consolidation runs (see Elaboration)
- `budget`: budget per consolidation run
- `allowance`: optional agent-specific cap per time window, in addition to the
  project housekeeping allowance
- `policy`: rules for the `memory.*` effects (see Policy)
- `triggers`: when consolidation is requested (see Triggers), `[]` by default
- `concerns`: optional decision concerns the agent follows (see Decisions);
  all decisions of its plans when absent
- `instructions`: what this agent's memory is for, given to its Memoriser

Plan (memorisation facet): attributes added to a plan.
- `agents`: references to the agents informed by and observing the plan

Agent scope: where an agent's consolidation runs are stored, as a plan is for
the runs of its tasks, at `.auto/agents/{agent}/`.

```text
.auto/agents/{agent}/
  .lock               # held by the running consolidation
  pending.json        # at most one coalesced request
  cursors.json        # per plan, the last decision event consumed
  runs/{run}/         # Output, journal and receipt (see Task)
```

A consolidation run is referenced as `auto:agent/{agent}/run/{id}`. The
agent scope has no goal, tasks, gaps or journal: the history of consolidations
is the agent's own log, where each report is an `outcome` observation.

Instance: a run, or an interface conversation, acting for an agent. Its
canonical reference (eg. `auto:{plan}/run/{id}`) is its Memo `instance`.

Memoriser: an ordinary task implementing the Memoriser interface, whose output
is a MemoryConsolidation.

Memoriser interface: the well-known inputs of a Memoriser, each a Value (see
[Script](spec-002-script.md)).

| Input | Purpose | Supplied by |
|---|---|---|
| `agent` | The agent entry: persona, perspective, stores | Auto |
| `due` | The agent's due work (Memo `AgentDue`) | Auto |
| `record` | References to the record cited by the due observations: run journals, plan events, decisions, transcripts | Auto |
| `decisions` | References to the decisions new to the agent (see Decisions) | Auto |
| `accepted` | Proposals accepted by a human and not yet taken up, with their acceptance (see Policy) | Auto |
| `phases` | Phases requested, defaulting to all | Auto |
| `authorization` | Whether promotion and perspective changes may be applied or must be proposed | Auto, from policy |
| `budget` | Budget of the run | Auto |
| `instructions` | What this agent's memory is for | Configuration |

The Memoriser reads the agent's material through Memo (learnings, model,
sources) and the record through Auto read operations; it does not write to
Memo itself.

MemoryConsolidation: the output of a Memoriser run, a Memo change set (see
Memo Memorisation) with its report.
- `id`, `agent`, `snapshot`, `changes`, `report`: as in the Memo change set
  (summary, deferred, acknowledged, questions, proposals); the Memo report
  defines each field

## Capture

Observations come from two sources:

- *Runs*: tasks of a plan with `agents` receive, for each agent, its wake at
  the task's memory profile (see Actuation) and the capture instructions, with
  the run as `instance`. Observations are appended to the agent's AgentMemory
  (Memo `AgentObserve`), with the run, or one of its journal events, as
  `source`. Runs judge
  what is notable; nothing restricts them to a type of event.
- *Interfaces*: an interface acting for an agent (eg. an auto-agent
  conversation) notes observations with its conversation as instance and
  conversation references as sources; capture rules are those of the
  interface.

Subagents spawned by a run do not observe: they cannot judge what is already
known, and would duplicate the run's observations. Auto records no observation
itself; in particular, decisions are not observations (see Decisions).

## Decisions

Decisions are recorded once, in their plan, as Memo entries (see
[Plan](spec-004-plan.md) and [Memo](spec-005-memo.md)), independently of any
agent. They are typically design and architecture choices affecting the whole
project, so they are project knowledge, not one agent's memory; `concerns`
labels say what they affect.

- *Input*: a consolidation receives, as `decisions`, the decisions resolved
  or recorded since the agent's previous consolidation in plans listing the
  agent, restricted to its `concerns` when configured; a decision without
  `concerns` reaches every agent of its plan. Auto finds them through
  `GapResolved` and `DecisionRecorded` events from the cursor in
  `cursors.json`, and advances the
  cursor only when the consolidation is applied.
- *Citation*: learnings and model entries cite decisions directly as sources
  (eg. `memo:auto/decision:checkout_guest+20261110T…`); decisions are record,
  never copied into the agent's log. An agent notes an observation about a
  decision only when something about it is notable from its perspective (eg.
  it contradicts a learning).
- *Relevance*: a decision no agent cites is not lost: it stays in its plan,
  readable by any agent, elaboration or human. Uncited decisions are not
  routing backlog.

## Triggers

A trigger requests a consolidation of an agent; Orchestration decides when it
runs. Requests are coalesced: at most one consolidation runs and one is
pending per agent. Requests and incomplete applications persist under `.auto/`;
Auto commands check them on invocation. An idle project may leave work pending
until the next invocation. A triggered request exceeding the housekeeping or
agent allowance is deferred until the window allows it; an explicit request
reports the allowance and runs only with an explicit override. The per-run
budget still applies.

Housekeeping is accounted separately from plan budgets, even when a plan
triggers it. The project `.auto/` stores usage for the configured window;
failed and interrupted runs count their known usage. The project-level
`[housekeeping]` allowance covers internal processes, with memorisation its
first consumer. An agent-specific allowance, if configured, further restricts
that agent. Neither allowance may loosen a user-level restriction.

| Trigger | Requests a consolidation |
|---|---|
| `plan-ended` | When a plan with the agent in `agents` ends |
| `backlog:{n}` | When `AgentDue` reports at least `n` unrouted observations, checked after `RunEnded` |
| `decision` | When a decision reaching the agent is resolved or recorded, including on an ended plan |
| (explicit) | By `ConsolidationStart` |

Idle-time consolidation, and consolidation after a conversation answered a
question or settled a proposal, are requested by long-running interfaces
through `ConsolidationStart`, Auto having no daemon. Interfaces learn that a
consolidation changed an agent's memory by comparing its wake layer revisions
(see Memo Learning), not through events. No plan ever waits for a
consolidation: elaboration and refinement use the material as it is.

## Application

A consolidation is a run of the Memoriser task in the agent scope, holding
the agent lock:

1. Auto compacts this working copy's pending observations (Memo
   `LogCompact`, storage only), then computes the inputs: `AgentDue`, the
   record references, and the `authorization` from the effective policy (see
   Policy).
2. The Memoriser runs and outputs a MemoryConsolidation, validated against its
   schema and with Memo `AgentConsolidate` in dry mode as Post. A violation
   fails the run as `output`, with the diagnostics as feedback for a retry.
3. Auto stages the intended change set in `.auto/` before applying it with
   `AgentConsolidate`, then persists Memo's completed/pending result and the
   run receipt. A stale revision before application fails the run as `output`
   and is retried from fresh inputs. After a partial application or crash,
   Auto reconciles the staged change set with Memo before any retry; already
   applied changes and report observations must not be duplicated: appends are
   keyed from the change set's `id` (see Memo Memorisation), so a retry finds
   them. Memo retains the frozen plan under the agent identity and pass id
   before publishing units. An exact retry reconciles completed units and
   keyed appends, and publishes the completion report last. Auto retains the
   staged intent and Memo's completed, pending and uncertain unit results until
   reconciliation completes.
4. Questions and proposals are logged by `AgentConsolidate` as open
   observations of the agent. The agent decides whether and when to raise
   them, typically in a conversation through an interface. The outcome (an
   answer, an accepted or rejected proposal) is noted as an observation
   resolving them, and a later consolidation takes it up, re-deriving an
   accepted proposal against the current material rather than replaying it.
   A proposal is accepted by an observation that `resolves` it, written by
   anyone but a consolidation pass (eg. an interface acting on a human's
   confirmation); it is rejected by one that `dismisses` it.

The run receipt records the report. Consolidation never raises gaps and never
blocks a plan: it is cheap, and its
results are reworked by later passes rather than approved in advance.

## Policy

Memorisation declares effects (see [Plan Policy](spec-008-plan-policy.md)):

| Effect | Consequence | Recommended rule |
|---|---|---|
| `memory.observe` | Appending observations | `allow` |
| `memory.consolidate` | Applying corrections, summaries, learnings and model changes | `allow` |
| `memory.promote` | Changing startup selection (`core`, bindings) | `confirm` |
| `memory.evolve` | Changing a perspective | `confirm` |

Rules come from user and project configuration and the agent's `policy` (see
Plan Policy sources); a later layer may tighten but never loosen an earlier
restriction. Plan rules do not apply to consolidation, which belongs to no
plan. An effect without a rule uses its recommended rule above.

`authorization` is derived from these rules: `allow`
lets the Memoriser include the change in `changes`; `confirm` makes it a
logged proposal, not a checkpoint; `deny` makes it neither. Memorisation
never needs, and is never granted, `vcs`, `net.write` or `data.delete`.

Accepted proposals are the human confirmation that `confirm` asks for: the
next consolidation receives them (`accepted`), and its `authorization` allows
the `memory.promote` and `memory.evolve` effects that its rules leave to
`confirm`, never those denied. Once that pass is applied, its accepted
proposals are taken up, recorded in the agent scope (`accepted.json`), and
grant nothing further; a pass that fails or is not applied leaves them for the
next one.

## Actuation

When a plan has `agents`:

- *Memory profiles*: a task's allocation profile names its `memory` profile
  (see Elaboration), `standard` by default; Auto wakes each of the plan's
  agents with it, the task goal as query. Elaborators use `full`. How much
  memory an instance loads is thus decided per task, with its model and
  budget.
- *Several agents*: an instance acting for several agents receives one agent
  prompt per agent, and shares the profile's world and recent budgets between
  them (each gets at least its layer minimums), rather than one full budget
  per agent.
- *Order*: wake layers are placed from the most stable to the most volatile
  (agent prompts, world, recent, then the task), so that the stable prefix is
  reused from the engine's prompt cache when it supports one.
- *Usage*: a run receipt records, per agent and layer, the bytes loaded and,
  when the engine reports it, the cached share, so profiles can be tuned
  against run outcomes.
- *Elaboration*: when the Elaborator does not supply `learnings`, Auto supplies
  each agent's wake for the task goal, in addition to the plan learnings. `context` gains the agents'
  views of the shared world (Memo `AgentModel`), with other agents' views of
  the same entities, as context, never as instructions.
- *Citations*: learnings applied by an elaboration are cited in the
  TaskElaboration (see Elaboration). Runs of tasks citing a learning are part
  of the record, so that a later Derive phase can mark a learning `tested`
  from their outcome, or re-open it.
- *Interfaces*: give the agent its open questions and proposals as context
  (see Questions).

## Questions

How an agent raises its open questions is its judgement, not a rule. Auto and
interfaces provide what it needs to judge:

- *Visibility*: the recent layer of the agent's wake includes its open
  questions, the most relevant to the current goal or conversation first
  (see Memo Learning).
- *Purpose*: the capture instructions state the principle: ask when the work
  or conversation touches the subject, or at a natural pause (a finished task,
  an idle moment); never interrupt work a human asked for; ask little at a
  time.
- *Feedback*: how a human responds to a question (answers, defers, ignores)
  is noted as an observation, so the agent learns how and when each human
  prefers to be asked, like any other learning.
- *Expiry*: questions left unanswered after several opportunities are
  rephrased, settled tentatively or dropped by a later consolidation (see Memo
  Memorisation).

The conversational behaviour itself belongs to the interfaces (eg. the
auto-agent Assistant).

## Progression

Memorisers are expected to grow without changing the interface or the
MemoryConsolidation format:
1. *Built-in*: one Memoriser running all phases in one run, with a generic
   prompt parameterised by the perspective and instructions.
2. *Phased*: an aggregate Memoriser whose children run phases separately, with
   cheaper profiles for Verify and Compress and stronger ones for Derive and
   Model.
3. *Specialised*: perspective-specific Memorisers in the catalogue (eg. for
   user experience, for backend architecture).
4. *Reflective*: memorisation of the Memoriser itself, its failures and
   questions informing changes to its instructions, as proposals.

## Operations

- `ConsolidationStart(context,agent,phases?,reason?,overrideAllowance?)`:
  requests a consolidation of an agent, returning its run, or the pending one
  when coalesced. An allowance overrun requires an explicit override.
- `ConsolidationStatus(context,agent)`: the agent's due work, the latest and
  pending consolidations, open questions and pending proposals.

Plan `agents` are set at creation (`PlanCreate`, see Plan) and changed with an
`agents` revision operation, extending the PlanRevision operations.

## Format

```toml
[memory]
default_agents = ["auto:agent/designer"]

[memory.agents.designer]
agent = "agent-designer/agent:Designer"
memoriser = "auto:catalogue/task/memorise"
profile = "deep"
budget = {tokens = 200000}
triggers = ["plan-ended", "backlog:24"]
concerns = ["experience", "design"]
policy = {"memory.promote" = "allow", "memory.evolve" = "confirm"}
instructions = """
Remember what helps design for the people using the product: who they are,
what they try to do, where they struggle, and what was decided about it.
"""

[housekeeping]
allowance = {tokens = 1000000, per = "day"}
```

```markdown
---
id: checkout-redesign
goal: Simplify the checkout flow
agents: ["auto:agent/designer", "auto:agent/architect"]
---
```

## Rules

- *Ownership*: Memo owns learning material and its rules; Auto owns when and
  how memorisation runs. Auto writes learning material only through Memo
  operations, and only for the agent being consolidated.
- *One consolidator*: consolidations of an agent never run concurrently, the
  agent lock excluding them across processes; observing never waits for them.
- *Grounding*: the record given to a Memoriser is referenced, not copied, and
  is limited to what due observations cite, plus what the Memoriser reads
  explicitly within its budget.
- *Privacy*: conversation transcripts are record, never learning material;
  interfaces pass references, resolved during a Memoriser run (eg. auto-agent
  conversation excerpts, see Resolvers), and Memo privacy rules apply to what
  is derived.
  Consolidation run journals may hold what the Memoriser read, so
  `.auto/agents/` is never version-controlled.
- *Determinism*: inputs are recorded in the run, so a consolidation can be
  re-examined against the same due work.
- *Failure*: a failed consolidation leaves the material as it was, apart from
  completed changes reported in its receipt; capture is unaffected.
- *Resolvers*: an interface may provide the resolver of its reference scheme
  (eg. `auto-agent:`), declared by the project owner in configuration
  (`[resolvers] "auto-agent" = "auto-agent excerpt"`); Auto calls the command
  with the reference and `--budget BYTES`, and reads a JSON excerpt from its
  output, exit status 3 meaning unavailable. When the
  resolver is unavailable (eg. the interface is not installed on this host),
  the reference is reported unavailable to the Memoriser, never guessed.
  Commands are split into an executable and arguments, without a shell; relative
  executables resolve from the project directory. The JSON excerpt is an object;
  if it includes `source`, that value must match the requested reference. Invalid
  JSON, an oversized response or another nonzero status is a resolver error.
  A missing executable and exit status 3 report the reference as unavailable.
  Configured resolvers cannot replace Auto's built-in schemes.
- *Availability*: Auto must not claim to have applied or scheduled a runnable
  consolidation until Memo provides the required agent operations and partial
  application reconciliation semantics.

## Out of Scope

- Learning material formats and rules (see Memo Learning)
- Capture from specific interfaces (see their specifications, eg. auto-agent
  Memory)
- Agents acting as task performers (personas allocated to tasks), beyond
  observing and informing elaboration
- Cross-agent consolidation; perspectives belong to one agent

## Open Questions

- Whether elaboration should receive past decisions of other plans by
  `concerns`, in addition to what agents' models cite.

## References

- Memo specifications: Log, Learning, Memorisation.
- [Components of agent memory](https://www.anthropic.com/research/components-of-agent-memory).
