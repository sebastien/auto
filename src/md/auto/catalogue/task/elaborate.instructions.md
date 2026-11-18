### Method

1. *Understand the goal*: what the task must achieve and how success is
   judged (its output contract, Post and Evaluation, and the plan's definition
   of done). If an ambiguity would change the plan's structure, raise a
   `decision` or `information` gap rather than guessing.

2. *Research only what the structure needs*. Read the context (specs,
   sources, prior outputs) inline to decide the structure; detailed
   investigation belongs to the tasks. Propose a discovery task instead,
   followed by `hints: {refine: after}`, when its findings are worth keeping
   as an artifact, or the investigation is expensive or uncertain.

3. *Decompose* into the fewest tasks that each have one clear output. A task
   needing more than a paragraph to explain what it produces is probably two.

4. *Connect*. Tasks consume plan inputs (`plan:{name}`), other tasks' outputs
   (`task:{id}`), files and catalogue entries; dependencies follow from these
   references, so a task needing another's output references it in `inputs`.
   When a catalogue template (`auto:catalogue/task/{name}`) does what a task
   needs, instantiate it with `from:` and the `inputs` it declares, rather
   than writing the task or listing the template as an input: it brings its
   own process and checks. Prefer the most specific entry: a project action
   (layer `actions`) that specialises a builtin template already holds the
   project's scope and criteria. Give every `(required)` input, and only the
   inputs and params the entry lists. To fix the findings of an audit, chain
   an audit (or an action specialising it), `auto:catalogue/task/audit-resolve`
   with `audit: task:{audit}`, then the same audit again with
   `baseline: task:{audit}` to verify the fixes. An input path (`./path`)
   must exist now; for a file a plan task will create, depend on that task (`task:{id}`) and give
   the path as plain text (eg. `dir: site`).

5. *Ground*. Reference only inputs, files and catalogue entries that exist;
   never invent them. For anything missing (an input, a fact, a choice, a
   sign-off), raise a gap of that kind (`input`, `information`, `decision`,
   `approval`) whose `needed_by` lists the tasks needing it. A gap's answer is
   given to those tasks when they run: do not reference gaps in `inputs`
   (`plan:{name}` only names plan inputs).

6. *Explain*: a one line `summary`, and a short `## Rationale` on why these
   tasks, in this order, and what was left out.

### Conventions

- Task ids are lowercase words separated by `-`, unique within the plan,
  never `root`. Auto prefixes them with `tsk-`, which you may omit:
  `build-site` and `tsk-build-site` name the same task.
- Each task has a `title`, and a `goal` when the title is not enough.
- Each task has a `### Process`, unless it uses `from` or is elaborated later.
- Write instructions for the agent running the task: what to produce, from
  what, and what matters. Do not restate the whole plan.
