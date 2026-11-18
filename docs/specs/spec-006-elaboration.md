# Elaboration

## Purpose

We want to define how the goal of a task is broken down into tasks.
Elaboration is where Auto becomes active: it turns intent into a plan, and
keeps refining it as tasks run and learnings accrue. Elaboration may involve
significant work (reading specs, source code, prior outputs), done by the
agent harness with its configured agents and skills. It needs to support:

- *Decomposition*: break the goal of a task into sub-tasks, recursively,
  starting from the plan goal
- *Context*: inputs, specs and sources, given as content or as references
- *Budget*: fit the tasks within the available budget
- *Framework*: follow an established methodology, and reuse task templates,
  artifact types, profiles and checks
- *Learnings*: nudge the elaboration with knowledge from past runs and
  decisions
- *Progression*: start with a standalone Elaborator, then extract parts into
  shared assets and manage variants with parameters

## Conceptual Model

TaskGoal: what a task must achieve, and what is elaborated.
- `goal`: the task goal, defaulting to its title and description; for the
  root task, the plan goal (see [Plan](spec-004-plan.md))
- `output`: the output contract (`output.*`, `artifact`)
- `post`, `evaluation`: the task checks

Elaborating the root task creates or refines the plan; elaborating any other
task decomposes it (see Decomposition).

Elaborator: an ordinary task (see [Task](spec-003-task.md)) implementing the
Elaborator interface, whose output is a TaskElaboration.

Elaborator interface: the well-known inputs of an Elaborator. Each is a Value
(see [Script](spec-002-script.md)): literal content, a file, a reference or
a shell command.

| Input | Purpose | Supplied by |
|---|---|---|
| `goal` | TaskGoal of the task to elaborate: its goal, then its definition of done | Auto |
| `state` | Plan state: task definitions and states, run failures, open gaps, usage and the reason; empty for a plan's first elaboration | Auto |
| `budget` | Budget available to the elaborated tasks | Auto |
| `policy` | Rules differing from the default, and grants by scope (see [Plan Policy](spec-008-plan-policy.md)) | Auto |
| `context` | Specs, sources, notes | The Elaborator, or plan inputs |
| `methodology` | Methodology to follow, if any | The Elaborator |
| `catalogue` | Task templates, artifact types, profiles and checks to use | The Elaborator |
| `learnings` | Learnings to take into account | The Elaborator, or the plan learnings |

Inputs other than those supplied by Auto are optional, and an Elaborator may
declare any other inputs and parameters.

Auto renders its inputs as text for a model to read: block YAML, with
multiline text as literal blocks, stating each fact once. The target's goal is
only in `goal`, the policy only in `policy`, and the `state` leaves out what a
reader can derive: paths, empty fields, goals equal to their task's title and
description, children (from `parent`), and outputs (a succeeded task's output
is `task:{id}`).

TaskElaboration: the output of an Elaborator run.
- `task`: id of the elaborated task
- `summary`, `rationale`: text
- `tasks`: task definitions, the children of the elaborated task
- `removals`: ids of children to remove, with reasons
- `gaps`: gap definitions (see Plan), without their generated fields (`id`,
  `status`, `revision`), named locally so tasks and other gaps can refer to
  them within the elaboration
- `context`: references the elaboration relied on (see Provenance); a bare
  relative path is read as a project `PATH`, and an entry that is not
  a reference (prose) is ignored
- `learnings`: references to the learnings applied

Elaborated tasks are task definitions, using the task keys introduced by this
specification:
- `from`: task template it instantiates, with `params`
- `artifact`: artifact type of its output
- `profile`: allocation profile, overridden by explicit `engine`, `model`,
  `agent` or `temperature` (`agent` is the engine's harness agent, eg. an
  OpenCode agent; memorisation agents are the plan's `agents`, see
  [Memorisation](spec-010-memorisation.md))
- `phase`: methodology phase it belongs to
- `hints`: advisory flags for Orchestration (see Hints)
- `replaces`: id of the task it reworks
- `learnings`: references to the learnings applied to this task

### Aspects

An elaborated task only needs an id, a title and a process (or `from`).
A refined elaboration fills in more aspects, and missing aspects fall back to
defaults:

