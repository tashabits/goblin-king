# Contributing

Goblin King code should stay easy to review, test, and reuse across projects.

## Branches And Pull Requests

- Do code work on feature branches. Agent-created branches should use the `codex/` prefix unless a maintainer asks for another naming scheme.
- Submit changes through pull requests into `main`.
- Do not commit directly to `main`.
- Keep each pull request scoped to one coherent change.
- Include a short summary, local CI test evidence, and any known follow-up work in every pull request.

## Local CI

All phases use local CI unless the project explicitly changes this policy later. Do not
rely on GitHub Actions CI runs as the required quality gate.

Before opening a pull request, run:

```bash
python -m pytest
python -m ruff check .
```

Add any extra manual CLI smoke-test commands to the pull request description when they are relevant.

## Commenting Standards

- New public modules should start with a concise file-level comment describing purpose and ownership.
- Public functions, runtime entrypoints, goblin contracts, persistence boundaries, and non-obvious helpers should have function-level comments explaining purpose, inputs, outputs, and important failure behavior.
- Avoid comments that restate the code. Comments should explain contract, intent, invariants, edge cases, or operational consequences.
- Each new goblin should document its `GOBLIN_KIND`, expected input shape, result shape, side effects, artifact behavior, and failure modes.

## Tests

Add or update local tests for new contracts, registry behavior, runtime behavior, persistence behavior, and CLI behavior.
