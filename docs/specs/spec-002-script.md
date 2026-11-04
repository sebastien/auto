# Agent Script

## Purpose

We want to define a markdown-based format that can be used to create scripts
in text form that can be run by agents. To be useful, these scripts need to
support:

- *Parameters*: be configurable (provider, model, temperature, etc)
- *Inputs*:  can be given specific inputs, files, data, text
- *Logic*: support logic operations based on parameters (including inputs)
- *Composable*: support including assets/files, but also including sub-scripts
  with context inheritance.

## Conceptual Model

Script: templated text decorated with parameters and inputs.
- `parameters`: a named map of parameters, matching a schema
- `inputs`: an arbitrary map of input
- `content`: templated text (see Template)

Configuration: parameters for running the script, aimed at the runtime.
- `name`: based on the list of supported  parameters
- `value`: current value
- `default`: default value

Input: an input, specific to the task.
- `name`: a string
- `value`: current value
- `default`: the default value of the input

FileInput(Input): a file input
- `path`

ShellInput(Input): a shell input
- `shell`

Noting that inputs can be explicit (front matter), or implicit when referenced
in the templates.

Parameter: task-specific parameters to specify variations on input processing
- `name`: based on the list of supported  parameters
- `value`: current value
- `default`: default value

Value:
- Literal data
- or path: reference to a file or URI (resolved by the runtime)

Template: templated text that supports expansion, transclusion, repetition and conditionals

Runtime: is able to process a script and feed it to a model.
- `name` the name of the runtime
- `engine` the name of the runtime engine
- `models` the AI models supported by the runtime

## Format

### Templates

Script templates are defined by:
- Content in *markdown* format with extensions
- Frontmatter in YAML
- Template in LiquidJS

Template syntax:

- Expansion: `{{ VARIABLE }}`
- Conditional: `{% if CONDITION %}}CONTENT{% elif CONDITION %}CONTENT{% else %}CONTENT{% endif %}`
- Repetition: `{% for ITEM in ITEMS %}CONTENT{% endfor %}`
- Transclusion: `{{@PATH}}` for files or `@PATH` (on single line), `{{!SHELL}}`  or  `!SHELL` (single line) for shell

`PATH` can:
- Start with `./`, `../` or `/` to denote a local path
- Start with `{scheme}:` to denote a specific scheme (like `git:{operation}`) provided by the runtime
- Whitespace is double quoted like `"./path with spaces"`

Trasncluded `PATH` also supports ranges and references (based on the runtime capabilities) as a suffix:
- `:{START}-{END}` for the range in lines
- `#{SYMBOL}` for a given symbol (format specific, decided by the runtime)

### Parameters

| Property | Runtime interpretation | Purpose |
|---|---|---|
| `title` | string | Human-readable single line title |
| `description` | string | Human-readable summary |
| `engine` | string | Engine |
| `model` | string | Model identifier or alias |
| `agent` | text | Name of a project-local agent, where supported |
| `temperature` | number | Generation settings, where supported |
| `cwd`, `env.*` | text | Working directory and environment overrides |
| `prompt` | text | Replace system instructions |
| `isolated` | `true` / `false` | Isolate ambient context where supported; default `true` |
| `inputs.*` | native YAML values | Input data |
| `params.*` | native YAML values | Behavior parameters |
| `output.*` | field-specific text | Artifact template, output format, and processing |

## Operations

- `ScriptExpand(context,script)`, renders the script as a prompt without running it through a model.
- `ScriptRun(context,script)`, runs the script through the model, streaming events
- `ScriptInputs(context,script)`, determines the inputs (including transitive) that would be used by the script

Events: emitted by running the script, can be consumed and relayed through the CLI
- `ScriptMeta(name,value)`, meta information emitted by the runtime, like model, cost, tokens, etc.
- `ScriptAction(name,content)`, internal action logged
- `ScriptMessage(content)`, message output by the model (for information)
- `ScriptQuestion(name,content)`, internal action logged

## References

- [mdflow](https://github.com/johnlindquist/mdflow) was a source of inspiration,
  offering the baseline concepts (frontmatter, templating, markup for inclusion).


