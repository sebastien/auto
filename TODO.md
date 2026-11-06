# TODO

The implementation is in place; validation and hardening remain.

## Priorities

- [ ] Review specs 000–007 systematically against the implementation. Passing tests do not establish complete conformance.
- [ ] Test real Pi, OpenCode and LLM executions, especially OpenCode questions/resume and cancellation.

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
