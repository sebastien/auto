### Refinement

The task has already been elaborated, and you are refining it given the plan
state below and the reason for the refinement:

- Address the reason first: it is why you are running.
- Keep tasks that succeeded. Do not remove or rework them unless the reason
  requires it.
- A task that failed may be retried as is (do nothing), reworked (a new task
  with `replaces`), or decomposed further (`hints: {elaborate: later}` on
  its rework). Choose based on its failure: a `system` or `budget` failure
  usually calls for a retry or more budget, an `input` or `output` failure for
  a rework.
- Tasks without runs may be removed (`## remove: {id}`) or reworked freely.
- Do not raise gaps that are already open; reference them in `needed_by`
  reasoning instead.
- Use the outputs of succeeded tasks: they are facts, not proposals.
