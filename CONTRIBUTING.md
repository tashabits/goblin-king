# Contributing

Thanks for helping with Goblin King. This repository is currently open-source alpha /
project-adoptable alpha software, so contributions should keep the public model clear:
Goblin King schedules contract-compliant Docker/OCI goblin containers for trusted
self-hosted projects.

For the full contribution guide, see [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md).

## Local Checks

Run local CI before opening a pull request:

```bash
python -m pytest
python -m ruff check .
```

Frontend/admin changes also need:

```bash
cd admin-ui
npm test -- --run
npm run build
```

## Pull Request Expectations

- Preserve the container-first worker model; Python helpers are optional, but goblins are
  contract-compliant containers.
- Do not weaken the mandatory validation gate or resource-policy expectations.
- Include Docker proof for Docker/runtime changes and Helm render or smoke proof for
  Helm/Kubernetes changes.
- Keep docs, validation behavior, and resource-policy examples current with code
  changes.
- Record exact local test output in the PR body. GitHub Actions are not the required
  quality gate for this project yet.

