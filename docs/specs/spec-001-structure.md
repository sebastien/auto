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
  integration/
    opencode/               # OpenCode integration
    pi/                     # Pi integration
    llm/                    # llm integration
    memo/                   # Memo integration (memory and learning storage)
  runtime/
    model.py                # Runtime data model
    operations.py           # Core abstract operations
    config.py               # Runtime configuration
  features/
    script/
      model.py              # Feature data model
      operations.py         # Feature operations, using the runtime
      config.py             # Feature configuration
    task/
    …
  app/
    cli/
      main.py               # Main CLI module
    mcp/
      main.py               # MCP server, served by `auto mcp`
  utils/
    *.py                    # Shared utilities
tests/
  data/                     # Test data
  test_NNN_NN_{type}_{feature}_{name}.py  # Spec number, then index
```

## Rules

- Keep files under 1000 lines, split into submodules as they grow.
- Follow `review-py` standards.
