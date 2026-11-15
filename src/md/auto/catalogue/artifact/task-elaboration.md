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

Its structural rules (resolvable references, valid gaps, titles, a Process
unless `from` or deferred, new and unique ids) are checked deterministically
by `auto elaboration validate`, in the Post of the Elaborator
(`auto:catalogue/check/elaboration-valid`), rather than judged by a model.

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
