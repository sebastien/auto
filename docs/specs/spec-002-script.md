# Agent Script

## Purpose

We want to define a markdown-based format that can be used to create scripts
in text form that can be run by agents. To be useful, these scripts need to
support:

- *Configuration*: defining the runtime/execution parameters
- *Inputs*: can be given specific inputs, files, data, text
- *Parameters*: defining variants and options for the processing of the inputs
- *Logic*: support logic operations based on parameters (including inputs)
- *Composable*: support including assets/files, but also including sub-scripts
  with context inheritance.
- *Lifecycle*: guard, check and apply the output of a run with shell commands,
  and run once per item of a list.

## Conceptual Model

Script: templated text decorated with configuration, parameters and inputs.
- `configuration`: runtime/execution settings (see Frontmatter)
- `parameters`: a named map of parameters, matching a schema
- `inputs`: an arbitrary map of inputs
- `output`: the expected output (see Output)
- `content`: templated text (see Template)

Input: data given to the script.
- `name`: a string
- `value`: current value
- `default`: default value

Inputs can be explicit (frontmatter), or implicit when referenced in the template.

Parameter: script-specific option to specify variations on input processing.
- `name`: based on the parameters schema
- `value`: current value
- `default`: default value

Value: one of
- *Literal*: native YAML data
- *File*: a `PATH` resolved by the runtime
- *Shell*: a command whose output is the value

Values are written in YAML, and a string is interpreted by its form:
- `!SHELL`: a Shell value, its value being the command stdout as text, without
  the trailing newline. YAML requires quoting it (eg. `"!git log -5"`), as an
  unquoted `!` starts a YAML tag.
- `PATH` form (`./`, `../`, `/` or `{scheme}:`): a File value, its value being
  the content of the file or reference.
