---
title: Summarize changes
description: Write a commit description summarizing a change
temperature: 0.1
inputs:
  # Revision to describe; empty means the current change (`@` for jj, the
  # working tree for git)
  rev: ""
  # Revision to compare to; empty means the parent of `rev`
  base: ""
params:
  # When true, suggest how to split unrelated changes after the message
  split: true
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
Summarize the changes made in {% if rev == "" %}the current change{% else %}revision `{{ rev }}`{% endif %}
of this repository, surfacing the key changes, and write them as a commit
description.

## Context

Revision:

!r={{ rev }}; if [ -d .jj ]; then jj log --no-graph -r "${r:-@}"; elif [ -n "$r" ]; then git log -1 "$r"; else git status --short; fi

Changes compared to {% if base == "" %}the parent revision{% else %}`{{ base }}`{% endif %}:

!r={{ rev }}; b={{ base }}; if [ -d .jj ]; then jj diff --stat --from "${b:-${r:-@}-}" --to "${r:-@}"; elif [ -n "$r" ]; then git diff --stat "${b:-$r~1}" "$r"; else git diff --stat "${b:-HEAD}"; fi

## Method

1. Use `jj` when the repository has a `.jj` directory, `git` otherwise.
2. Read the diff of the changed files to understand each change, using the
   same revisions as above (`jj diff --from BASE --to REV`, or `git diff`).
3. Compare the symbols of the changed files (`sdk-symbols FILE…`) with their
   version in the base revision to identify external API changes.
4. Identify the changes done in each module.
5. Identify groups of changes sharing a same scope or feature across modules.
{% if params.split %}6. When changes that are not directly related could be grouped into separate
   commits, suggest how to split them after the message.
{% endif %}7. Write a high-level summary of the changes first, then detail what has
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
- Do not modify files or the repository: only output the message.
- Output plain markdown following the format above, without tables{% if params.split == false %},
  and nothing but the message{% endif %}.
