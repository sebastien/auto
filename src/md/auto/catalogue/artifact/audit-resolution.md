---
kind: artifact
identity: auto:catalogue/artifact/audit-resolution
title: Audit resolution
description: >-
  What was done about the findings of an Audit report: for each selected
  finding, whether it was fixed (with the locations changed), is stale,
  deferred, not to be fixed, or needs a decision, as produced by
  `auto:catalogue/task/audit-resolve`.
output:
  format: markdown
  template: ./audit-resolution.template.md
---
# Audit resolution

An Audit resolution: frontmatter with the mode and the selected findings,
then one `Resolutions` subsection per selected finding, and optionally
`Decisions` and `Verification`.

Its Post runs `auto x resolution validate`, which fails when:

- The mode is not `fix` or `triage`, or `selected` is not a list of ids.
- A selected finding has no resolution, a resolution is not selected, or a
  finding is resolved twice.
- A status is not one of `fixed`, `to-fix`, `stale`, `deferred`, `wont-fix`
  and `needs-decision`; `fixed` in triage, or `to-fix` in fix mode.
- A `fixed` finding lists no changed location.

## Post

@auto:catalogue/check/audit-resolution-valid

## Evaluation

Score the resolution from 0 to 1 against the following, and give feedback on
the weakest points:

- *Selection*: the selected findings are those the selection asked for.
- *Effectiveness*: fixed findings comply with their criterion, and a re-audit
  would report them as fixed.
- *Restraint*: changes are limited to the selected findings, with no
  unrelated edits.
- *Honesty*: deferred, not fixed and stale findings give reasons that can be
  checked; decisions are left to people.
- *Traceability*: every change is listed with its location.
