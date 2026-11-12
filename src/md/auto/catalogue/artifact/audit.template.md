````markdown
---
facet: {perspective of the audit, eg. symbol names}
verdict: {compliant | partial | non-compliant}
summary: {one line summary of the outcome}
coverage: {full | sample}, {N} of {M} items
findings: {critical: N, major: N, minor: N, info: N}
---
## Scope

- {item or group of items audited, eg. src/py/auto/**/*.py}
- Excluded: {item}, {reason}
- Sample: {how the sample was chosen, when sampling}

## Criteria

- {ID}: {pass | partial | fail | n/a | unverifiable} — {criterion}{, findings: F1, F2}{, reason when n/a or unverifiable}

## Findings

### F1: {short title}

- criterion: {ID}
- severity: {critical | major | minor | info}
- locations:
  - {path:line | symbol | page}

{Evidence: what does not comply, and why.}

{Recommendation: what to change to comply. Omit when not requested.}

## Observations

- {Optional: remarks outside the criteria.}

## Recommendations

1. {Optional: the changes to make, ordered by impact, with their findings.}

## Changes

- New: {findings not in the baseline}
- Fixed: {baseline findings no longer present}
- Persisting: {findings also in the baseline}
````
