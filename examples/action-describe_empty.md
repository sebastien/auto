---
title: Describe empty changes
description: Write a description for every mutable jj change that has none
# One run per change without a description, by change id, as describing a
# change rewrites its descendants (and their commit ids).
each:
  rev: "!jj log --no-graph -r 'mutable() & description(exact:\"\") & ~empty()' -T 'change_id.short() ++ \"\\n\"'"
params:
  split: false
pre:
  # Skip changes described since the list was made.
  - r={{ rev }}; test -z "$(jj log --no-graph -r "$r" -T description)"
post:
  # The message is on stdin: require the expected first line.
  - "sed -n '1p' | grep -qE '^\\[[A-Za-z]+\\] ' || { echo 'Expected the first line to start with a type such as [Update] followed by a space' >&2; exit 1; }"
apply:
  - jj describe -r {{ rev }} --stdin
---
@./action-summarize_changes.md
