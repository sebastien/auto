### Method

1. *Understand*. For each selected finding, read its criterion, evidence and
   locations, and check the finding still holds. A finding that no longer
   holds is `stale`: say what you checked.

2. *Decide*. Choose what to do with each finding:
   - Fix it when the criterion says what compliance is, and the change is
     contained (`fixed`, or `to-fix` in triage).
   - Leave a choice to people when compliance can be reached in several ways
     that change behaviour, interfaces or documents others rely on
     (`needs-decision`): give the options and their consequences.
   - Defer it when the fix is too large or too risky for this resolution
     (`deferred`): say why, and what a follow-up should do.
   - Do not fix it when the finding is a false positive or the criterion
     does not apply (`wont-fix`): give the evidence.

3. *Fix* (fix mode only). Make the smallest change that complies with the
   criterion, one finding at a time, consistently across all its locations.
   Run the project's relevant tests or checks when it has them, and fix what
   your changes break.

4. *Report*. One subsection per selected finding, titled with its id and
   title as in the audit, giving its status, the locations changed, and in
   one or two sentences what was done or why not. Gather the choices left to
   people under `Decisions`, and the checks run under `Verification`.

### Rules

- Only resolve the selected findings: do not fix other findings, or make
  unrelated changes, even when you notice them; mention them under
  `Verification` instead.
- Do not change the criteria or the audit report.
- In triage, change nothing in the project.
- Only list locations you have changed, and never claim a fix you have not
  made.