- `\` prefix: a Literal, with the `\` removed (eg. `'\./not-a-path'`).
- Anything else, and non-string data: a Literal.
- `~` (null) as an input default: a *required* input. A run without a value
  for it fails before the engine is started; interactive sessions ask for it.

Only top-level values are resolved: the items of a list or mapping are
Literals, so a list of `PATH`s is passed as references (eg. for the agent to
explore) rather than as content.

Output: the contract for what the script produces, as text or JSON.
- `format`: the output format (`markdown`, `text` or `json`)
- `schema`: the structure the output must conform to (eg. JSON Schema)
- `template`: an outline the output follows (eg. markdown headings and sections)

Schemas and templates are Values: given as text, or referenced by `PATH` so
they can be stored as standalone artifacts and shared across scripts.

Template: templated text that supports expansion, transclusion, repetition and
conditionals.

Lifecycle: shell commands around the run of the template, given in the
frontmatter (see Lifecycle).
- `each`: the items to run the script for, one run per item
- `pre`: commands that must pass for the run to happen
- `post`: commands that must pass for the output to be accepted
- `apply`: commands applying the accepted output (eg. saving it)

The frontmatter holds the parameterisable parts of a script, the body its
template.

Template inheritance: a script may specialise a template with `from`, taking
its configuration, output contract and lifecycle, and filling its inputs with
its own sections (see Templates).

Execution: running a script streams events and ends with a status and an
output, without configuration (see [Goal](spec-000-goal.md)). `ScriptStart`
is ephemeral, like running a shell script; `ScriptRun`, used by `auto run`,
also records the run, so that its output can be found later (see Recorded
runs). Task runs add contracts and plans (see [Task](spec-003-task.md)).

## Format

### Templates

Script templates are defined by:
- Content in *markdown* format with extensions
- Frontmatter in YAML
- Template in [Liquid](https://shopify.github.io/liquid/) syntax, including
  its standard operators (eg. `contains`) and filters (eg. `size`, `join`)

Template syntax:

- Expansion: `{{ VARIABLE }}`
- Conditional: `{% if CONDITION %}CONTENT{% elif CONDITION %}CONTENT{% else %}CONTENT{% endif %}`
- Repetition: `{% for ITEM in ITEMS %}CONTENT{% endfor %}`
- Transclusion:
  - File: `{{@PATH}}` inline, or `@PATH` alone on a line
  - Shell: `{{!SHELL}}` inline, or `!SHELL` alone on a line (excluding `![`,
    and `!?`, reserved for shell checks; see [Task](spec-003-task.md))

### Transclusion

Transclusion markers (`@`, `!`) are distinct from Liquid expressions and are
resolved by the template engine during expansion. How content is inserted
depends on its source:

| Source | Insertion | Expanded |
|---|---|---|
| Markdown file | Inlined as-is | Yes, in the current context |
| Markdown file with frontmatter | Inlined as a sub-script (see Composition) | Yes, with inherited context |
| Other file | Fenced block, language inferred from the file type | No, verbatim |
| Shell command | Fenced block | No, verbatim |

- The single-line form (`@PATH`, `!SHELL`) applies the insertion above; the
  inline form (`{{@PATH}}`, `{{!SHELL}}`) inserts the content verbatim, for
  short values within a line.
- Fences are longer than any backtick sequence in the content.
- A path that cannot be resolved, or a shell command that fails, fails the
  expansion.

`PATH`:
- Starts with `./`, `../` or `/` for a local path, relative paths resolving
  against the directory of the file containing them, or `{scheme}:` for a scheme
  provided by the runtime (like `git:{operation}`, or `memo:{ref}` for a Memo
  entry). Planning adds `task:`, `run:`, `revision:`, `gap:`, `decision:`,
  `plan:` and `auto:`
  (see [Plan references](spec-004-plan.md)). This disambiguates `@PATH` from
  mentions.
- Is double-quoted when it contains whitespace, like `"./path with spaces"`.
- Supports a suffix, based on the runtime capabilities:
  - `#L{START}-{END}` for a range of lines
  - `#{SYMBOL}` for a given symbol (format specific, decided by the runtime)
  - `#{SECTION}` for a markdown file: the body of the one heading named
    `SECTION`, up to the next heading of the same or a higher level. The
    frontmatter is kept, so a transcluded script keeps its defaults (eg.
    `@auto:catalogue/task/audit#Process`)

### Frontmatter

| Property | Kind | Type | Purpose |
|---|---|---|---|
| `title` | meta | string | Human-readable single line title |
| `description` | meta | string | Human-readable summary |
| `engine` | config | string | Integration running the script (eg. `opencode`, `pi`, `llm`) |
| `model` | config | string | Model identifier or alias |
| `agent` | config | string | Name of a project-local agent, where supported |
| `temperature` | config | number | Generation settings, where supported |
| `cwd`, `env.*` | config | string | Working directory and environment overrides |
| `prompt` | config | string | Replace system instructions |
| `isolated` | config | boolean | Isolate ambient context where supported; default `false` |
| `tools` | config | list | Allowlist of the engine's tool names; `[]` disables all tools. Absent: the engine's own tools. An engine that cannot apply it fails the run |
| `access` | config | string | `read` or `write` (default): what the run may do to the project. `read` is enforced where the engine can (Pi: read-only tools unless `tools` is given; OpenCode: file edits denied; LLM: no tools), and warned about otherwise. Recorded runs report the files a `read` run changed |
| `from` | template | string | Template the script specialises: a `PATH`, or `{kind}/{name}` for `auto:catalogue/{kind}/{name}` (see Templates) |
| `inputs.*` | input | Value | Input data |
| `params.*` | param | Value | Behaviour parameters |
| `output.format` | output | string | Output format; default `markdown` |
| `output.schema` | output | Value | Schema the output must conform to |
| `output.template` | output | Value | Outline the output follows |
| `each.{NAME}` | lifecycle | Value | Items to run the script for, bound to the input `NAME` |
| `pre` | lifecycle | Command(s) | Checks that must pass before the run |
| `post` | lifecycle | Command(s) | Checks that must pass on the output |
| `transform` | lifecycle | Command(s) | Replace output with command stdout after checks |
| `apply` | lifecycle | Command(s) | Effects applying the accepted output |

### Lifecycle

```markdown
---
title: Describe empty changes
each:
  rev: "!jj log --no-graph -r 'description(exact:\"\")' -T 'change_id ++ \"\\n\"'"
pre:
  - r={{ rev }}; test -z "$(jj log --no-graph -r "$r" -T description)"
post:
  - grep -qE '^\[[A-Za-z]+\] '
apply:
  - jj describe -r {{ rev }} --stdin
---
@./summarize_changes.md
```

- *Commands*: `pre`, `post`, `transform` and `apply` are a command or a list of commands.
  As list items, they are not resolved as Values: they run at their step, as
  shell commands without the `!` prefix.
- *Variables*: commands use the same template variables as the body, plus
  `output` (the current output data) in `post`, `transform` and `apply`. Substitutions are quoted as
  shell data, as for shell transclusions.
- *Output*: `post`, `transform` and `apply` commands receive the current output on stdin, as text,
  or as JSON for `json` outputs.
- *Transform*: successful command stdout replaces the output, parsed using the
  effective `output.format`. For JSON, stdout must be one strict JSON document;
  invalid JSON, duplicate keys, a nonzero exit or oversized output fails the run
  without returning raw or partially transformed output. Commands in a list form
  a pipeline; later commands and `apply` receive the latest value. The original
  `output.schema` validates the model response, not the transformed result. Any
  additional result-schema checks belong in the transformation command. `post`
  and `apply` stdout still do not replace output. The text CLI buffers model text
  when transformations are declared and prints only the final result.
- *Each*: `each` has a single key, the name of the input bound to each item.
  Its Value is a list, or text with one item per non-empty line (eg. a Shell
  value). Items are Literals: they are not resolved as `PATH` or Shell values.

### Templates

A script giving `from` specialises a template: a script or task file, often
from the catalogue (see [Elaboration](spec-006-elaboration.md#catalogue)).

```markdown
---
from: task/audit
inputs:
  facet: documentation structure
---
Keep `docs/` focussed on specifications, references and use cases.

## Scope

- docs/

## Criteria

- DOC-01: `docs/` only holds specifications, references and use cases
```

- *Content*: the template's `## Process` section when it has one (a task
  template), else its body.
- *Metadata*: the template's metadata, except its catalogue identity (`kind`,
  `identity`), then the script's, which takes precedence key by key (and
  within `inputs`, `params` and `output`). Lifecycle commands are inherited,
  unlike with transclusion. Relative references keep their origin: the
  template's resolve from the template, the script's from the script.
