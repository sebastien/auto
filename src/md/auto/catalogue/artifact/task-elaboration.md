---
kind: artifact
identity: auto:catalogue/artifact/task-elaboration
title: Task elaboration
description: >-
  The breakdown of the goal of a task into child tasks and gaps, as produced
  by an Elaborator (see spec-006 Elaboration).
output:
  format: markdown
  template: ./task-elaboration.template.md
---
# Task elaboration

A TaskElaboration: frontmatter with the elaboration fields, a `## Rationale`
section, then one second level section per element (`## task: {id}`,
`## gap: {name}`, `## remove: {id}`), each starting with a YAML block.

## Post

Check the elaboration against the following, and fail with the list of
issues found:

- Every input, file and catalogue entry referenced by a task is either a plan
  input, the output of a task (`task:{id}`), a file listed or found in the
  context, or an entry of the available framework.
- Every gap has a `kind` and a question, and its `needed_by` lists at least
  one task, or `root`.
- Every task has a `title`, and a `### Process` section unless it uses `from`
  or `hints: {elaborate: later}`.
- No task id is `root`, and no id is used twice.

## Evaluation

Score the elaboration from 0 to 1 against the following, and give feedback on
the weakest points:

- *Coverage*: once all tasks succeed, the goal is achieved and its definition
  of done is met. Nothing essential is missing.
- *Minimality*: no task is redundant, and no task could be merged into
  another without losing a clear output.
- *Breakdown*: the granularity matches the requested breakdown: coarse
  elaborations defer detail, fine ones are runnable as is.
- *Grounding*: gaps are raised where information is genuinely missing, and
  nothing is invented.
- *Realism*: budgets, when given, are plausible for the work, and leave
  a margin within the available budget.
- *Rationale*: the structure is explained, including what was left out.
