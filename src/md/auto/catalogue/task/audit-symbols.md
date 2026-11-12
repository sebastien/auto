---
kind: task
identity: auto:catalogue/task/audit-symbols
title: Audit symbol names
description: >-
  Example specialisation of `auto:catalogue/task/audit`: checks that the
  names of modules, types, functions and variables are consistent and
  reflect the domain. Copy it to `.auto/catalogue/task/` and adapt the scope
  and criteria to your project.
profile: auto:catalogue/profile/default
artifact: auto:catalogue/artifact/audit
inputs:
  facet: symbol names
---
## Process

### Scope

The source files tracked by version control, excluding tests:

!git ls-files '*.py' '*.js' '*.mjs' '*.ts' '*.tsx' '*.go' '*.rs' '*.java' | grep -vE '(^|/)tests?/|\.test\.' | head -500

### Criteria

- SYM-01: Names use the casing convention of their kind (module, type,
  function, variable, constant) consistently across the code base.
- SYM-02: Names use the vocabulary of the domain: the same concept has the
  same name everywhere, and different concepts have different names.
- SYM-03: Functions are named after what they do (a verb or verb phrase),
  and types and variables after what they are (a noun or noun phrase).
- SYM-04: Abbreviations are avoided, except for well-known ones used
  consistently (eg. `id`, `url`).
- SYM-05: Names are specific: generic names (eg. `data`, `info`, `manager`,
  `utils`, `helper`) are not used where a more precise name exists.
- SYM-06: Booleans read as predicates (eg. `isReady`, `hasChildren`).

@auto:catalogue/task/audit#Process

Start from the declarations (modules, types, top-level functions) before
looking at local variables, and group findings by concept rather than by
file.
