### Breakdown: fine

Break the goal down into tasks that can each be run as is:

- Each task should be completable by an agent in a single run, within its
  budget, without further decomposition.
- Use `hints: {elaborate: later}` only when the breakdown of a task genuinely
  depends on outputs that do not exist yet.
- Give each task complete instructions in its `### Process` section.
