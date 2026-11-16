---
kind: artifact
identity: auto:catalogue/artifact/audit
title: Audit report
description: >-
  The result of checking a scope of the project against a list of criteria:
  a verdict, the status of each criterion, and evidenced findings, as
  produced by `auto:catalogue/task/audit` and its specialisations.
output:
  format: markdown
  template: ./audit.template.md
---
# Audit report

An Audit report: frontmatter with the verdict and counts, then the `Scope`,
`Criteria` and `Findings` sections, and optionally `Observations`,
`Recommendations` and `Changes`. Criteria and findings are lists, so that
they can be edited and compared as plain markdown.

Its Post runs `auto x audit validate`, which fails when:

- The scope lists no audited item, or there is no criterion.
- A criterion has no status among `pass`, `partial`, `fail`, `n/a` and
  `unverifiable`, or is `partial` or `fail` without listing its findings.
- A finding references an unknown criterion, has no severity among
  `critical`, `major`, `minor` and `info`, or lists no location.
- The `findings` counts in the frontmatter do not match the findings.
- The verdict does not match the statuses: `compliant` when every criterion
  is `pass` or `n/a`, `non-compliant` when any criterion is `fail` or any
  finding is `critical`, and `partial` otherwise.

## Post

@auto:catalogue/check/audit-valid

## Evaluation

Score the report from 0 to 1 against the following, and give feedback on the
weakest points:

- *Coverage*: the audited items match the scope, and limits are stated.
- *Evidence*: each finding is backed by locations and evidence that can be
  checked.
- *Precision*: findings follow from the criteria, with no false positives
  and no remarks outside the criteria reported as findings.
- *Consolidation*: one finding per distinct problem, not per occurrence.
- *Calibration*: severities are proportionate and consistent.
- *Actionability*: recommendations, when requested, are specific and
  ordered by impact.
