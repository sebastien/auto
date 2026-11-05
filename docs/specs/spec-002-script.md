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

Output: the contract for what the script produces, as text or JSON.
- `format`: the output format (`markdown`, `text` or `json`)
- `schema`: the structure the output must conform to (eg. JSON Schema)
- `template`: an outline the output follows (eg. markdown headings and sections)

Schemas and templates are Values: given as text, or referenced by `PATH` so
they can be stored as standalone artifacts and shared across scripts.

Template: templated text that supports expansion, transclusion, repetition and
conditionals.

Execution: running a script is ephemeral, like running a shell script. It
streams events and ends with a status and an output, without storage or
configuration (see [Goal](spec-000-goal.md)). Persistence starts with task runs
(see [Task](spec-003-task.md)).

## Format

### Templates

Script templates are defined by:
- Content in *markdown* format with extensions
- Frontmatter in YAML
- Template in Liquid syntax

Template syntax:

- Expansion: `{{ VARIABLE }}`
- Conditional: `{% if CONDITION %}CONTENT{% elif CONDITION %}CONTENT{% else %}CONTENT{% endif %}`
- Repetition: `{% for ITEM in ITEMS %}CONTENT{% endfor %}`
- Transclusion:
  - File: `{{@PATH}}` inline, or `@PATH` alone on a line
  - Shell: `{{!SHELL}}` inline, or `!SHELL` alone on a line (excluding `![`)

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
- Starts with `./`, `../` or `/` for a local path, or `{scheme}:` for a scheme
  provided by the runtime (like `git:{operation}`, or `memo:{ref}` for a Memo
  entry). This disambiguates `@PATH` from mentions.
- Is double-quoted when it contains whitespace, like `"./path with spaces"`.
- Supports a suffix, based on the runtime capabilities:
  - `#L{START}-{END}` for a range of lines
  - `#{SYMBOL}` for a given symbol (format specific, decided by the runtime)

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
| `isolated` | config | boolean | Isolate ambient context where supported; default `true` |
| `inputs.*` | input | Value | Input data |
| `params.*` | param | Value | Behaviour parameters |
| `output.format` | output | string | Output format; default `markdown` |
| `output.schema` | output | Value | Schema the output must conform to |
| `output.template` | output | Value | Outline the output follows |

## Operations

- `ScriptExpand(context,script)`: renders the script as a prompt without
  running it through a model.
- `ScriptStart(context,script)`: runs the script through the model, streaming
  its events. Interrupting the stream cancels the execution.
- `ScriptInputs(context,script)`: determines the inputs (including transitive)
  that would be used by the script.

Events, streamed by `ScriptStart`, can be consumed and relayed through the CLI:
- `ScriptMeta(name,value)`: meta information emitted by the runtime (model,
  cost, tokens, etc).
- `ScriptAction(name,content)`: internal action logged.
- `ScriptMessage(content)`: message output by the model (for information).
- `ScriptQuestion(name,content)`: question raised by the model, answered live
  by the consumer (eg. a CLI prompt). Scripts rarely need questions.
- `ScriptEnded(status,output)`: ends the stream, with status `Succeeded`,
  `Failed`, `TimedOut` or `Cancelled`.

## Rules

- *Composition*: a transcluded markdown file with frontmatter is a script. Its expanded
  content is inlined (it does not run separately), and it inherits the
  configuration and parameters of the current context, by decreasing precedence:
  1. Explicit values in the current context
  2. Explicit values in the transcluded script
  3. Defaults in the current context
  4. Defaults in the transcluded script
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
- Persistence of executions (see Task runs)
- Effects of an execution (eg. file system changes), captured by Planning and Orchestration
- Budget allocation (see Planning), tracking across task runs, retries and sequencing (see Orchestration)
- Shell permission policy (defined by the runtime)
- Learning capture and storage (see Learning)

## References

- [mdflow](https://github.com/johnlindquist/mdflow) was a source of inspiration,
  offering the baseline concepts (frontmatter, templating, markup for inclusion).
