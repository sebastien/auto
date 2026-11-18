````markdown
---
mode: {fix | triage}
selected: [{F1}, {F2}]
summary: {one line summary of what was done}
---
## Resolutions

### F1: {finding title, as in the audit}

- status: {fixed | to-fix | stale | deferred | wont-fix | needs-decision}
- changes:
  - {path:line changed, for fixed findings}

{What was done, or why not: the reason for stale, deferred and wont-fix, the
options for needs-decision.}

## Decisions

- {Optional: the choices people need to make, with their options and the findings they unblock.}

## Verification

- {Optional: the checks run (tests, linters), and the audit to re-run with this report's audit as baseline.}
````
