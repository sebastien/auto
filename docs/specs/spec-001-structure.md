# Project Structure


Overall layout:

```
bin/auto
docs/
  specs/
    spec-NNN-{feature}_{name}.md   # Specification
src/py/auto
  integration/
    opencode/               # OpenCode integration
    pi/                     # Pi integration
    llm/                    # Simon Willison llm integration
    memo/                   # Memo integration (memory storage)
  runtime/                  # Runtime capabilities
    operations/
      storage.py            # Storage operations for the runtime
    model.py                # Model operations for the runtime
    config.py               # Configuration options
  features/
    script/
      model.py              # Data model
      operations.py         # Operations using a semi abstract runtime
      config.py             # Configuration options
    task/
    …
  app/
    cli/
      main.py               # Main CLI module
  utils/
    *.py                    # Shared utilities
tests/
  data/                     # Test data
  TNNN-NN-{test_type}-{feature}_{test_name}.test.py #  Test number matches spec, then index
```


Patterns:
- `Runtime/Feature/Integration`, this is the main pattern used for implementation.
  The runtime acts as a gateway to operations that need to be performed, which
  are then implemented through integrations. The runtime is abstract but provides
  a default implementation using a preferred integration. Features leverage the runtime
  in an integration-agnostic way. Adding new features often means extending the runtime.
- `Model/Operation/Config`, this is the secondary pattern used for implementing the runtime
  and features. Model defines data structures, operations define behaviour (using integrations
  for the runtime, and the runtime for features), and configuration defines the
  parameters.



