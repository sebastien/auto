---
kind: task
identity: auto:catalogue/task/audit-resolve
title: Resolve audit findings
description: >-
  Acts on the findings of an Audit report: fixes those selected by severity
  or by id, and states for each whether it was fixed, is stale, deferred, not
  to be fixed, or needs a decision, producing an Audit resolution. Verify it
  by re-running the audit with the resolved report as `baseline`.
isolated: false
access: write
profile: auto:catalogue/profile/default
artifact: auto:catalogue/artifact/audit-resolution
inputs:
  # The Audit report to resolve, eg. auto:runs/ID or task:ID
  audit: ~
  # References the resolver may read, eg. specs and conventions
  context: []
  # What else to know about resolving, in the author's words
  brief: ""
  learnings: ""
params:
  # Severities of the findings to resolve, when `findings` is empty
  severity: [critical, major]
  # Ids of the findings to resolve, instead of selecting by severity
  findings: []
  # `fix` changes the project; `triage` decides what to do with each finding, changing nothing
  mode: fix
---
## Process

You are resolving the findings of the Audit report below{% if params.mode == "triage" %}:
deciding what to do with each selected finding, without changing the project{% else %}:
fixing each selected finding in the project{% endif %}. Your output is an
Audit resolution, with `mode: {{ params.mode }}`.

### Selection

{% if params.findings.size > 0 %}Resolve these findings, and only these: {{ params.findings | join: ", " }}.
{% else %}Resolve the findings of severity {{ params.severity | join: ", " }}, and only these.
{% endif %}
List the selected finding ids in `selected`, in the order of the report. When
none is selected, give `selected: []` and say so in the summary.
{% if brief != blank %}
### Brief

{{ brief }}
{%- endif %}
{% if context.size > 0 %}
### Context

Read these as needed to interpret the criteria and the findings:

{% for ref in context %}- {{ ref }}
{% endfor %}
{%- endif %}
{% if learnings != blank %}
### Learnings

Learnings from previous runs. Apply those that are relevant.

{{ learnings }}
{%- endif %}

### Audit report

{{ audit }}

@./audit-resolve.instructions.md
