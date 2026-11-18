---
title: Write a changelog
description: Write a user-facing changelog since the last release and describe a jj change
temperature: 0.1
inputs:
  # The jj change whose description will be replaced
  rev: "@"
  # Release boundary; leave empty to infer the latest release tag
  base: ""
output:
  format: markdown
  template: |
    # Overview

    ## What changed
    ## What is new
    ## What was removed
    ## What was fixed
apply:
  - jj describe -r {{ rev }} --stdin
---
Write a changelog for the `jj` change `{{ rev }}`, covering changes since the
last release, and use the changelog as that change's description.

## Context

Target change:

!jj log --no-graph -r '{{ rev }}'

Release tags:

!jj log --no-graph -r 'tags()' -T 'commit_id ++ " " ++ description.first_line() ++ "\\n"'

Available history:

!jj log --no-graph -r 'all()' -T 'commit_id ++ " " ++ description.first_line() ++ "\\n"'

## Method

1. Use `{{ base }}` as the release boundary when provided. Otherwise inspect
   release tags above and infer the latest release commit only when a tag or
   other clear release marker identifies it.
2. If no reliable release boundary can be identified, ask the user which
   commit to use and wait for their answer before generating the changelog.
   Do not treat the package version alone as evidence of a release commit.
3. Read the changes from the boundary (exclusive) through `{{ rev }}` with
   `jj diff --from BASE --to {{ rev }}`. If the user chooses the start of the
   available history, say so in the overview.
4. Explain changes from a user's perspective, grouping them into the required
   sections. For an empty section, write `None identified.` rather than
   inventing an entry.
5. Write plain Markdown following the output structure. The apply command
   passes it to `jj describe -r {{ rev }} --stdin`.

## Rules

- Do not modify files, create commits, or otherwise change repository history;
  only replace the description of the explicitly selected `jj` change.
- Do not claim changes outside the available diff or infer an unsupported
  release boundary.
- Do not run the build system, checks, linters, tests or CI.
- Output only the changelog.
