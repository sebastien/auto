---
title: Set up Memo
description: Initialize Memo's project store and connect its agent to the project instructions
temperature: 0.1
inputs:
  # Agent created with its private observation log and learnings memory
  agent: Developer
  # Instructions file receiving the Memory block printed by `memo agent init`
  instructions: AGENTS.md
params:
  # Install the subagent and the four skills in the project (`project`), for
  # the current user (`user`), or not at all (`none`)
  integrations: project
output:
  format: markdown
  template: |
    ## Store
    ## Instructions
    ## Agent
    ## Integrations
    ## Verification
pre:
  - command -v memo >/dev/null || { echo 'memo is not installed; install it from the Memo repository first' >&2; exit 1; }
post:
  - grep -qx '## Memory' {{ instructions }}
  - awk '/^## Memory$/{n++} END{exit n != 1}' {{ instructions }}
  - memo agent status | grep -Fq '{{ agent }}'
---
Set up Memo in this project: initialize its `.memo` store, create an agent
with its observation log and learnings memory, and connect that agent to the
project's instructions.

Memo keeps structured knowledge and agent memory beside the code, under
`.memo`. Its agent records observations while working and consolidates
reusable learnings from them; `memo agent init` prints the `## Memory` block
that tells the primary agent how to use it.

## Context

Memo CLI:

!command -v memo

Project store:

!ls -ld .memo 2>/dev/null || echo 'no .memo store'

Project instructions:

!ls -l {{ instructions }} 2>/dev/null || echo 'no instructions file'

Memo checkout (subagent and skills):

!found=; for path in "${MEMO_REPO:-}" ../memo; do if [ -n "$path" ] && [ -f "$path/.opencode/agents/memo.md" ]; then found=$path; break; fi; done; echo "${found:-not found}"

## Method

1. Initialize the store only when it is absent: run `memo init`. An existing
   `.memo` is the project's knowledge; never reinitialize, reset or repair it.
2. Create the agent. Check `memo agent status` first, then run
   `memo agent init {{ agent }}`; rerunning it is safe and it prints the
   `## Memory` block. Keep the command output.
3. Add that Memory block to `{{ instructions }}`:
   - The block is the `## Memory` section at the end of the `memo agent init`
     output, starting with `At startup run` and `memo agent wake`. Use it
     verbatim, including its blank lines.
   - Create `{{ instructions }}` when absent, and preserve the rest of the file
     when it exists.
   - When the file already has a Memo `## Memory` section, replace only that
     section; otherwise append the block after a blank line. The heading must
     appear exactly once.
{% if params.integrations == "none" %}4. Skip the subagent and skills: integrations are disabled.
{% elsif params.integrations == "user" %}
4. Install the Memo integrations for the current user:
   - the subagent's `memo.md` as `~/.config/opencode/agents/memo.md`;
   - the four skill directories `memo`, `memo-journal`, `memo-learnings` and
     `memo-documentation` under `~/.config/opencode/skills/`, all together and
     preserving their names.
{% else %}
4. Install the Memo integrations in the project:
   - the subagent's `memo.md` as `.opencode/agents/memo.md`;
   - the four skill directories `memo`, `memo-journal`, `memo-learnings` and
     `memo-documentation` under `.opencode/skills/`, all together and
     preserving their names.
{% endif %}{% if params.integrations != "none" %}
   Copy them from the Memo checkout above. When no checkout is found, look for
   the source of the installed `memo` package; if it is still unavailable,
   report what is missing and skip this step. Never overwrite a differing
   existing subagent or skill; report the conflict instead, and never install
   only some of the four skills.
{% endif %}
5. Verify: `memo agent status` lists the agent, `memo agent wake` prints its
   guidance, and `{{ instructions }}` contains exactly one `## Memory` section.
6. Report what was created, updated, skipped or left unchanged, and the
   commands used.

## Rules

- Do not commit or publish anything: leave the store and instruction changes
  for review.
- Do not add `.memo` to ignore rules: useful entries are reviewed and committed
  with the code.
- Do not write personal preferences, secrets or task diaries to the store.
- Do not invent the Memory block, subagent or skills: they come from Memo.
- Do not initialize or install anything when the memo CLI, checkout or package
  is unavailable; report what is missing instead.
- Do not run the build system, checks, linters, tests or CI.
