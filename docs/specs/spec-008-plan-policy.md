# Plan Policy

## Purpose

We want to define which consequences of agent work are allowed, which need
human confirmation, and which are off limits, so that a plan can run with
little supervision while consequential actions remain under human control.
Releasing to staging may be fine, releasing to production may need explicit
confirmation, and making a commit may be off limits unless asked for. Policy
needs to support:

- *Declaration*: rules are declared, not improvised by agents
- *Scoping*: rules come from the user, the project and the plan, and can be
  tightened locally
- *Explicit requests*: only a human can lift a restriction, and the request
  is traceable
- *Planning*: the Elaborator places confirmations (checkpoints) where the
  policy requires them, unless the task states its effects
- *Enforcement*: the policy also applies during execution, since a plan does
  not bind what an agent does

Policy is a facet of the plan (see [Plan](spec-004-plan.md)): it adds
attributes to plans and tasks, and uses gaps and decisions for confirmations.

## Conceptual Model

Effect: a named class of consequence an action may have, as a dotted name
(eg. `vcs.commit`). The built-in effects are:

| Effect | Consequence |
|---|---|
| `repo.read` | Reading project files |
| `repo.write` | Changing project files in the plan workspace |
| `shell.run` | Running local commands (tests, builds, linters) |
| `vcs.commit` | Recording changes in version control |
| `vcs.push` | Publishing changes to a remote (push, PR, release tag) |
| `net.read` | Reading from external services |
| `net.write` | Writing to external services (issues, messages, APIs) |
| `deploy.{env}` | Deploying to an environment (eg. `deploy.staging`, `deploy.production`) |
| `data.delete` | Deleting data outside the plan workspace |
| `memory.{effect}` | Changing agent memory: `observe`, `consolidate`, `promote`, `evolve` (see [Memorisation](spec-010-memorisation.md#policy)) |

Projects may declare further effects. A rule on an effect prefix applies to
the effects below it (eg. `deploy` covers `deploy.production`), the most
specific rule taking precedence.

Rule: what an effect requires.
- `allow`: done without confirmation
- `confirm`: done only after an approval (see Checkpoint)
- `deny`: not done, unless granted (see Grant)

Policy: `{effect: rule}`. An effect without a rule is `confirm`.

Grant: an explicit request by a human that lifts a `deny` or `confirm` rule.
- `effect`: the effect granted
- `scope`: `auto:{plan}` or a task (`auto:{plan}/task/{id}`)
- `source`: the reference it traces back to: the plan goal
  (`auto:{plan}/task/root/definition`) or a Decision
- `author`: the human author of the source
- `revision`: the PlanRevision recording it

Effects (task facet): the effects a task is expected to have.
- `effects`: declared in the task definition, or determined by the Elaborator
- `checkpoint`: the Checkpoint guarding the task, if any

Checkpoint: an `approval` gap raised before a task with effects requiring
confirmation.
- `subjects`: the task definition, pinned by digest
- `needed_by`: the task
- `question`: the effects and the rules requiring confirmation

## Format

Policy is declared under `[policy]` in configuration files (see Sources), and
under `policy` in the root task (`tsk/root.md`):

```toml
[policy]
"repo" = "allow"
"shell.run" = "allow"
"deploy.staging" = "allow"
"deploy.production" = "confirm"
"vcs" = "deny"
```

```markdown
---
id: release-notes
goal: Publish release notes for version 2.0, and commit them
policy:
  net: deny
---
```

Tasks declare their expected effects with `effects`:

```markdown
---
id: publish-notes
effects: [repo.write, vcs.commit]
---
```

| Key | Type | Purpose |
|---|---|---|
| `policy` | `{effect: rule}` | Plan rules, in the root task only |
| `effects` | list of effects | Expected effects of the task, in task definitions |

Grants are recorded in the plan journal as a revision operation,
`grant(effect,scope,source)`, extending the PlanRevision operations (see
[Plan](spec-004-plan.md)).

## Sources

The effective policy of a plan is resolved from, by increasing precedence:

1. The built-in defaults: `repo.read` and `shell.run` are `allow`, everything
   else is `confirm`
2. The user configuration (`~/.auto/config.toml`,
   `$XDG_CONFIG_HOME/auto/config.toml`)
3. The project configuration (`.auto/config.toml`)
4. The plan (its root task)
5. The grants of the plan

Sources 2 to 4 may tighten any rule (`allow` → `confirm` → `deny`). They may
only loosen a rule from the built-in defaults, never one set by a lower source
(eg. a plan cannot `allow` what the project `deny`s). Loosening beyond that
requires a grant.

## Explicit Requests

A grant is the record of an explicit request, made by a human:

- *Goal*: a human requests effects explicitly through the `effects` argument
  of `PlanCreate` (`--effects '[vcs.commit]'` in the CLI). Plan creation records
  these requests as grants with the root task definition and its digest as
  source; changing that definition expires the grants. Goal prose alone never
  grants an effect. An Elaborator or a run cannot request goal grants.
- *Decision*: a decision by a human may grant effects, typically by answering
  a gap raised for a denied effect (see Planning). The grant is recorded with
  the Decision as source. A planning grant gap declares its `effects`; answering
  it with `{"grant": "effect.name", "scope": "auto:plan/task/id"}` records the
  grant in the same revision as the resolution. The effect must be covered by
  the gap, and the scope must be its plan or one of its `needed_by` tasks.
  A plain approval is insufficient to lift a denial.

Task definitions, Elaborator outputs, run outputs and learnings never grant
effects, even when they state them. Granting is recorded in the plan journal
and is visible in `PlanStatus`.

## Planning

The effective policy and the grants are inputs of the Elaborator, as the
well-known input `policy`, supplied by Auto (see
[Elaboration](spec-006-elaboration.md)). For each elaborated task:

- *Effects*: the Elaborator determines its expected effects, unless the
  task declares them. Declared effects are kept as is.
- *Allowed*: when every effect is `allow`ed or granted, the task runs
  without confirmation.
- *Confirm*: when an effect is `confirm` and not granted, the Elaborator
  raises a Checkpoint for the task.
- *Deny*: when an effect is `deny` and not granted, the Elaborator raises
  a `decision` gap asking for a grant, needed by the task. It never plans
  around the rule (eg. by renaming or splitting the action).

Checkpoints differ from `hints.gate` (see Elaboration): a Checkpoint
approves a task before it runs, a gate approves an output before its
dependents run.

Validation (see `ElaborationValidate`) adds a violation for any elaborated
task whose effects are not allowed, granted, or covered by a Checkpoint or
grant gap.

## Execution

Policy applies to runs, through the engine integration:

- *Mapping*: before a run starts, the runtime maps the effective policy, the
  task grants and its approved Checkpoint to the engine permissions (eg.
  OpenCode permissions, tool allow and deny lists), and to the shell
  permission policy (see [Script](spec-002-script.md)).
- *Requests*: an action requested at runtime whose effect is `confirm` or
  `deny`, and not covered by the task effects, its approval or a grant, is
  raised as a gap; the run is `Waiting` until a decision is made (see Plan
  rules). Within an approved task, its declared effects are covered.
- *Coverage*: each integration declares the effects it can enforce. Effects
  it cannot enforce are reported by `PolicyResolve` and `PlanStatus`, and the
  plan relies on planning alone for them.
- *Apply*: `apply` commands (see Task) are subject to the task effects like
  any other action.

## Operations

- `PolicyResolve(context,plan,task?)`: returns the effective policy of the
  plan or task, the source of each rule, the grants and the effects not
  enforceable by the engine.
- `PolicyGrant(context,plan,effect,scope,decision)`: records a grant from
  a human Decision, as a revision.

Events, added to the journal of the run concerned, if any:
- `PolicyRequested(effect,action)`: an action required a confirmation at
  runtime, raised as a gap.
- `PolicyDenied(effect,action)`: an action was refused by the engine.

## Rules

- *Human only*: a grant traces back to a human-authored source (the plan
  goal or a Decision). Elaborators and runs cannot grant effects.
- *Pinned approval*: a Checkpoint approves the task definition it pins. A
  rework of the task (see Plan immutability) needs a new Checkpoint.
- *Narrowing*: a source may tighten any rule; loosening is done by grants
  (see Sources).
- *Default*: an unknown or unruled effect is `confirm`.
- *Transparency*: the effects, rules and grants governing a task are
  visible in `PlanStatus`, and each Checkpoint states the effects and rules
  requiring it.
- *No silent bypass*: when an engine cannot enforce an effect, this is
  reported, never assumed enforced.

## Out of Scope

- Workspace isolation of concurrent plans (see Orchestration)
- Classification of arbitrary shell commands into effects, beyond what
  integrations declare
- Roles and multiple humans with different rights
- Policy learning: turning recurring approvals into rules (see [Memorisation](spec-010-memorisation.md))
