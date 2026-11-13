# TODO

Core execution and planning are implemented; specification coverage, validation
and hardening remain.

## Priorities

- [ ] Complete the remaining conformance work tracked in [TODO_SPECS.md](TODO_SPECS.md).
- [ ] Review specs 000–007 systematically against the implementation. Passing tests do not establish complete conformance.
  - [x] Recognise canonical same-plan task references during dependency discovery and cycle validation (specs 003–004).
  - [x] Record decisions without gaps through the operation and CLI, repair missing journal entries, preserve gap concerns, and allow records on ended plans (specs 004–005, 007).
- [ ] Test real Pi, OpenCode and LLM executions, especially OpenCode questions/resume and cancellation.

## Draft specs

- [ ] Finish reviewing [Plan Policy](docs/specs/spec-008-plan-policy.md). Layered rules, grants, checkpoints and permission mapping are implemented; reassessment enforcement and planning grant answers now have regressions.
- [ ] Finish reviewing and hardening [Orchestration](docs/specs/spec-009-orchestration.md). Core continuation, holds, event repair and refinement hints are implemented with regression coverage.

## Hardening

- [ ] Verify budget behaviour for nested allocations, retained siblings, retries and concurrent runs against plan-wide limits.
- [ ] Stress-test recovery from process crashes, concurrent writers and successful Elaborators interrupted before application.
- [ ] Improve installation of the separate Memo dependency and its retention across environment syncs.
- [ ] Complete the final code review and commit the implementation when approved.

## Specified work still to implement

- Live-provider wake/cache validation.
- Memorisation crash/cancellation and live-provider hardening (spec 010).
  Explicit agent-scoped runs, dry validation, staged application and retained
  Memo recovery, decision cursors and allowance reservations are implemented and
  tested. Triggers and invocation-time draining are also implemented; wider
  crash, Memo-lock contention and provider checks remain.

Automatic retries, cross-plan scheduling, parallel task execution and workflow
engines are outside the current orchestration increment's specification.
Capture behavior for particular external interfaces belongs to their own
specifications and is outside spec 010's core memorisation scope.