- *Sections*: each `##` section of the script whose heading names a template
  input (lowercased, other characters as `_`, eg. `## Out of scope` is
  `out_of_scope`) gives that input, as a Literal. A section given for a list
  input gives its bullet items, or its text as one item. Giving an input both
  as a section and in the frontmatter is an error.
- *Brief*: the rest of the body (text before the first section, and the
  sections naming no input) gives the `brief` input when the template
  declares one, with its sections nested as `####`; otherwise it follows the
  template's content.
- *Artifact*: an `artifact` gives the output contract (`output`), below the
  explicit values. A recorded run also runs the shell checks (`!?`) of the
  artifact's Post on the stored output (see Recorded runs); its other checks
  are task checks.
- Templates may themselves give `from`. A cycle fails the expansion.

### Recorded runs

`ScriptRun` runs a script like `ScriptStart` and records it under the
project's `.auto/runs/{run}/`, beside the plans:

```
.auto/runs/{run}/
  receipt.json      # Status, script, template, match key, usage, warnings, changes
  journal.jsonl     # The script events
  output.{md,txt,json}
```

- The terminal `ScriptEnded` event names the run (`run`) and its output file
  (`outputPath`); the CLI shows both, with the command reading the output.
- *Checks*: when the script inherits an `artifact`, the shell checks of the
  artifact's Post run on the stored output after the run succeeds, with
  `run.output` its path. A failing check fails the run, keeping its output.
- *Baseline*: when the template declares a `baseline` input left empty, it is
  given the output of the latest succeeded run with the same match key: the
  template, and the `facet` and `scope` inputs when given. The receipt names
  that run in `baseline`, and a `ScriptMeta(baseline)` event announces it.
- *Access*: a `read` run snapshots the git working tree before running, and
  reports the files it changed (outside `.auto/`) in a warning and in the
  receipt's `changes`, without failing the run.
- A run missing a required input is rejected before it is recorded.
- *References*: `auto:runs/{id}` resolves to a run's output once it has
  ended, and `auto:runs/{id}/receipt` and `auto:runs/{id}/journal` to those
  files, so that one run's output can be another's input (eg.
  `audit: auto:runs/{id}`). The CLI offers recent succeeded runs when asking
  for a missing input, those whose artifact is named like the input first.

## Operations

- `ScriptExpand(context,script)`: renders the script as a prompt without
  running it through a model.
- `ScriptStart(context,script)`: runs the script through the model, streaming
  its events. Interrupting the stream cancels the execution.
