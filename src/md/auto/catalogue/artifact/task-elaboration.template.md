````markdown
---
task: {id of the elaborated task, eg. root}
summary: {one line summary of the elaboration}
methodology: {identity of the methodology followed, if any}
context:
  - {reference relied on, eg. ./docs/specs/spec-001-structure.md}
learnings:
  - {reference of a learning applied to the elaboration as a whole}
---
## Rationale

{Why the tasks are structured this way, in this order, and what was left out.}

## task: {id}

```yaml
title: {short title}
goal: {intended outcome, when the title is not enough}
inputs:
  {name}: {plan:{input} | task:{id} | ./path | auto:catalogue/...}
# Optional keys, as needed:
# from: {task template identity}
# params: {template parameters}
# artifact: {artifact type identity}
# output: {format: markdown | text | json, template: ..., schema: ...}
# profile: {profile identity}
# engine, model, agent: {explicit overrides}
# budget: {tokens: N, cost: N, time: 10m}
# phase: {methodology phase id}
# hints: {refine: after, elaborate: later, gate: approval}
# replaces: {id of the task it reworks}
# learnings: [{reference of a learning applied to this task}]
```

### Process

{Instructions for the agent running the task. Omit when using `from` or
`hints: {elaborate: later}`.}

### Post

{Optional: pass or fail conditions, with `!?COMMAND` lines for shell checks.}

### Evaluation

{Optional: quality criteria.}

## gap: {name}

```yaml
kind: {input | information | decision | approval}
options: [{possible answers, if any}]
needed_by: [{task ids, or root}]
```

{The question, stating what is needed and why.}

## remove: {id}

{Why the task is removed. Refinement only.}
````