| Aspect | Keys | Default |
|---|---|---|
| Core | `id`, `title`, `goal`, `inputs`, Process | — |
| Contract | `artifact`, `output.*`, Post | No output constraint, no check |
| Allocation | `profile`, overrides, `budget` | Plan default profile, even budget split |
| Governance | Evaluation, `hints.gate`, gaps | No evaluation, no gate |
| Structure | `phase`, `hints.refine`, `hints.elaborate` | — |

The root task's `profile` is the plan default allocation profile. An elaborated
task inherits it only when neither the task nor its template selects a profile;
relative references keep the directory of the definition that supplied them.

### Hints

Hints are advisory: they inform Orchestration, which decides how to act on
them.

| Hint | Value | Meaning |
|---|---|---|
| `refine` | `after` | A checkpoint: refine the plan once the task succeeds |
| `elaborate` | `later` | Decompose the task when it becomes Ready, instead of running it |
| `gate` | `approval` | Raise an approval gap on the output before dependent tasks run |

## Framework

The framework is a set of conventions over markdown files with frontmatter.
These files are ordinary assets, transcluded or referenced by `PATH`, and
optionally shared through the catalogue (see Catalogue).

| Kind | Content | Used by |
|---|---|---|
| `methodology` | Phases, with their artifacts, templates and gates | Elaborators, conformance check |
| `artifact` | Output contract (`output.*`) with Post and Evaluation sections | Tasks, with `artifact` |
| `task` | Task template: a task with parameters | Tasks, with `from` |
| `profile` | Engine, model, agent, generation settings, default budget and memory profile | Tasks, with `profile` |
| `check` | A script with a TaskVerdict or TaskEvaluation output | Post and Evaluation sections |

### Methodology

```markdown
---
kind: methodology
identity: auto:catalogue/methodology/sdlc
title: Spec-driven SDLC
phases:
  - id: spec
    artifacts: [auto:catalogue/artifact/spec]
    gate: approval
  - id: design
    artifacts: [auto:catalogue/artifact/design]
  - id: implement
    templates: [auto:catalogue/task/implement]
  - id: verify
    templates: [auto:catalogue/task/review]
---
# Spec-driven SDLC

Guidance for the Elaborator, per phase.
```

- Phases are ordered; `artifacts` must be produced by tasks of the phase,
  `templates` are the task templates the phase draws from, and `gate` raises
  an approval gap on the outputs of the phase.
- The body is guidance given to the Elaborator.

### Artifact Type

```markdown
---
kind: artifact
identity: auto:catalogue/artifact/spec
title: Specification
output:
  format: markdown
  template: ./spec.template.md
---
# Specification

## Post

Instructions checking the output is a specification.

## Evaluation

Criteria assessing the quality of a specification.
```

A task with `artifact` inherits the output contract, Post and Evaluation of
the artifact type:
- Task `output.*` values take precedence over the artifact values.
- Artifact Post and Evaluation instructions come first in the corresponding
  task checks, followed by the task instructions.

### Task Template and Profile

- A task template is a task file with `params`. An elaborated task with
  `from` is materialised when the elaboration is applied: the template is
  expanded with the given `params`, then the elaborated keys and sections take
  precedence. The task records `from` as `{ref,digest}`, so later template
  changes do not affect it (see Plan immutability).
