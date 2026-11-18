---
kind: task
identity: auto:catalogue/task/audit
title: Audit the project
description: >-
  Checks the project against a set of criteria, from one perspective (eg.
  code conventions, file structure, symbol names, documentation, UI/UX,
  security), producing an Audit report. Specialise it with a task giving
  `### Scope` and `### Criteria` in its Process, then transcluding
  `@auto:catalogue/task/audit#Process`, or with a script giving `from:
  task/audit` and `## Scope` and `## Criteria` sections.
isolated: false
access: read
profile: auto:catalogue/profile/default
artifact: auto:catalogue/artifact/audit
inputs:
  # Perspective of the audit, eg. "symbol names"
  facet: ""
  # What to audit: paths, globs or descriptions, as references
  scope: []
  # What items in scope should have: a list of `- ID: criterion`, or a PATH
  criteria: ""
  # What the audit is about and why, in the author's words
  brief: ""
  # References the auditor may read, eg. specs and conventions
  context: []
  # Previous audit report, to report new, fixed and persisting findings
  baseline: ""
  learnings: ""
params:
  # `full` audits every item in scope, `sample` a stated representative sample
  coverage: full
  # Include a recommendation per finding and a prioritised list
  recommend: true
---
## Process
{% if scope.size > 0 %}
### Scope

{% for ref in scope %}- {{ ref }}
{% endfor %}
{%- endif %}
{% if criteria != blank %}
### Criteria

{{ criteria }}
{%- endif %}
{% if brief != blank %}
### Brief

What the audit is about, as given by its author. Use it to interpret the
scope and the criteria; it does not add criteria.

{{ brief }}
{%- endif %}

### Audit

You are auditing the project{% if facet != blank %} from the perspective of
*{{ facet }}*{% endif %}: checking the items in the *Scope* section against
the *Criteria* section, and reporting where they comply and where they do
not. Your output is an Audit report.

If the scope or the criteria are missing, do not audit: report what is
missing, with an `unverifiable` status for each criterion given.
{% if context.size > 0 %}
### Context

Read these as needed to interpret the criteria and the items in scope:

{% for ref in context %}- {{ ref }}
{% endfor %}
{%- endif %}
{% if baseline != blank %}
### Baseline

A previous audit of the same scope and criteria. Compare your findings with
it in the `Changes` section: new, fixed and persisting findings.

{{ baseline }}
{%- endif %}
{% if learnings != blank %}
### Learnings

Learnings from previous runs. Apply those that are relevant.

{{ learnings }}
{%- endif %}

@./audit.instructions.md

{% if params.coverage == "sample" %}
### Coverage: sample

Audit a representative sample of the items in scope rather than every item.
State in `Scope` how the sample was chosen and how many items it covers out
of the total. Do not extrapolate findings to items you have not checked.
{% else %}
### Coverage: full

Audit every item in scope. If that is not possible within your budget, audit
as much as you can, and state in `Scope` exactly what was and was not covered.
{% endif %}

### Recommendations

{% if params.recommend %}
Give each finding a recommendation: what to change to comply, specific
enough to act on. End with `Recommendations`, ordered by impact.
{% else %}
Report findings only: omit recommendations and the `Recommendations` section.
{% endif %}
