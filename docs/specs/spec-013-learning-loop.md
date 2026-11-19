# Learning Loop

## Purpose

We want Auto to get better at a repository every time a human uses it, with
no setup and no ceremony. The whole interaction should be one command:

```sh
auto "anything"
```

Auto decides what the text is (a question, a task, a goal, feedback on what it
just delivered, a durable instruction), does it, and learns from how the human
reacts. The loop:

```
REQUEST   auto "TEXT" is routed by what Auto has learned          <--+
 |                                                                   |
 v                                                                   |
DELIVERY  answered, run or planned (see Plan, Orchestration)         |
 |                                                                   |
 v                                                                   |
REVIEW    the human's reaction, asked for only when worth it         |
 |                                                                   |
 v                                                                   |
FEEDBACK  noted as observations of the project agent (Memo)          |
 |                                                                   |
 v                                                                   |
DIGEST    consolidation turns feedback into insights and rules       |
 |                                                                   |
 v                                                                   |
NOTICE    rule changes are applied, shown, and can be undone      ---+
```

This specification composes [Memorisation](spec-010-memorisation.md),
[Elaboration](spec-006-elaboration.md) triage and the
[CLI Prompt](spec-011-cli-prompt.md); it adds no primitive:

- a request is a Goal, a Gap answer or a plan refinement
- feedback is a Memo observation
- an insight and a rule are Memo learnings (roles `insight` and `guidance`)
- a promotion is a Memo node policy change (`core`)
- an undo is a Memo consolidation pass

The division of labour is the one of [Memo Integration](spec-005-memo.md):
Memo stores, classifies and serves memory; Auto decides when to capture,
digest, promote and tell.

Principles:

- *One door*: `auto "TEXT"` is enough; the other commands are shortcuts that
  skip routing, and every routed request echoes the command it stands for.
- *Ask little*: a review is asked for only when its answer is likely to teach
  something; not answering is itself a signal.
- *Forgiveness, not permission*: learning changes are applied, then shown with
  a way to undo them; nothing about memory ever waits for a confirmation.
- *Learn on by default*: a repository learns from its first request.

## Conceptual Model

Project agent: the built-in agent `auto:agent/project`, with Memo agent
`agent-project/agent:Project`. It is the default of `[memory]
default_agents` (see [Memorisation](spec-010-memorisation.md)), so every
plan observes for it unless configured otherwise. Its focus is how to work in
this repository for its humans: conventions, preferences, corrections and
what tends to go wrong. Its built-in configuration:

```toml
[memory.agents.project]
agent = "agent-project/agent:Project"
triggers = ["feedback", "plan-ended", "backlog:12"]
policy = {"memory.promote" = "allow", "memory.evolve" = "allow"}
export = "AGENTS.md"
```

Any field may be overridden in configuration; `default_agents = []` disables
it. On its first use, when its Memo agent does not exist, Auto creates it
(Memo `AgentInit`) and says so once, with how to disable it.

Request: the text given to `auto "TEXT"`.

Route: what a request is, decided by `RequestTriage`:

| Route | The request is | Stands for |
|---|---|---|
| `direct` | A question or a task done in one run | `auto new --route direct "TEXT"` |
| `plan` | A goal that needs a plan | `auto new --route plan "TEXT"` |
| `change` | A change to the current plan | `auto change "TEXT"` |
| `answer` | An answer to the current plan's open question | `auto answer N "TEXT"` |
| `continue` | Carry on with the current plan | `auto go` |
| `feedback` | A reaction to the last delivery | `auto review "TEXT"` |
| `instruction` | A durable instruction (eg. "from now on…") | `auto review --instruction "TEXT"` |

A request can also be noted while routed elsewhere: "no, use pnpm" is a
`change` and a `correction`. The triage returns the route, an optional
`note` (stance and text), and its reason.

Delivery: the last plan summary or run receipt given to the human in this
project, recorded in `.auto/last.json` with its plan, runs and time. Feedback
without an explicit subject is about the delivery.

Feedback: an observation of the delivery's agents (Memo `AgentNote`),
written by the CLI as an interface, with:

- `type`: its stance
  - `approval`: the delivery was right (explicit, or an unanswered review)
  - `correction`: something was wrong, and how
  - `preference`: how the human wants things done, in this case
  - `instruction`: how things must be done from now on (a durable instruction)
- `source`: the delivery (`auto:{plan}` or `auto:{plan}/run/{id}`)
- `subjects`: rules the delivery's runs cited, when the feedback is about them
- `raises`: when given in answer to a review, the review's question item and
  the response (`answered`, `deferred` or `ignored`)
- text: what the human said, as said; it is never paraphrased at capture

An unanswered review is noted as a weak `approval` with `raises` response
`ignored`. Feedback text is record: the privacy rules of Memo apply to what is
derived from it.

Review: the human's chance to react to a delivery. `ReviewAssess` decides
whether to ask, and how:

- `none`: not worth asking (eg. a trivial answer, a routine run with nothing
  new); the summary ends with its usual `Next:`
