### Method

1. *Understand the goal*. Restate what the task must achieve and how success
   will be judged: its output contract, its Post and Evaluation, and the
   definition of done of the plan. If the goal is ambiguous in a way that
   changes the structure of the plan, raise a `decision` or `information` gap
   rather than guessing.

2. *Research as needed*. Read the context (specs, sources, prior outputs) to
   the extent needed to decide the structure of the plan, and no further.
   Detailed investigation belongs to the tasks themselves.

3. *Research inline or as a task*. Investigate inline what you need to decide
   the structure. Propose a discovery task instead, followed by
   `hints: {refine: after}`, when its findings are worth keeping as an
   artifact, or when the investigation is expensive or its outcome uncertain.

4. *Decompose*. Propose the fewest tasks that each have one clear output.
   A task that needs more than one paragraph of instructions to explain what
   it produces is probably two tasks.

5. *Connect*. Tasks consume plan inputs (`plan:{name}`), outputs of other
   tasks (`task:{id}`), files and catalogue entries. Dependencies follow from
   these references, so a task that needs another task's output must
   reference it in its `inputs`.
   When a catalogue task template (`auto:catalogue/task/{name}`) does what
   a task needs, instantiate it with `from: auto:catalogue/task/{name}` and
   the `inputs` it declares, instead of writing the task yourself or listing
   the template as an input: the template brings its own process and checks.
   A path given as an input (`./path`) must exist now. For a file or
   directory that a task of the plan will create, depend on that task
   (`task:{id}`) and give the path as plain text (eg. `dir: site`).

6. *Ground*. Only reference inputs, files and catalogue entries that exist.
   When something needed is missing (an input, a fact, a choice, a sign-off),
   raise a gap of the matching kind (`input`, `information`, `decision`,
   `approval`), with `needed_by` listing the tasks that need it. Never invent
   inputs, file paths or catalogue entries.
   A gap's answer is given to the tasks in its `needed_by` when they run: do
   not reference gaps in `inputs` (`plan:{name}` only names plan inputs).

7. *Explain*. Give a one line `summary`, and a short `## Rationale` explaining
   the structure: why these tasks, in this order, and what was left out.

### Conventions

- Task ids are lowercase words separated by `-`, unique within the plan, and
  never `root`.
- Each task has a `title` and, when the title is not enough, a `goal`.
- Each task has a `### Process` section, unless it uses `from` or is to be
  elaborated later.
- Write instructions for the agent that will run the task: what to produce,
  from what, and what matters. Do not restate the whole plan.
