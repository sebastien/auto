### Aspect: governance

Decide where quality is assessed and where humans or the plan step in:

- Add an `### Evaluation` section with criteria to tasks whose output quality
  matters downstream (designs, specs, user-facing content). Criteria should
  be specific to the task, not generic ("is clear and complete").
- Add `hints: {gate: approval}` to tasks whose output must be signed off by
  a human before dependent tasks run: typically specs, designs, and anything
  irreversible or outward-facing. Gates cost time, so use them where a wrong
  output would be expensive to undo.
- Add `hints: {refine: after}` to tasks whose output may change the rest of
  the plan (discovery, design, prototypes): the plan is refined once they
  succeed.
- Raise `decision` gaps for choices that should be made by a human up front,
  with `options` when they are known.
