# Internal Release Checklist

Use this checklist for private/internal Goblin King releases. Public PyPI release
hardening remains deferred.

The release story starts with the generic portable worker backbone: project config,
worker image maps, validation gates, resource policy, and Docker/Helm deployment shape.
Profile-specific work comes after that. RAG is the first intended profile/use case, but
it should prove it fits the same backbone rather than becoming a separate scheduler or
competing runtime path.

Existing JupyterHub notebook function, notebook ASGI service, Goblin Directory browser
UI, and JupyterLab Directory picker flows remain compatible authoring and sharing paths
for the same container-backed model. They are optional proof surfaces, not replacements
for project config, Dockerfile-backed workers, or the validation gate.

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
make worker-backbone-proof
make rag-profile-proof
make project-validate
make project-build-workers
make project-discovery-reload
make project-admin-proof
helm template goblin-king charts/goblin-king -f examples/adopting-project/helm-values.yaml
```

`make rag-profile-proof` defaults to the bundled portable worker-backbone fixture and
its local RAG first-use-case worker. For a private RAG adopter profile, run it with the
profile project and values files, for example:

```bash
make rag-profile-proof \
  RAG_PROFILE_PROJECT=/path/to/rag-profile/goblin-king-project.json \
  RAG_PROFILE_HELM_VALUES=/path/to/rag-profile/helm-values.yaml \
  RAG_PROFILE_KIND=rag.index
```

Run heavier runtime proofs when the release changes worker runtime behavior, notebook
runner behavior, JupyterHub auth, or Directory flows:

```bash
make validate-cross-language-workers
make validate-behavior-workers
make notebook-service-docker-proof
make jupyterhub-workbook-proof
make jupyterhub-directory-proof
make jupyterhub-directory-ui-proof
make jupyterhub-directory-picker-proof
```

## Evidence To Record

- Wheel filename and install proof.
- API/scheduler/admin image tags.
- Worker image tags.
- Generic worker-backbone proof output.
- RAG profile proof output, including whether the bundled local fixture or a private
  RAG profile was used.
- Local CI output.
- Docker adoption smoke output.
- Helm render or live Helm smoke output.
- Discovery reload output showing project goblins.
- Admin screenshot or guide screenshot path.

The King accepts a release only when the receipts are boring.
