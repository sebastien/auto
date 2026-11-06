### Aspect: allocation

Decide what runs each task and with what budget:

- Pick a `profile` from the available framework based on what the task
  demands (reasoning depth, code editing, speed). Prefer profiles over
  explicit models.
- Override `engine`, `model` or `agent` only when the task has a specific
  need that no profile covers, and say why in the rationale.
- Give each task a `budget` (`tokens`, `cost`, `time`), sized to the work:
  exploration and design tasks need more than mechanical ones, and tasks with
  checks must budget for them.
- The task budgets must fit within the budget available to the elaboration.
  Keep a margin for retries and refinements, rather than allocating it all.