- `glance`: `Good? [Y]es / [c]hange / [n]ote`
- `question`: one specific question about the delivery (eg. "I chose to
  vendor the parser; was that right?"), answered freely

Insight and rule: learnings of the project agent with role `insight` and
`guidance` (see Memo Learning). A rule loaded at startup is `core`.

Notice: what a consolidation changed that a human should know: rules added,
revised, retired, promoted or demoted, and perspective changes. Notices are
stored in `.auto/agents/{agent}/notices.json` until shown, each with its
pass id, the changed rules (id, title, change) and the undo command.

## Behaviour

1. *Route*: `auto "TEXT"` runs `RequestTriage` with the request, the current
   plan state (status, open gaps, the delivery) and the project agent's wake
   (`minimal` profile). It echoes the command the route stands for
   (`› auto change "…"`), notes the request when it carries a stance, then runs
   it as if that command had been typed, with its usual confirmations.
2. *Deliver*: as the routed command does.
3. *Review*: at the end of a guided plan, or of a direct run, `ReviewAssess`
   runs in one turn with the decision profile, given the summary, the
   decisions made and answers given during it, the route, failures, changed
   files, the rules cited, and the project agent's recent `raises` responses.
   It returns `none`, `glance` or `question` with its reason. Any failure
   answers `none`: review never blocks and never fails a command. In a
   non-interactive session nothing is asked; the summary ends with
   `Feedback: auto review "…"` when the assessment was not `none`.
4. *Note*: the answer is noted as Feedback. `c` asks what should change,
   notes it as a `correction` and applies it as `auto change` does.
5. *Digest*: noting feedback requests a consolidation of the agents
   triggered by `feedback`. It runs as housekeeping, after the command printed
   its result (see Memorisation Triggers).
6. *Notify*: the next human command shows pending notices on stderr, before
   its own output, then clears them:

   ```
   Learned  Use pnpm, never npm, for this repository (rule, from your correction)
            Run the type checker before declaring a task done (rule, core)
   Undo     auto forget            or   auto forget R3
   ```

7. *Act*: rules reach later work through wake (startup prompt for `core`,
   recent layer for the others), the exported `AGENTS.md` section, and
   elaboration, which may turn a checkable rule into a check of the tasks it
   applies to (see Elaboration, Actuation).

## Digest

The project agent's Memoriser instructions (owned by Auto, see Memorisation)
apply Memo's learning rules with these Auto conventions:

- *Stated*: an `instruction` establishes a rule at once, `basis: stated`,
  citing it; `core` when it applies to most of the work in the repository.
- *Corrections*: a `correction` strengthens or qualifies the rule it
  concerns, or becomes an insight. An insight becomes a rule when independent
  corrections, from different deliveries, support it; an agent's own retries
  and echoes are not independent.
- *Preferences*: a `preference` is about the case at hand unless repeated
  across deliveries; it is never generalised to every collaborator from one
  person's statements (see Memo Learning, Privacy).
- *Tested*: a rule whose citing deliveries were approved, or not corrected,
  becomes `basis: tested`; a correction of a cited rule re-opens it.
- *Undo*: an undone rule stays retired; the undo is a `correction` the pass
  must account for, so the rule is not derived again from the same evidence.

These are guidance to the Memoriser, not thresholds Memo enforces.

## Undo

- `auto forget` undoes the last notice: Auto runs Memo `AgentRevert` on its
  pass, after noting a `correction` citing the pass and, when given, the
  human's reason. A rule the pass added is retired, a promotion is demoted, a
  retirement is reactivated; content revisions are restored where Memo can,
  and reported otherwise.
- `auto forget RULE` retires one rule (by its notice number or Memo node id)
  through a single-entry consolidation, with the same `correction`.
- `auto forget --list` lists the recent notices with their passes.

Undo needs no confirmation: it is itself reversible, by stating the rule again.

## Operations

- `RequestDo(context,text,route?)`: routes a request (unless `route` is
  given), notes its stance, and runs the routed operation. CLI: `auto "TEXT"`
  and `auto do "TEXT"`. A single unknown word stays a usage error.
- `RequestTriage(context,text)`: the route, an optional note and the reason,
  in one turn with the decision profile (`auto:catalogue/task/route`). It
  extends `GoalTriage` (see Elaboration), which decides between `direct` and
  `plan` once the route is a goal. Any failure answers `plan` for a goal-like
  text with no current plan, otherwise asks the human to pick a route, or, in
  a non-interactive session, fails as usage with the candidate commands.
- `ReviewAssess(context,delivery)`: whether and how to ask for a review (see
  Behaviour); read-only.
- `FeedbackNote(context,text,stance?,subject?)`: notes feedback for the
  agents of the subject's plan (default: the delivery), triaging the stance
  when not given. CLI: `auto review ["TEXT"] [--instruction|--approve]
  [--plan ID] [--task ID]`; without text, it asks the review question.
- `LearningForget(context,rule?,reason?)`: see Undo. CLI: `auto forget`.
- `LearningStatus` (`auto learned`) gains the recent notices and the current
  rules with their basis and sources.

## Rules

- *Interface capture*: feedback is noted by the interface the human used,
  never inferred later from logs; runs keep observing as in Memorisation.
- *Never block*: routing, review and digest failures fall back (to a
  command, to `none`, to a pending consolidation) and never fail the request.
- *Teach*: a routed request always echoes the command it stands for, so that
  a misroute is visible and correctable ("no, I meant …" is a `correction` of
  the routing, learned like any other).
- *Applied then shown*: memory changes are applied by policy and always
  reach the human as notices; a notice is shown once, and remains in
  `auto learned`.
- *Bounded*: routing and review use the decision profile and count to the
  plan they serve, or to housekeeping when there is none.
- *No commit*: Auto writes `.memo/` and the `AGENTS.md` section; it never
  commits or publishes them.

## Out of Scope

- Learning from other interfaces than the CLI (eg. auto-agent conversations),
  which note feedback by the same model
- Team-wide arbitration of conflicting preferences, beyond Memo's scope rules
- A daemon: digestion runs as housekeeping after commands

## Open Questions

- Whether `ReviewAssess` should also run after `auto change`, to learn from
  refinements that did not need one.
- Whether notices should be grouped per day when several consolidations ran
  between two commands.
