```
<BANNER: ASCII art of the project name, added by the author>
```

*Auto* is a command-line tool for automating work with coding agents. It runs
Markdown prompts as scripts, turns a goal into a plan of tasks, runs them with
the agent you already use, asks you when it needs a decision, and learns from
what happened.

```
GOAL → PLAN → TASKS → OUTPUTS → LEARNING
         ↑                         │
         └──── refines the plan ───┘
```

We built Auto for continuity in automation. The way in is a Markdown script:
a prompt with inputs that you run like a command. The same format then grows
into reusable tasks, plans that chain them, and memory agents that turn what
happened into learnings for the next plan, so a workflow can improve as you
use it rather than being rewritten. That is the goal; Auto is young and still
proving it. If you only need to run Markdown prompts, a lighter tool such as
[mdflow](https://github.com/johnlindquist/mdflow) will do.

## Get started

You need Python 3.14+, [uv](https://docs.astral.sh/uv/), `git-deps` and one
agent engine: Pi (the default), OpenCode V2, Vercel's
[fx](https://fx.sh) or the [llm](https://llm.datasette.io) CLI.

```sh
git-deps checkout --missing             # fetches Memo into deps/memo
uv sync --extra mcp --extra interactive
bin/auto init                           # optional: pick an installed engine
```

Auto stores plans with [Memo](https://github.com/sebastien/memo), which is not
the unrelated `memo` packages on PyPI. `git-deps` checks out the pinned
revision and uv installs it from there, so install Auto from this checkout:
built wheels name `memo` without its source and may resolve the wrong
package. The OpenCode engine also needs Node.js 22+ and
`npm ci --prefix src/py/auto/integration/opencode`.

## In a nutshell

Start with a script, a Markdown prompt with inputs:

```sh
$ cat hello.md
---
inputs:
  name: world
---
Greet {{ name }} in one sentence.
$ auto ./hello.md --input name=reader
```

When the work needs several steps, give Auto a goal. It plans, shows you the
plan, runs it once you agree, asks you when a task needs an answer, and ends
with a receipt:

```
$ auto "Document the project architecture"
  › Planning
Plan pln-000-document-project-architecture  Document the project architecture
   1 tsk-survey-modules    Map packages and their responsibilities
   2 tsk-draft-overview    Write the overview  ← tsk-survey-modules
   3 tsk-review            Review the document  ← tsk-draft-overview
Run it? [Y]es / [c]hange / [n]o ›
● tsk-survey-modules
✓ tsk-survey-modules  41s · $0.08
● tsk-draft-overview
Which diagram format should the overview use?
Answer › mermaid
✓ tsk-draft-overview  1m50s · $0.31
● tsk-review
✓ tsk-review  38s · $0.07

Done · 3/3 tasks · 3m09s · $0.46 · 1 decision
Results:
  ✓ tsk-survey-modules  Six packages: app, features, integration, runtime, utils…
  ✓ tsk-draft-overview  Wrote docs/architecture.md
  ✓ tsk-review          The overview matches the code.
Changed 2 files:
  + docs/architecture.md
  ~ README.md
Decided 1 question:
  ✓ Which diagram format should the overview use?  → mermaid
Usage:
  pi openai/gpt-6-luna          12.3k tokens  ≈<$0.01
  pi anthropic/claude-opus-5-5   140k tokens  $0.46
  total                          152k tokens  $0.46
  by stage: planning 31k tokens 8 turns $0.09 · tasks 121k tokens 22 turns $0.37
Details: auto show tsk-survey-modules · auto log --level debug · auto x usage --where plan=pln-000-document-project-architecture --by kind,model
Next: auto change "…" to refine it, or auto log to review it
```

Choose `c` to say what should change before anything runs, or `n` to keep
the plan for later. The receipt lists the files changed (in a git project),
the questions answered, and the tokens and cost per model (`≈` marks an
estimate from a price table). Most goals need no plan: a quick model first
decides whether the goal can run directly as one task, and Auto then
proposes just that.

Plans live in `.auto/` at the project root. The one you created or selected
last is the *current plan*, and the everyday commands act on it:

| Command | What it does |
|---|---|
| `auto "GOAL"` | Plan a goal, confirm it, run it and summarise |
| `auto` | Where the current plan stands and what needs you |
| `auto go` | Continue the plan until it ends or needs you |
| `auto answer` | Answer the open questions |
| `auto change "WHAT"` | Change the plan, or one task with `--task ID` |
| `auto show TASK` | A task, its latest attempt and its output |
| `auto run FILE.md` | Run a script without a plan (or `auto FILE.md`) |

Every view ends with the command you most likely want next. `auto help`
lists the rest, and `auto help "QUESTION"` asks Auto about itself.

## Features

- *Markdown scripts*: prompts with inputs, runnable on their own, kept with
  their output under `.auto/runs/`, and able to specialise catalogue
  templates such as `task/audit`.
- *Plans with contracts*: tasks declare dependencies and output checks; the
  plan is refined as tasks complete.
- *Questions and decisions*: a task that lacks something asks; your answer is
  kept as a decision, with its rationale, for the tasks that follow.
- *Policy*: approving a plan approves the effects its tasks declare; denied
  effects still need a decision.
- *Memory agents*: personas that observe runs and consolidate learnings in
  Memo for later plans.
- *Several engines*: Pi, OpenCode, fx or llm, per project, per role
  (planning, tasks, checks…) or per script.
- *Usage receipts*: tokens and cost per run in `.auto/usage.jsonl`, with
  estimates where the engine reports none.

## Design

Auto drives agents rather than being one: every prompt runs through an
external engine, and Auto owns what lies around it, namely the plan, the
contracts, the questions, the policy and the record of what happened. The
main decisions:

- *Defaults you can override*: like Rails, Auto ships opinions (planning
  prompts, task templates, profiles, policy) so it works without setup. Any
  of them can be overridden in your own or the project's catalogue
  (`~/.config/auto/catalogue`, `.auto/catalogue`) or configuration, and
  memory agents adjust plans to how you work as you keep using Auto. The
  price is that the defaults shape results until you change them, and
  learning only helps once there is history to learn from.
- *Markdown all the way*: scripts, tasks, templates and profiles are Markdown
  with front matter, so you read, diff and edit them like any document. The
  price is that contracts are checked by checks and agents, not by a type
  system.
- *Bring your own engine*: you keep the harness, models and subscriptions you
  already have. Engines differ, though: fx does not enforce Auto's effect
  policies, fx and llm report partial usage, and OpenCode always adds your
  global instructions.
- *Plans are Memo documents*: task definitions, questions and decisions in
  `.auto/` can be read with `memo -m .auto`, beside the runs and journals.
  Auto therefore depends on Memo, pinned through `git-deps` rather than
  published on PyPI.
- *Approve once, decide when needed*: agreeing to a plan approves what its
  tasks declare, so a run interrupts you only for real questions. You trust
  the declarations you approved.
- *Triage before planning*: a cheap, quick model (the `decision` profile, Pi
  with `openai/gpt-6-luna`) routes most goals to a single task. When that
  model is unavailable, every goal is planned.
- *No daemon*: learning consolidates as housekeeping after a command
  succeeds, so nothing happens while you are not running Auto.

Agent runs cost money. The test suite uses deterministic engine fixtures and
makes no paid calls:

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

## References

- [Components of agent memory](https://www.anthropic.com/research/components-of-agent-memory):
  the model behind memory agents' observations and learnings.

Related projects, compared to *Auto*:

- [mdflow](https://github.com/johnlindquist/mdflow): executable Markdown
  prompts on the agent CLI you already use. It inspired Auto's scripts (front
  matter, templating, inclusion) and is the better pick for prompts alone. In
  our view it has since grown features that dilute that purpose, and it has
  no plans, decisions or learning.
- [agenttasks.dev](https://agenttasks.dev/): a
  [task file format](https://agenttasks.dev/docs/spec/task-file) for tasks
  given to agents. It specifies tasks but does not run them; Auto's tasks add
  contracts and run inside plans.
- [GitHub Spec Kit](https://github.github.com/spec-kit/index.html) and
  [OpenSpec](https://openspec.dev/): methodologies that expand a prompt into
  specs, design and tasks. Spec Kit's workflow can be configured but not
  replaced. Auto starts from its own defaults instead: you can override them
  or express such a methodology as a `methodology` framework file, and
  learning adjusts them to your use over time.
- [Memo](https://github.com/sebastien/memo): the knowledge store Auto keeps
  plans, decisions and learnings in, and whose conventions its catalogue
  follows.

## Where to go next

- [docs/ref-cli.md](docs/ref-cli.md): every command, option and exit status,
  scripts and templates, internals and consolidation
- [docs/ref-configuration.md](docs/ref-configuration.md): configuration
  files, engines, usage and prices, memory agents
- [docs/ref-glossary.md](docs/ref-glossary.md): the vocabulary
- [docs/specs/](docs/specs): the contracts, from goal to memorisation
- [docs/implementation.md](docs/implementation.md): implementation notes
