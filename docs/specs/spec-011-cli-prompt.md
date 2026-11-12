# CLI Prompt

## Purpose

We want the CLI to be self-discoverable: a human should be able to type any
incomplete command and be guided to a complete, valid invocation without
reading the reference. The guiding principle:

> **Ask, don't fail.** When a human is present and a command is incomplete,
> the CLI asks for what is missing instead of failing, and teaches the full
> command as it goes.

Interaction needs to be:

- *Additive*: the non-interactive CLI (see [Interface](spec-007-interface.md))
  is unchanged; interaction only fills gaps that would otherwise be usage
  errors
- *Discoverable*: every prompt states the need, offers candidates, and shows
  the equivalent full command, so that users graduate to typing it directly
- *Derived*: prompts are generated from operation metadata, like the help and
  the MCP schemas, never hand-written per command
- *Safe*: nothing runs until every argument is gathered and, for operations
  with effects, confirmed; cancelling has no effect
- *Optional*: interaction is an optional extra; without it, the CLI is
  complete and non-interactive

## Conceptual Model

Session: an invocation is *interactive* when all of the following hold,
otherwise it is non-interactive and behaves as described in
[Interface](spec-007-interface.md), including for run questions:

- the `interactive` extra ([prompt_toolkit](https://python-prompt-toolkit.readthedocs.io/))
  is installed
- stdin and stderr are terminals, and `TERM` is not `dumb`
- `--format` is not `json`
- neither `--no-input`, `AUTO_NO_INPUT` nor `CI` is set

Command gap: what prevents a command line from being run as typed (not to be
confused with a plan Gap, see [Plan](spec-004-plan.md)):

- *Missing verb*: a noun without a verb (eg. `auto plan`) → pick a verb of
  the noun
- *Missing context*: the operation needs a plan and none resolves → pick a
  plan of the project
- *Missing argument*: a required argument is absent (eg. `auto plan create`
  without a goal)
- *Missing input*: a script uses inputs (see `ScriptInputs` in
  [Script](spec-002-script.md)) that are neither given nor declared with a
  value; each may be skipped
- *Invalid value*: a value fails validation for an argument with a known
  domain (eg. an unknown task id)

A bare `auto` is not a command gap: it prints the reference, like
`auto help`.

Argument descriptor: exposure metadata, per argument:

- `label` and `help`: what to ask, and why (default from the argument name
  and the operation need)
- `kind`: how the value is entered
  - `text`: a single line
  - `multiline`: free text (eg. a goal, a reason)
  - `path`: a file path, with an optional suffix filter (eg. `.md`)
  - `choice`: one of the candidates
  - `value`: the Value syntax (see [Script](spec-002-script.md))
  - `structured`: YAML (eg. a budget, effects)
- `candidates`: a function of the context returning `(value, description)`
  pairs, built on runtime operations (eg. the tasks of the plan, its open
  gaps and runs, catalogue entries, plans of the project)
- `ask`: whether an optional argument is asked for once another gap has been
  filled (default: only required arguments are asked for), so that a
  complete command never prompts for options

Exposure effects: whether an operation has effects (eg. `PlanCreate`,
`PlanRefine`, `TaskStart`, `GapDecide`) or is read-only (eg. `PlanStatus`,
`GapList`, `CatalogueGet`). Operations with effects are confirmed before
running.

The same descriptors provide the argument descriptions of the MCP tool
schemas and of the generated help.

## Behaviour

1. *Parse*: the command line is parsed as described in
   [Interface](spec-007-interface.md).
2. *Fill*: in an interactive session, command gaps are resolved in order:
   verb, context, required arguments, `ask` optional arguments, missing
   inputs. Each prompt shows:
   - the label, preceded by the operation need on the first prompt
   - for `choice` arguments, a picker: the candidates are listed with their
     descriptions and filtered as you type (fuzzy, space-separated terms),
     like fzf; the picker is erased once a candidate is picked, leaving
     `{label} › {value}`
   - otherwise, completion from candidates, or path completion for `path`
     arguments
   - inline validation, applying the same coercion as the operation (eg. a
     budget must parse as YAML, `since` as an integer), re-prompting with the
     entered value kept
3. *Echo*: when gaps were filled, the equivalent command is written on
   stderr, eg.
   `› auto plan create "Migrate auth to OIDC" --plan auth --budget 'cost: 2'`
4. *Confirm*: when gaps were filled for an operation with effects,
   `Run? [Y/n]` is asked; declining
   exits with `130` and nothing run. Read-only operations run directly.
5. *Run*: the operation runs exactly as if the echoed command had been typed.

In a non-interactive session, command gaps are usage errors (exit `2`, see
[Interface](spec-007-interface.md)), whose recovery names the missing
argument and, when it has candidates, lists a few of them.

*Questions*: run questions (`ScriptQuestion`) use the same prompts. A
question with fields is asked field by field, each with its kind. In a
non-interactive session, questions are handled as non-interactive (see
[Interface](spec-007-interface.md)).

## Conventions

- *Keys*: `Enter` accepts, `Tab` completes, `Esc Enter` submits multiline
  text, `Ctrl-X Ctrl-E` edits the value in `$EDITOR`. In a picker, `↑`/`↓`
  (or `Ctrl-P`/`Ctrl-N`, `Tab`/`Shift-Tab`, `PageUp`/`PageDown`) move,
  `Enter` picks, and `Esc` skips an optional pick.
- *Cancellation*: `Ctrl-C` or `Ctrl-D` at a prompt exits with `130`, nothing
  having run.
- *Streams*: prompts are written on stderr; stdout only carries results.
- *History*: entered values are remembered per argument in
  `$XDG_STATE_HOME/auto/history/`.
- *Colour*: `NO_COLOR` is honoured.

## Rules

- *Interface only*: interaction gathers arguments and adds no behaviour.
  Anything an interactive session does, a full command line does.
- *Never block*: a non-interactive session never prompts.
- *Teach*: every filled command is echoed in full before running.
- *Derived*: no command has a bespoke prompt flow; the need for one is a
  missing descriptor field.
- *No surprise*: `auto`, `auto help` and `--help` always print the reference,
  and never prompt.
- *Optional*: without the `interactive` extra, the CLI is complete; every
  session is non-interactive.

## Out of Scope

- A full-screen TUI or REPL shell: `auto` remains a one-shot command
- Interactive editing of plans or results
- Prompts in the MCP interface: agents get errors listing candidates instead

## References

- [prompt_toolkit](https://python-prompt-toolkit.readthedocs.io/) provides
  prompts, completion, validation, history and editor integration.
