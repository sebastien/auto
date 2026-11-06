### Aspect: contract

Define what each task produces and how it is checked:

- When an artifact type in the available framework matches the output of
  a task, use it (`artifact: {identity}`): the task inherits its output
  format, template and checks. Override `output.*` only for what differs.
- Otherwise, give `output.format` (`markdown`, `text` or `json`) and, when
  the shape matters to the tasks consuming it, an `output.template` or
  `output.schema`.
- Add a `### Post` section when success can be checked: prefer shell checks
  (`!?COMMAND`, passing on exit status `0`) for anything deterministic, such
  as tests, linters or schema validation, and instructions for the rest.
- Post decides pass or fail. Do not put quality criteria in Post; they belong
  to Evaluation.
