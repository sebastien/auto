# Glossary

Terms below describe Auto's implementation; links point to their principal
definitions.

- **Script** — Template source, metadata and origin, rendered for execution.
  A task exposes each lifecycle section as a script. Scripts alone are
  ephemeral. ([model](../src/py/auto/features/script/model.py))
- **Task** — A plan-owned script definition with metadata and separate
  lifecycle sections: Pre, Process, Post and Evaluation. Tasks can have
  parents, children and dependencies. ([model](../src/py/auto/features/task/model.py))
- **Plan** — A plan directory containing root-task metadata, description and
  sections, together with its tasks and history. Its goal defaults to the
  description. ([model](../src/py/auto/features/plan/model.py))
- **Goal** — The intended outcome stated for a plan or task, not its execution
  result. A task without an explicit goal defaults to its title and description.
- **Run** — A stored execution of a task, with status, inputs, usage, checks
  and potentially a question or gap. A task can have multiple runs; a terminal
  receipt is immutable. ([model](../src/py/auto/features/task/model.py))
- **Revision** — An append-only plan-journal entry recording authored
  operations and their reason. ([model](../src/py/auto/features/plan/model.py))
- **Gap** — Something a task or plan needs and does not yet have, such as an
  answer or approval. A **decision** records an answer and rationale; it may
  resolve a gap or be recorded independently. ([model](../src/py/auto/features/plan/model.py))
- **Question** — A request for an answer during execution, potentially with
  structured fields. A task run can wait on a gap representing that need;
  recording its decision and resuming the run are separate operations.
- **Elaboration** — A proposed breakdown of a task into children, removals
  and gaps. An **Elaborator** produces it; application can publish a plan
  revision. ([model](../src/py/auto/features/elaboration/model.py))
- **Materialisation** — Instantiating an elaborated task, optionally from a
  template: parameter expansion and source capture happen during application,
  while runtime inputs remain available at execution.
  ([implementation](../src/py/auto/features/elaboration/materialize.py))
- **Catalogue** — Layered framework assets addressed by kind and name, including
  task templates and artifacts. Project entries override user entries, which
  override built-ins. ([implementation](../src/py/auto/features/elaboration/catalogue.py))
- **Reference** — An address resolved to content, potentially selecting part of
  a resource. Task definition references identify assets; task output
  references can also establish dependencies.
- **Context** — Caller configuration, inputs, policy and runtime facilities
  passed to operations. Its runtime gateway resolves references and reaches
  integrations. ([model](../src/py/auto/runtime/model.py))
- **Runtime** — The gateway on a context through which feature operations reach
  engines, knowledge, reference resolvers and other integration boundaries;
  it is not itself an engine. ([model](../src/py/auto/runtime/model.py))
- **Engine** — An integration that executes model requests and reports events
  and available usage. It is distinct from a task or run.
- **Policy** — Layered rules and grants governing effects; an effect can be
  allowed, require confirmation or be denied. Enforcement depends on the
  boundary and engine capabilities. ([implementation](../src/py/auto/features/plan/policy.py))
- **Memory** — Agent-associated material used as context and observations for
  future work. **Memo** is the external knowledge and memory system accessed
  through Auto's integration; Auto does not own its storage contract.
- **Wake** — A bounded snapshot of selected agents' memory layers (prompt,
  world and recent) allocated for a run. Saved layers are reused on resume.
  ([implementation](../src/py/auto/features/memory/operations.py))
- **Consolidation** — An agent-scoped task lifecycle that proposes, validates
  and applies memory changes through Memo. It is distinct from wake, which
  supplies context for execution.
- **Housekeeping** — Invocation-triggered maintenance that repairs and queues
  work and processes at most one pending consolidation pass per agent; it is
  not a daemon. ([implementation](../src/py/auto/features/memory/scheduler.py))
- **Budget** and **usage** — Limits on resources such as tokens, cost and
  time, and the measured amounts consumed against them, respectively.
- **Operator** — A named operation in the Python API. Operators use PascalCase,
  typically a subject followed by an action, such as `TaskStart`, `GapDecide`
  or `PlanContinue`; CLI commands use their own command spelling.

In short, a plan contains tasks; a task supplies scripts and can have runs.
Elaboration proposes changes to the plan, revisions record changes, and gaps
and decisions capture missing information and its resolution.
