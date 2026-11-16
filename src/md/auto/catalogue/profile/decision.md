---
kind: profile
identity: auto:catalogue/profile/decision
title: Decision
description: >-
  Quick, cheap judgements on small inputs: routing a goal, checking an output
  against criteria. One turn, no tools, a fast model. Override it in the
  project catalogue (.auto/catalogue/profile/decision.md) to choose another
  engine or model.
engine: pi
provider: openai
model: gpt-6-luna
budget:
  tokens: 20000
  time: 2m
---
# Decision

Runs on Pi, which adds little to the prompt, with OpenAI's gpt-6-luna.
When it is unavailable, triage falls back to planning. Override the profile
to use another configured provider, such as `openai-codex`.
