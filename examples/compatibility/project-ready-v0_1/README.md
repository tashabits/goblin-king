# Project-Ready Compatibility Fixture v0.1

This fixture is intentionally small. It proves that a host project using the
`0.1.0` project settings, registry, worker image map, and API settings shapes can still
be discovered and scheduled by current Goblin King code.

Validate it from the repository root:

```bash
goblin-king project validate --project examples/compatibility/project-ready-v0_1/goblin-king-project.json
```

Use this fixture as the starting point for future upgrade tests. When a future contract
changes, add a new compatibility fixture instead of rewriting this one.
