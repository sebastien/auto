# Goal

We want to create an agent orchestration tool, built on composable layers of
execution. The end goal is to support building fully autonomous, self-improving
systems with human control and oversight. Task automation is central, but just
as important is knowledge retention and learning, which feeds back into
the core process.

```
GOAL      An initial goal leads to the creation of an initial plan
 |
 v
PLAN      A plan is made of tasks and updated as tasks progress   <--+
 |                                                                   |
 v                                                                   |
TASK      A task is executed and produces outputs                    |
 |                                                                   |
 v                                                                   |
OUTPUT    Outputs are assessed against the task                      |
 |                                                                   |
 v                                                                   |
LEARNING  Assessed outputs (failures, surprises, successes)          |
          build knowledge                                            |
 |                                                                   |
 v                                                                   |
ACTUATION Learnings refine the plan and the elaboration of tasks  ---+
```

## Principles

- *Simple model*: a small set of elements, each built from existing ones.
- *Composable layers*: each layer adds elements; Planning introduces the *Run*,
  extended by the layers above.
- *Facets, not subtypes*: a layer extends an existing element with attributes
  (a facet) rather than introducing a specialised type (eg. Task gains a plan
  facet, Run is extended by Orchestration).
- *Zero setup*: running a script needs no storage or configuration;
  persistence starts with tasks.
- *No new primitives*: higher-level ideas are expressed with existing elements
  (a meta-task is a Task, actuation is Planning informed by Learnings,
  learning management is a set of Tasks in a Workflow).
- *Orthogonal storage*: the storage format and policy of learnings (and memory
  in general) is independent of the layers, defaulting to Memo.

## Layers

| Layer | Adds | Built from | Extends Run with |
|---|---|---|---|
| Execution | Script | Template + inputs | — (ephemeral: events and output) |
| Planning | Task, Run, Plan, Gap, Decision | Script + contract; Tasks + Learnings | State, journal, receipt, output assessment |
| Orchestration | Workflow | Rules acting on a Plan | Budget status, retry count |
| Learning | Learning | Assessed Runs, Decisions | — |

- *Execution*: defines agent work as templated instructions, run as a script
  that outputs a result or produces effects.
- *Planning*: composes a script and its contract (inputs, outputs,
  prerequisites, postconditions) into a task, and tasks into a plan. Planning
  uses learnings to refine or decompose tasks (meta-tasks) into sub-tasks.
- *Orchestration*: sequences and refines tasks, and decides on the overall
  progression of the plan based on time and budget constraints, policy and
  strategy.
- *Learning*: closes the loop, as learnings are derived from Runs and Decisions, and feed back
  into Planning. Managing learnings is itself defined as Tasks within
  a Workflow (eg. capture failures and surprises, register learnings,
  periodically consolidate them into a defined knowledge structure).

## Elements

- *Script*: an agent-runnable template supporting inputs and parameters, run
  standalone and without storage.
- *Task*: a script decorated with a contract: dependencies and quality
  assessment of the output (definition of done).
- *Run*: the stored execution of a task, carrying a state, a journal of events,
  and a receipt of completion or failure. Extended by Orchestration.
- *Plan*: the scope of a set of tasks working towards a goal, elaborated from
  the goal and refined as tasks are executed and as learnings accrue.
- *Gap*: something a task or plan needs and does not have (missing input,
  question, decision to be made), tracked until resolved.
- *Decision*: the resolution of a gap, with its rationale and author.
- *Workflow*: rules that drive the creation and updating of a plan.
- *Learning*: knowledge derived from assessed Runs and Decisions, classified
  (failure, surprise, discovery) and stored durably for retrieval.

## References

- [mdflow](https://github.com/johnlindquist/mdflow) defines a good executable
  markdown format, but drifted off in a vibecoded fever and added many features
  that dilute the purpose.
- [agenttasks.dev](https://agenttasks.dev/) defines a format for managing
  tasks given to agents. Its [task file format](https://agenttasks.dev/docs/spec/task-file)
  works well with something like mdflow.
- [GitHub Spec Kit](https://github.github.com/spec-kit/index.html) defines
  a complete SDLC with a baseline workflow that can be configured, although
  not changed entirely.
- [OpenSpec](https://openspec.dev/) defines a methodology to expand a prompt
  into a `proposal -> specs -> design -> tasks` loop.
- [Memo](https://github.com/sebastien/memo) defines a classification and conventions
  to organise and query knowledge.
