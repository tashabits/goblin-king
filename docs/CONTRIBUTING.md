# Contributing

Goblin King code should stay easy to review, test, and reuse across projects.

## Branches And Pull Requests

- Do code work on feature branches. Agent-created branches should use the `codex/` prefix unless a maintainer asks for another naming scheme.
- Submit changes through pull requests into `main`.
- Do not commit directly to `main`.
- Keep each pull request scoped to one coherent change.
- Include a short summary, local CI test evidence, phase objective proof, and any known follow-up work in every pull request.

## Local CI

All phases use local CI unless the project explicitly changes this policy later. Do not
rely on GitHub Actions CI runs as the required quality gate.

Before opening a pull request, run:

```bash
python -m pytest
python -m ruff check .
```

Add any extra manual CLI smoke-test commands to the pull request description when they are relevant.

Pull request bodies must prove that phase objectives were met. For phase work,
include a concrete evidence section that maps each objective to the code, tests,
or manual smoke output that verifies it. Local CI proof should include the exact
commands run and their results, for example `python -m pytest - 25 passed` and
`python -m ruff check . - passed`. When a phase includes CLI behavior, persistence,
or scheduler behavior, include the relevant manual commands and a short statement
of what the command proved.

When a phase includes Docker behavior, PR evidence must include real local Docker proof.
Mocked Docker tests are useful, but they do not replace building the worker image,
running the Docker-backed path, and recording the observed result in the PR body.

## Commenting Standards

- New public modules should start with a concise file-level comment describing purpose and ownership.
- Public functions, runtime entrypoints, goblin contracts, persistence boundaries, and non-obvious helpers should have function-level comments explaining purpose, inputs, outputs, and important failure behavior.
- Avoid comments that restate the code. Comments should explain contract, intent, invariants, edge cases, or operational consequences.
- Each new goblin should document its `GOBLIN_KIND`, expected input shape, result shape, side effects, artifact behavior, and failure modes.

## Tests

Add or update local tests for new contracts, registry behavior, runtime behavior, persistence behavior, and CLI behavior.
Docker runtime phases must also include local Docker tests that fail clearly when Docker
is unavailable.
