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
- *Composable layers*: each layer adds elements, and extends the *Run* defined
  by Execution.
- *No new primitives*: higher-level ideas are expressed with existing elements
  (a meta-task is a Task, actuation is Planning informed by Learnings,
  learning management is a set of Tasks in a Workflow).
- *Orthogonal storage*: the storage format and policy of learnings (and memory
  in general) is independent of the layers, defaulting to Memo.

## Layers

| Layer | Adds | Built from | Extends Run with |
|---|---|---|---|
| Execution | Script, Run | Template + inputs | State, journal of events, receipt |
| Planning | Task, Plan | Script + contract; Tasks + Learnings | Output assessment, learnings |
| Orchestration | Workflow | Rules acting on a Plan | Budget status, retry count |
| Learning | Learning | Assessed Runs | — |

- *Execution*: defines agent work as templated instructions, run as a script
  that outputs a result or produces effects.
- *Planning*: composes a script and its contract (inputs, outputs,
  prerequisites, postconditions) into a task, and tasks into a plan. Planning
  uses learnings to refine or decompose tasks (meta-tasks) into sub-tasks.
- *Orchestration*: sequences and refines tasks, and decides on the overall
  progression of the plan based on time and budget constraints, policy and
  strategy.
- *Learning*: closes the loop, as learnings are derived from Runs and feed back
  into Planning. Managing learnings is itself defined as Tasks within
  a Workflow (eg. capture failures and surprises, register learnings,
  periodically consolidate them into a defined knowledge structure).

## Elements

- *Script*: an agent-runnable template supporting inputs and parameters.
- *Run*: the execution of a script, carrying a state, a journal of events, and
  a receipt of completion or failure. Extended by each layer above Execution.
- *Task*: a script decorated with a contract: dependencies and quality
  assessment of the output (definition of done).
- *Plan*: a set of tasks, refined as they are executed and as learnings accrue.
- *Workflow*: rules that drive the creation and updating of a plan.
- *Learning*: knowledge derived from an assessed Run, classified (failure,
  surprise, discovery) and stored durably for retrieval.

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
