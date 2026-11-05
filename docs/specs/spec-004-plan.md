# Plan

## Purpose

We want to define how tasks can be organised in a plan. While upfront planning
based on a goal is possible in theory, in practice the initial plan needs to
be refined, and sometimes even changed based on discoveries during task
execution. Our approach to planning, which is the elaboration of tasks and
their sequencing based on dependencies needs to take the following into
consideration:

- *Readiness*: determine if a task is ready to be executed/run, which depends
  on its input availability and pre condition validity.
- *Budget*: tasks can be allocated (or draw from) a token, time, or monetary
   budget
- *Failure*: tasks can fail, from system or tool errors, from bad input, bad
   output or from running out of budget.
- *Recovery*: in case of a task failure, the task may be retried, or may
   be aborted and then reworked, leading potentially to a replanning.
- *Learnings*: execution and failures lead to findings, typically surprises,
   recurrent prompts from the users, unforeseen edge cases and exceptions,
   or discoveries of patterns and concepts. These learnings need to be
   persisted and reviewed, used to inform both planning and task execution.
