# Task

## Purpose

We want to define the conceptual model, operations and format for a *task*.
Tasks compose *agent scripts* into elements that form a plan. Tasks are the
building blocks of planning and need to support:

- *Inputs*, which are used to produce a result/output
- *Parameters* (optional), which are use to tweak and influence the process of producing the output
- *Output Format*, expected format of the output, as a schema or instructions
- *Process*, instructions to produce the output from the input and parameters
- *Preconditions* (optional), which define if/when the task can be executed
- *Postconditions* (optional), which define if/when was successful
- *Output Evaluation* (optional), defining criteria to assess output quality

## Conceptual model

Task: composition of a script
- `id`: identifier
- `name`: label or title
- `script`: composed script, defines inputs, parameter and output format
- `process`: step-by-step instructions for producing
- `pre`: pre-condition instructions
- `post`: post-condition instructions
- `evaluation`: output evaluation instructions
- `budget.{tokens,cost}`: allocated budget, if any

Run: tracks execution of a task
- `id`: run identifier, composed of task, datetime and counter
- `status`: Running|Success|Failed|TimedOut
- `started`: start datetime
- `ended`: end datetime
- `output`: output data
- `log`: log of events from the runtime
- `runtime`: engine, provider and model
- `usage.{tokens,cost}`: record of usage

Note here that the log only needs to keep track of runtime events, each
runtime may have more granular logs (eg. full transcript)

## Format

A task is represented as a markdown file with a front matter, using
transclusion to compose the script. Consistently named second level sections
are used to denote the key parts.

```markdown
# Task Name

Optional task description

## Pre

Preconditions instructions

## Post

Postconditions instructions

## Process

Process instructions

## Output

Output production instructions

## Evaluation

Output evaluation instructions
```

Run status should be stored by the runtime as one or more artifacts covering:
- The output, typically as a text file or structured data file
- The log, as structured data or structured log file
- The receipt, as the run metadata


## Operations

Task:
- `TaskStart(task)` produces a `Run`

Run(read):
- `RunLog(run,since?)`, yields a stream of events from the log (may be running)
- `RunStatus(run)`, returns the run status
- `RunOutput(run)`, returns the output

Run(state):
- `RunSucceed(run,result)`
- `RunFail(run,failure)`
- `RunTimeout(run)`

