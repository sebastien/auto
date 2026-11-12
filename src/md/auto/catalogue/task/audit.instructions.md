### Method

1. *Normalise the criteria*. Keep the identifier of each criterion as given
   (eg. `SYM-01`), and assign `CRIT-01`, `CRIT-02`, ... in order to those
   without one. A criterion that cannot be checked as written is reported as
   `unverifiable`, with the reason, rather than interpreted.

2. *Inventory the scope*. List what you actually audit: the items in scope,
   what was excluded and why, and, when sampling, how the sample was chosen.

3. *Assess*. Check each criterion against the items in scope. Record
   evidence for each failure: a location (`path:line`, a symbol, a page) and
   a short quote or description of what does not comply.

4. *Consolidate*. Report one finding per distinct problem, listing all its
   locations, rather than one finding per occurrence. A finding relates to
   exactly one criterion; a problem breaking several criteria is several
   findings.

5. *Rate*. Give each finding a severity, and each criterion a status:
   - Severity: `critical` (must be fixed before relying on the project),
     `major` (significant, should be fixed), `minor` (localised or cosmetic),
     `info` (worth knowing, no change required).
   - Status: `pass` (no finding), `partial` (findings, but mostly complies),
     `fail` (does not comply), `n/a` (nothing in scope it applies to),
     `unverifiable` (could not be checked).

6. *Conclude*. Give the verdict: `compliant` when every criterion passes or
   is `n/a`, `non-compliant` when any criterion fails or any finding is
   `critical`, and `partial` otherwise.

### Rules

- Do not modify files: this is an audit, not a fix.
- Every finding cites a criterion. Remarks outside the criteria go to
  `Observations`, never to `Findings`.
- Only cite locations you have checked. Never invent paths, lines or symbols.
- State the limits of the audit: what was not covered, sampled, or could not
  be verified.
- Be precise rather than exhaustive in wording: one or two sentences of
  evidence per finding.