- A standalone script may also give `from` (eg. `from: task/audit`, short
  for `auto:catalogue/task/audit`): it is composed with the template when it
  runs, its sections filling the template inputs they name (see
  [Agent Script](spec-002-script.md#templates)).
- A task template may itself be such a script (an action): one giving
  `from`, or whose static body has no `## Process` but sections of its own.
  It is composed as `auto run` would compose it, and its content becomes the
  Process, its own sections nested one level down. The elaborated keys still
  take precedence. A template choosing its sections with Liquid is a task
  template.
- A profile gives `engine`, `model`, `agent`, `temperature`, a default
  `budget`, a `memory` profile (`minimal`, `standard` or `full`, see
  [Memorisation](spec-010-memorisation.md#actuation)), and a description of
  its capabilities to guide selection. A cheap model usually pairs with
  `minimal` memory.
  Runtime settings are resolved by decreasing precedence: explicit task
  values, the profile, the script values (see Script composition), then the
  runtime defaults. A profile may leave any setting unset.

## Catalogue

The catalogue makes framework files available across plans and projects:
- Files are stored as `{catalogue}/{kind}/{name}.md`, using Memo front matter
  conventions (`kind`, `identity`, `title`, `description`, tags), so that Memo
  can index and query them.
- Companion files of an entry (eg. prompt fragments, templates) are stored
  next to it as `{catalogue}/{kind}/{name}.{companion}.{ext}` (eg.
  `task-elaboration.template.md`), and transcluded by relative path. Entry
  names contain no `.`, so companion files are not catalogue entries.
- Their identity is `auto:catalogue/{kind}/{name}`, resolved by Auto without
  Memo. `catalogue` is reserved and is not a valid plan id.
- Catalogues are layered by decreasing precedence. The first match wins:

| Layer | Location |
|---|---|
| Project | `.auto/catalogue/` |
| Actions | Project scripts matched by `[catalogue] actions` globs, as `task` entries |
| User | `~/.config/auto/catalogue/` |
| Built-in | `src/md/auto/catalogue/`, shipped with Auto |

- *Actions* are the project's own scripts, kept where the project keeps them
  (eg. `action-*.md` at its root), and made available to plans with:

  ```toml
  [catalogue]
  actions = ["action-*.md", "actions/*.md"]
  ```

  Globs are relative to the project. Each matched Markdown file is the task
  entry `auto:catalogue/task/{stem}`; the first match wins, and stems that are
  not identifiers are left out.
- `CatalogueList` gives each task entry its *signature*, so that it can be
  instantiated without reading it: the template it `specialises`, the
  `inputs` left to its callers (those it does not give itself, in its
  frontmatter or its sections), the `required` ones among them, its `params`
  with their defaults, its `access` and its `artifact`. An action specialising
  a template without its own `description` is described by its first
  paragraph. The markdown listing gives the signature on a second line.
- An Elaborator may be given catalogue entries as content, as references, or
  query them through the harness (eg. a Memo query). The built-in one is
  given the listing, and prefers the most specific entry doing what a task
  needs, such as an action specialising a built-in template.

## Format

A TaskElaboration is a markdown document. The frontmatter holds its fields,
and each element is a second level section with a YAML block, followed by its
content:

````markdown
---
task: root
summary: Collect, summarize and publish the changes
methodology: auto:catalogue/methodology/docs
context:
  - ./CHANGELOG.md
  - ./docs/specs/
learnings:
  - memo:agent-writer/learning:Summaries node=need_audience
---
## Rationale

Why the tasks are structured this way.

## task: summarize-changes

```yaml
artifact: auto:catalogue/artifact/summary
profile: fast
phase: draft
inputs:
  changes: task:collect-changes
hints: {gate: approval}
```

### Process

Instructions.

## task: review

```yaml
from: auto:catalogue/task/review
params: {focus: tone}
model: claude-opus-5-5
```

## gap: audience

```yaml
kind: decision
options: [developers, users]
needed_by: [summarize-changes]
```

Who are the release notes for?

## remove: draft-notes

Superseded by summarize-changes.
````

- `## task: {id}` holds the task frontmatter in the YAML block, and the task
  sections (Pre, Process, Post, Evaluation) as third level sections.
- `## gap: {name}` holds the gap fields in the YAML block, and the question as
  content. Auto generates the gap id when applying the elaboration.
- `## remove: {id}` holds the reason as content.
- A rework is a `## task:` section with `replaces`.

## Decomposition

Elaborated tasks become children of the elaborated task, which stays as an
aggregate (see Plan aggregates):
- The aggregate remains responsible for the output. Its process, if any,
  receives the outputs of its children as the `children` input, and its
  checks assess the assembled output.
- When the aggregate fails, it may be elaborated again, with the failure as
  the `state`: children are added, removed or reworked.
- A task with `hints.elaborate: later` is elaborated instead of run, once it
  is Ready.

## Operations

Catalogue:
- `CatalogueList(context,kind?,query?)`: lists the entries of all layers, the
  first match winning, with their identity, kind, title, description and
  layer.
- `CatalogueGet(context,identity)`: returns an entry, from the first layer
  defining it.

Elaboration:
- `ElaborationStart(context,plan,task,reason?)`: runs the plan Elaborator on
  the TaskGoal of the task, returning the run.
- `ElaborationParse(context,text)`: parses a TaskElaboration.
- `ElaborationValidate(context,plan,elaboration)`: returns the violations of
  the TaskElaboration (see Validation), each with the element concerned
  (`task:{id}`, `gap:{name}`, `remove:{id}` or the elaboration) and
  a message. A TaskElaboration is valid when there are no violations.
- `ElaborationApply(context,plan,elaboration,run)`: applies a valid
  TaskElaboration as a PlanRevision.

`PlanCreate` and `PlanRefine` (see Plan) start the elaboration, then validate
and apply its TaskElaboration.

Triage:
- `GoalTriage(context,goal)`: decides, in one turn with the decision profile
  (`auto:catalogue/task/triage`, `auto:catalogue/profile/decision`), whether a
  goal runs `direct`, as one task, or is planned (`plan`), and which effects
  it needs (built-in effects other than `repo.read`). It is given the goal,
  the project's files known to git (up to 200) and the effects with their
  rules. Any failure answers `plan`, with the reason: triage never blocks.
- `PlanCreate(…, route)`: `plan` (the default) elaborates the goal; `direct`
  writes a root that does it, with the goal as its Process and `route:
  direct`, without elaboration; `triage` lets `GoalTriage` choose. Triage
  runs once the plan exists, so its usage counts to the plan.
- `PlanRefine` of a direct root turns it back into a goal to plan (its
  Process, `route` and `effects` are removed) before elaborating it.

## Rules

- *Elaborator*: the plan gives its Elaborator and budget (`elaborator.task`,
  `elaborator.budget`); the default is the built-in
  `auto:catalogue/task/elaborate`. Elaborator runs are stored with the plan
  runs but are not tasks of the plan.
- *Research*: Elaborators run with `isolated: false` by default, so the
  harness can use the project agents, skills and tools to explore the
  context.
- *Provenance*: references listed in the TaskElaboration `context` are
  recorded with their digest in the revision. A plan whose recorded context
  has changed is reported as stale by `PlanStatus`; acting on it belongs to
  Orchestration.
- *Budget*: Elaborator runs draw from `elaborator.budget`, itself drawn from
  the plan budget. Elaborated task budgets fit within the remaining plan
  budget, and within the budget of the elaborated task, if any. The remaining
  budget deducts the usage of all runs and the unspent allocations of
  retained tasks that have not succeeded; a succeeded task reserves nothing.
- *Grounding*: elaborated tasks only reference plan inputs, task outputs,
  framework files and assets that resolve. What cannot be resolved is raised
  as a gap rather than invented (see Plan elaboration).
- *Validation*: before anything is written, Auto checks that:
  1. The TaskElaboration is well-formed, and ids are unique and new, except
     `replaces` targets and removals
  2. Every elaborated task is valid (see `TaskValidate`)
  3. `from`, `artifact`, `profile`, explicit overrides and references
     resolve
  4. There is no dependency cycle, including parents and children
  5. Budgets fit (see Budget)
  6. The TaskElaboration conforms to its methodology, when declared
- *Methodology*: a declared methodology is strict. Conformance requires that
  every phase has a task, that each phase artifact is produced by a task of
  the phase, that phase gates are raised, and that no task depends on a task
  of a later phase. Conformance is a Post check of the Elaborator (typically
  a shell check, see Task): a violation fails the run as `output`, with the
  violations as feedback for a retry. The default Elaborator allows `repair`
  attempts (see Task), so it corrects its output from the violations before
  failing.
- *Learnings*: when the Elaborator does not supply `learnings`, Auto supplies
  the plan learnings. Applied learnings are cited in the TaskElaboration and
  recorded in the revision.
- *Application*: a TaskElaboration is applied as a single PlanRevision, whose
  author is the Elaborator run and whose reason is the elaboration reason.
  Elaborated tasks become `add` (with the elaborated task as `parent`) or
  `replace`, removals become `remove`, gaps become `raise`, and `hints.gate`
  raises an approval gap once the output exists.

## Progression

Elaborators are expected to grow from simple to refined, without changing the
interface or the TaskElaboration format:
1. *Standalone*: a single task file, with the methodology, artifact
   descriptions and instructions written inline.
2. *Extracted*: parts moved into framework files and transcluded, like
   `@./framework/sdlc.md`.
3. *Variants*: parameters select parts with template logic, like
   `{% if params.governance %}@./passes/gates.md{% endif %}`.
4. *Shared*: framework files published to the catalogue and referenced by
   `auto:catalogue/{kind}/{name}`.

The built-in Elaborator is a reference for these stages, and its prompts are
assets rather than part of this specification.

### Built-in Elaborator

`auto:catalogue/task/elaborate` is the default Elaborator, at the *Extracted*
and *Variants* stages. It produces an `auto:catalogue/artifact/task-elaboration`,
and its Post is a shell check running `auto x elaboration validate`. It takes two
parameters, which vary independently:

| Param | Values | Effect |
|---|---|---|
| `breakdown` | `coarse` (default), `fine` | How far the goal is broken down in one pass: few tasks deferring detail with `elaborate: later`, or tasks ready to run |
| `detail` | list of `contract`, `allocation`, `governance` | Which aspects are filled in for each task, in addition to `core` |

For example, `coarse` with `governance` gives few tasks with gates, for a plan
reviewed step by step, while `fine` with no `detail` gives many lightweight
tasks, for a plan run like a script.

### Built-in Audit

`auto:catalogue/task/audit` checks a scope of the project against a list of
criteria, from one perspective (eg. conventions, structure, symbols,
documentation, UI/UX, security). It produces an `auto:catalogue/artifact/audit`,
whose Post runs `auto x audit validate`. It runs with `access: read`. An
audit is specialised by a task giving `### Scope` and `### Criteria` in its
Process, then transcluding `@auto:catalogue/task/audit#Process` (see
`auto:catalogue/task/audit-symbols`), or by a script giving `from: task/audit`
with `## Scope` and `## Criteria` sections, the rest of its body being the
`brief`. Recorded runs of the same template, facet and scope give the next
run its `baseline` (see [Agent Script](spec-002-script.md#recorded-runs)).

`auto:catalogue/task/audit-resolve` acts on the findings of an Audit report,
given as its required `audit` input (eg. `auto:runs/{id}`, or `task:{id}` in
a plan). It runs with `access: write` and produces an
`auto:catalogue/artifact/audit-resolution`, whose Post runs
`auto x resolution validate`. Its parameters vary independently:

| Param | Values | Effect |
|---|---|---|
| `severity` | list of severities, default `[critical, major]` | Selects the findings to resolve, when `findings` is empty |
| `findings` | list of finding ids | Selects these findings instead, eg. to split a large audit across tasks |
| `mode` | `fix` (default), `triage` | Fix the findings, or only decide what to do with each, changing nothing |

Each selected finding is `fixed` (listing the locations changed), `to-fix`
(triage), `stale`, `deferred`, `wont-fix` or `needs-decision`, with a reason;
choices left to people are gathered under `Decisions`. A fix is verified by
re-running the audit with the resolved report as `baseline`, whose `Changes`
then list the fixed findings. To fix an audit's findings, the built-in
Elaborator chains the audit, the resolution and the re-audit.

## Out of Scope

- Assessing elaboration quality against plan outcomes, and triggering
  refinement (see Workflow)
- Acting on hints, selecting which ready task to run (see Orchestration)
- Capturing learnings from Elaborator runs (see [Memorisation](spec-010-memorisation.md))
- The prompts of the built-in Elaborator

## References

- [GitHub Spec Kit](https://github.github.com/spec-kit/index.html) and
  [OpenSpec](https://openspec.dev/) are examples of methodologies that can be
  expressed as a `methodology` framework file.
- [Memo](https://github.com/sebastien/memo) front matter conventions are used
  for catalogue files.
