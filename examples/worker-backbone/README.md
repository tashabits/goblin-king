# Portable Worker Backbone Recipes

This fixture shows a small, project-config-driven worker backbone without external
models, cloud services, or credentials. It is intended as a recipe set that can be
copied into an adopting project and adapted one workload at a time.

## Contents

| Kind | Folder | Demonstrates |
| --- | --- | --- |
| `example.worker-backbone.normalize-note` | `workers/normalize-note` | A deterministic task worker that reads JSON input and writes a result envelope. |
| `example.worker-backbone.artifact-manifest` | `workers/artifact-manifest` | An artifact-producing task worker using `GOBLIN_ARTIFACT_ROOT`. |
| `example.worker-backbone.local-rag` | `rag-first-use-case` | Local retrieval over checked-in fixture documents with no model call. |
| `example.worker-backbone.catalog-service` | `workers/catalog-service` | A small HTTP service worker with deterministic probe payloads. |

The root project config loads `registries/worker-backbone.json`, the worker image map in
`goblin-images.json`, local inputs, and input schemas. The service example is declared
inline in `goblin-king-project.json` so service metadata such as `port` and `probePath`
travels with the project config.

## Local Proof

Static validation from the repository root:

```bash
goblin-king project validate --project examples/worker-backbone/goblin-king-project.json
```

Focused tests, including deterministic RAG fixture proof without Docker:

```bash
python -m pytest tests/test_worker_backbone_examples.py
```

Docker builds are optional and local only. This form includes the inline service worker:

```bash
goblin-king project validate \
  --project examples/worker-backbone/goblin-king-project.json \
  --check-worker-builds
```
