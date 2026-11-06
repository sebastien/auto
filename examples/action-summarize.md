---
title: Summarize changes
description: Write a commit description summarizing the current change
temperature: 0.1
inputs:
  # Revision to compare to; empty means the parent (`@-` for jj, `HEAD` for git)
  base: ""
params:
  # When true, save the message as the description of the current change
  apply: false
output:
  format: markdown
  template: |
    [${type}] ${scope}: ${shortDescription}

    ${summary}

    Changed:
    - ${scope}: ${description}

    Added:
    - ${scope}: ${description}

    Removed:
    - ${scope}: ${description}
---
Summarize the changes made in this repository, surfacing the key changes, and
write them as a commit description.

## Context

Version control status:

!if [ -d .jj ]; then jj status; else git status --short; fi

Changes compared to {% if base == "" %}the parent revision{% else %}`{{ base }}`{% endif %}:

!b={{ base }}; if [ -d .jj ]; then jj diff --stat --from "${b:-@-}"; else git diff --stat "${b:-HEAD}"; fi

## Method

1. Use `jj` when the repository has a `.jj` directory, `git` otherwise.
2. Read the diff of the changed files to understand each change.
3. Compare the symbols of the changed files (`sdk-symbols FILE…`) with their
   version in the base revision to identify external API changes.
4. Identify the changes done in each module.
5. Identify groups of changes sharing a same scope or feature across modules.
6. When changes that are not directly related could be grouped into separate
   commits, suggest how to split them after the message.
7. Write a high-level summary of the changes first, then detail what has
   changed, been added and been removed.
8. Explicitly mention any breaking change (data schema, external contract).

Where, in the output format:

- `type` is the overall kind of change, like `New`, `Update`, `Fix` or `Refactor`
- `shortDescription` is a one-line overview
- `summary` is a couple of paragraphs summarizing the changes, explicitly
  mentioning any breaking change
- `scope` is the module or feature
- Sections without entries are omitted

## Rules

- Do not run the build system, checks, linters, tests or CI.
- Do not modify files.
{% if params.apply %}- Save the message as the description of the current change
  (`jj describe -m MESSAGE`, or `git commit --amend -m MESSAGE` when there is
  no `.jj` directory), and do not split the change.
{% else %}- Do not modify the repository: only output the message.
{% endif %}- Output plain markdown following the format above, without tables.
