# TODO

Core execution and planning are implemented; specification coverage, validation
and hardening remain.

## Priorities

- [ ] Review specs 000–007 systematically against the implementation. Passing tests do not establish complete conformance.
  - [x] Recognise canonical same-plan task references during dependency discovery and cycle validation (specs 003–004).
  - [x] Record decisions without gaps through the operation and CLI, repair missing journal entries, preserve gap concerns, and allow records on ended plans (specs 004–005, 007).
- [ ] Test real Pi, OpenCode and LLM executions, especially OpenCode questions/resume and cancellation.

## Draft specs

- [ ] Review [Plan Policy](docs/specs/spec-008-plan-policy.md): effects, rules, sources, grants, checkpoints and engine permission mapping.
- [ ] Review [Orchestration](docs/specs/spec-009-orchestration.md): `PlanContinue`, holds and the plan event log.
- [ ] Implement both once settled; they are prerequisites of the chat interface incubated in `auto-agent`.

## Hardening

- [ ] Verify budget behaviour for nested allocations, retained siblings, retries and concurrent runs against plan-wide limits.
- [ ] Stress-test recovery from process crashes, concurrent writers and successful Elaborators interrupted before application.
- [ ] Improve installation of the separate Memo dependency and its retention across environment syncs.
- [ ] Complete the final code review and commit the implementation when approved.

## Outside the implemented scope

- Automatic scheduling
- Automatic retries
- Learning capture
- Refinement-hint execution
- Memorisation operations and wake integration (spec 010); runnable consolidation
  also depends on Memo's required agent operations and reconciliation contract.
