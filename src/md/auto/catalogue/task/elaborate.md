---
kind: task
identity: auto:catalogue/task/elaborate
title: Elaborate a task goal
description: >-
  Breaks the goal of a task into child tasks and gaps, producing
  a TaskElaboration. Default Elaborator of a plan.
isolated: false
profile: auto:catalogue/profile/default
artifact: auto:catalogue/artifact/task-elaboration
# Corrective attempts when the elaboration fails its checks
repair: 2
# Room for the first attempt and its corrections, within the plan budget
budget:
  tokens: 300000
  time: 30m
inputs:
  # Id of the elaborated task, supplied by Auto
  target: root
  goal: ""
  state: ""
  budget: ""
  policy: ""
  context: []
  methodology: ""
  catalogue: "!auto catalogue list --format markdown"
  learnings: ""
params:
  breakdown: coarse
  detail: []
---
# Elaborate a task goal

Breaks the goal of a task into child tasks and gaps. `goal`, `state` and
`budget` are supplied by Auto; `learnings` defaults to the plan learnings.

## Process

You are elaborating a task: breaking its goal down into child tasks that,
once run, deliver it. Your output is a TaskElaboration, applied to the plan
after validation.

The elaborated task is `{{ target }}`: set `task: {{ target }}` in the
frontmatter. Its children are new tasks, with ids not used in the plan (never
`{{ target }}` itself). Task ids carry a `tsk-` prefix, which Auto adds when
you omit it: `build-site` and `tsk-build-site` name the same task.

### Goal

{{ goal }}

{% if state != blank %}
@./elaborate.state.md

### Plan state

{{ state }}
{% endif %}

### Context

{% if context.size > 0 %}
{% for ref in context %}- {{ ref }}
{% endfor %}
{% else %}
No context was given beyond the goal and the plan inputs.
{% endif %}

Explore the context with your tools as much as the structure of the plan
requires. List everything you relied on in the `context` field of your
output.

{% if methodology != blank %}
### Methodology

Your elaboration must conform to this methodology: every phase has at least
one task with its `phase`, each phase artifact is produced by a task of the
phase, phase gates are raised, and no task depends on a task of a later
phase.

{{ methodology }}
{% endif %}

### Available framework

Task templates, artifact types, profiles and checks you may reference by
identity. Do not reference entries that are not listed here.

{{ catalogue }}

{% if learnings != blank %}
### Learnings

Learnings from previous runs and decisions. Apply those that are relevant,
and cite each one you apply, in the `learnings` field of the elaboration or
of the task it affects.

{{ learnings }}
{% endif %}

### Budget

{% if budget != blank %}{{ budget }}{% else %}No budget was given.{% endif %}

### Policy

{{ policy }}

Declare each task's expected effects. An effect without a rule in `rules`
follows the `default` rule. An effect listed in `grants` (for the `plan`, or
for that `task:ID`) is already authorised: raise no gap for it, whatever its
rule. Otherwise, do not plan around denied effects; request a human
decision for a grant. Effects requiring confirmation need an approval
checkpoint pinned to the task definition. Each policy gap must list
its effects in `effects`, name the affected task in `needed_by`, and mention
each effect and its rule (`confirm` or `deny`) in its question. For an approval,
include `subjects: ["task:ID/definition"]`; Auto pins the proposed definition.

@./elaborate.instructions.md

{% if params.breakdown == "fine" %}
@./elaborate.breakdown-fine.md
{% else %}
@./elaborate.breakdown-coarse.md
{% endif %}

{% if params.detail contains "contract" %}
@./elaborate.aspect-contract.md
{% endif %}
{% if params.detail contains "allocation" %}
@./elaborate.aspect-allocation.md
{% endif %}
{% if params.detail contains "governance" %}
@./elaborate.aspect-governance.md
{% endif %}

## Post

@auto:catalogue/check/elaboration-valid
