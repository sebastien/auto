---
kind: task
identity: auto:catalogue/task/triage
title: Triage a goal
description: >-
  Decides whether a goal can run as a single task, directly, or needs a plan
  of several tasks, and which effects it has. Run by Auto before planning a
  guided goal, with the decision profile.
profile: auto:catalogue/profile/decision
isolated: true
tools: []
inputs:
  goal: ""
  # Project files, as listed by git, possibly truncated
  files: []
  # Effects a task may declare: name and policy rule
  effects: []
output:
  format: json
  schema:
    type: object
    additionalProperties: false
    required: [route, effects, reason]
    properties:
      route:
        enum: [direct, plan]
      effects:
        type: array
        items:
          type: string
      reason:
        type: string
---
## Process

Decide how to carry out this goal in the project below.

### Goal

{{ goal }}

### Project files

{% if files.size > 0 %}{% for file in files %}{{ file }}
{% endfor %}{% else %}No files yet.{% endif %}

### Routes

- `direct`: one agent session can do it end to end without anyone's input:
  a focused change, a single file or a few related ones, a question about
  the code, a fix with an evident cause. Most everyday goals are direct.
- `plan`: it needs several distinct steps whose results feed each other,
  work spread across many parts of the project, choices a person should make
  first, or more than about an hour of an engineer's work.

When unsure, prefer `direct`: planning costs more than it saves on a goal
that one session can finish.

### Effects

List the effects the work needs, from:

{% for effect in effects %}- `{{ effect.name }}` ({{ effect.rule }})
{% endfor %}
Reading the project needs none. Only list what the goal requires.

Answer with JSON only: `{"route": "direct" | "plan", "effects": [...],
"reason": "one short sentence"}`.
