# Project Structure

This specification defines the overall structure and patterns to be used for
implementation. It leans more towards a plan, which is on purpose: it directs
the implementation and lays out a foundation to build upon.

## Stack

- Python 3.14+ for the runtime
- [OpenCode](https://opencode.ai), Pi, [llm](https://github.com/simonw/llm) for agent execution
- [Memo](https://github.com/sebastien/memo) for memory and learning storage
- Kame and Kash for automation and scripting

## Patterns

### Runtime/Feature/Integration

The main pattern used for implementation, where the *runtime* acts as
a gateway to operations (and effects):
- *Runtime*: defines operations based on a minimal set of core abstract
  operations implemented by integrations. The runtime uses a default
  integration to be able to run.
- *Features*: submodules that build upon the runtime and its operations to
  offer a new behaviour, in an integration-agnostic way. Adding new features
  often means extending the runtime.
- *Integrations*: implement the core abstract operations for a given
  underlying backend.

### Model/Operation/Config

The secondary pattern, used for implementing the runtime, features or other
modules that introduce a cohesive set of functionality:
- *Model*: data structures, their relations and constraints.
- *Operations*: behaviour, typically leveraging the runtime for concrete
  operations and effects.
- *Configuration*: the configurable parameters of the operations.

Operations take the form `Operation(context,arguments...)`, where:
- `context` composes the runtime and (integration) configuration
- `arguments` is a serializable payload giving the inputs and parameters

### Operation/Interface

Operations are the contract. Interfaces (CLI, MCP) are projections of
a subset of the operations, following shared mapping rules and adding no
behaviour (see [Interface](spec-007-interface.md)).

## Filesystem Layout

The expected layout of the project. The intent is to define conventions that
can be leveraged for automation, as well as consistency with other projects.

```
bin/auto                    # CLI entry point
Kamefile                    # Automation rules (Kame)
docs/
  specs/
    spec-NNN-{name}.md      # Specification
src/md/auto/
  catalogue/
    {kind}/{name}.md        # Built-in catalogue entry (see Elaboration)
    {kind}/{name}.{companion}.{ext}  # Companion files of the entry
src/py/auto/
  wiring.py                 # Composition root: reference resolvers
  integration/
    process.py              # Shared subprocess boundary
    opencode/
      engine.py             # OpenCode integration
    pi/
      engine.py             # Pi integration
    llm/
      engine.py             # llm integration
    memo/
      store.py              # Memo integration (memory and learning storage)
  runtime/
    model.py                # Runtime data model, statuses, enums and effect options
    operations.py           # Core abstract operations
    config.py               # Runtime configuration
    integration.py          # Engine and knowledge integration contracts
  features/
    script/
      model.py              # Feature data model
      operations.py         # Feature operations, using the runtime
      config.py             # Feature configuration
      templates.py          # Liquid renderer and transclusions
    task/
      model.py              # Task and Run models
      operations.py         # Task and run operations
    plan/
      model.py              # Plan, Revision, Gap and Decision models
      operations.py         # Plan graphs, revisions and status
      store.py              # Journal, lock and recovery
      references.py         # Plan-scoped reference resolution
      gaps.py               # Gap operations and Memo reconciliation
    elaboration/
      operations.py         # Elaboration, plan creation and refinement
      catalogue.py          # Layered framework assets
      materialize.py        # Template instantiation
  app/
    operations.py           # Exposure metadata shared by interfaces
    cli/
      main.py               # Main CLI module
    mcp/
      main.py               # MCP server, served by `auto mcp`
  utils/
    data.py                 # YAML/JSON, digests and atomic writes
    references.py           # Relative reference relocation
    *.py                    # Shared utilities
tests/
  data/                     # Test data
  test_NNN_NN_{type}_{feature}_{name}.py  # Spec number, then index
```

## Rules

- Keep files under 1000 lines, split into submodules as they grow.
- Follow `review-py` standards.
- Depend downward: `utils` and `runtime` know no feature; integrations implement
  the contracts in `runtime/integration.py`; `script → task → plan → elaboration`;
  `app` projects feature operations. Reference-scheme wiring lives in
  `wiring.py`, the only module that knows both the runtime and the features.
- Operations are `PascalCase` (`{Noun}{Verb}`); helpers and model members are
  `camelCase`. Model payloads serialise to the snake_case keys defined by the
  specifications.
- Entities with a specification model have a dataclass: `Script`, `Task`, `Run`,
  `Plan`, `Revision`, `Gap` and `Decision`. Operations may return their
  serialised form at the interface boundary.
