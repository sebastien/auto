# Goal

We want to create an agent orchestration tool, built on composable layers of
execution. The end goal is to support building fully autonomous, self improving
systems with human control and oversight. Task automation is central, but just
as importantly is knowledge retention and learning, that feeds back into
a core process.

```
PROCESS -> TASK -> OUTPUT -> EVALUATION -> LEARNINGS -> ACTUATION
```

We envision the following layers:

- *Agent Script* as a way to template agent work, giving scripted instructions
  and outputting a result.

- *Auto Task* as a way to compose one script and its contract into a task that is
  part of a plan with inputs, output, prerequisites and postconditions that assess
  quality of execution (definition of done, quality control).

- *Agent Workflow* as definition of how to run a task or a collection of tasks,
  in that sense a workflow is a meta-task with an emphasis on logic and
  conditional behaviour. While a task is for doing, a workflow is for coordinating.
  The baseline [Meta-task](spec-007-meta-task.md) uses an ordinary Auto Task to
  coordinate work, with durable Memo checkpoints and explicit questions.

- *Agent Memory* as way to keep track of things done (a journal), along with
  learnings (based on failures, surprises and discoveries)

Supported by the following elements:

- *Artifact* as a definition of an output, covering its format or structure, and quality
  assessment criteria.
- *Role* as a subset of an `AGENT.md` file delimiting the focus, without the
  personality.
- *Personality*, as a way to add color and character to the execution of the task.
- *Skill*, *Tool* as a classic agent skills and tools

## References

- [mdflow](https://github.com/johnlindquist/mdflow) defines a good executable
  markdown format, but drifted off in a vibecoded fever and added many features
  the dilute the purpose.
- [agenttasks.dev](https://agenttasks.dev/) defines a format for managing
  tasks given to agents. Its [task file format](https://agenttasks.dev/docs/spec/task-file)
  works well with something like mdflow.
- [GitHub Spec Kit](https://github.github.com/spec-kit/index.html) defines
  a complete SDLC with a baseline workflow that can be configured, although
  not changed entirely.
- [OpenSpec](https://openspec.dev/) defines a methodology to expand a prompt
  into a `proposal -> specs -> design -> tasks` loop.
