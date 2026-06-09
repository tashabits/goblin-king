# Internal Release Checklist

Use this checklist for private/internal Goblin King releases. Public PyPI release
hardening remains deferred.

## Build Artifacts

```bash
python -m pip wheel . -w dist
docker build -t goblin-king:<version> .
docker build -t goblin-king-admin-ui:<version> admin-ui
python -m goblin_king.cli workers build --images goblin-images.json
```

## Local CI

```bash
python -m pytest
python -m ruff check .
cd admin-ui && npm test -- --run
cd admin-ui && npm run build
```

## Adoption Smoke

```bash
make project-validate
make project-build-workers
make project-discovery-reload
make project-admin-proof
helm template goblin-king charts/goblin-king -f examples/adopting-project/helm-values.yaml
```

## Evidence To Record

- Wheel filename and install proof.
- API/scheduler/admin image tags.
- Worker image tags.
- Local CI output.
- Docker adoption smoke output.
- Helm render or live Helm smoke output.
- Discovery reload output showing project goblins.
- Admin screenshot or guide screenshot path.

The King accepts a release only when the receipts are boring.