- `ScriptRun(context,script)`: runs the script as `ScriptStart` does, and
  records it (see Recorded runs). Interrupting it records a `Cancelled` run.
- `ScriptList(context,filter?)`: lists recorded runs, newest first.
- `ScriptOutput(context,record)`: reads the stored output of a recorded run.
- `ScriptInputs(context,script)`: determines the inputs (including transitive)
  that would be used by the script.

Events, streamed by `ScriptStart`, can be consumed and relayed through the CLI:
- `ScriptMeta(name,value)`: meta information emitted by the runtime (model,
  cost, tokens, etc).
- `ScriptAction(name,content,access?,files?,detail?,error?)`: internal
  action logged. For an engine tool call, `content` is its status
  (`running`, `completed`, `error`), reported once per status; `access` is
  `read`, `write` or `use` by tool name; `files` the paths its input names;
  `detail` a short command, pattern or address (at most 200 characters); and
  `error` the failure message. Tool inputs and results are never copied.
- `ScriptMessage(content)`: message output by the model (for information).
- `ScriptQuestion(name,content)`: question raised by the model, answered live
  by the consumer (eg. a CLI prompt). Scripts rarely need questions.
- `ScriptItemStarted(name,value,index,count)`: starts the run of an `each`
  item, followed by its events.
- `ScriptItemEnded(name,value,index,status,output,failure?)`: ends the run of
  an `each` item.
- `ScriptEnded(status,output,failure?)`: ends the stream, with status
  `Succeeded`, `Failed`, `TimedOut`, `Cancelled` or `Skipped` (`pre` failed).
  With `each`, the output is the list of item results
  (`[{name,value,status,output,failure?}]`).

Lifecycle commands are logged as `ScriptAction(step,command)`, with `step`
being `pre`, `post`, `transform` or `apply`.

## Rules

- *Composition*: a transcluded markdown file with frontmatter is a script. Its expanded
  content is inlined (it does not run separately), and it inherits the
  configuration and parameters of the current context, by decreasing precedence:
  1. Explicit values in the current context
  2. Explicit values in the transcluded script
  3. Defaults in the current context
  4. Defaults in the transcluded script

  Lifecycle keys are not inherited: the `pre`, `post`, `transform` and `apply` of a
  transcluded script are ignored, and a transcluded script with `each` fails
  the expansion.
- *Lifecycle*: `ScriptStart` runs the following steps for each item (or once
  without `each`), stopping at the first failure:
  1. *Pre*: a failing command (non-zero exit status) ends the run as `Skipped`
  2. *Run*: the template is run through the model, giving the output
  3. *Post*: a failing command ends the run as `Failed` (`output` failure)
   4. *Transform*: replace the output with command stdout (`output` failure on error)
   5. *Apply*: a failing command ends the run as `Failed` (`system` failure)

  Items run in order. With `each`, the execution is `Succeeded` when every
  item is `Succeeded` or `Skipped`, `Failed` otherwise, and stops at the first
  item `TimedOut` or `Cancelled`. The `each` Value is resolved once, before
  the first item; inputs are resolved once per item, and shared by its steps.
- *Output*: the output contract is included in the prompt given to the model.
  The execution fails when the output does not match `output.format` or
  `output.schema`. Conformance is checked here; quality assessment belongs to
  Planning.
- *Shell*: shell commands run in `cwd` with `env`, subject to the runtime
  permission policy.
- *Limits*: the runtime ends an execution that exceeds its limits, as
  `TimedOut` for time and `Failed` otherwise. Limits are given by the caller
  (eg. a task budget).

## Out of Scope

- Task contracts, dependencies and output quality assessment (see Planning)
- Persistence of executions beyond recorded runs: resumption, plans (see Task runs)
- Effects of an execution (eg. file system changes) beyond the declared
  `apply` commands, captured by Planning and Orchestration
- Persistence, resumption and parallelism of `each` items (see Task and Plan)
- Budget allocation (see Planning), tracking across task runs, retries and sequencing (see Orchestration)
- Shell permission policy (defined by the runtime, from the plan policy, see [Plan Policy](spec-008-plan-policy.md))
- Learning capture and storage (see [Memorisation](spec-010-memorisation.md))

## References

- [mdflow](https://github.com/johnlindquist/mdflow) was a source of inspiration,
  offering the baseline concepts (frontmatter, templating, markup for inclusion).
