# Pipeline Contracts And Durable Admission

This first foundation slice provides reusable service/catalog contracts and a local
SQLite admission adapter. It does not yet attach GitHub repositories, authenticate
webhooks, execute builds, publish images, deploy workloads, or record releases in Git.
Cluster delivery remains gated by the [delivery roadmap](pipeline-delivery-roadmap.md).

## Two separate configuration owners

`PipelineServiceContract` describes the source-owned build context, Dockerfile,
test argument vector, service port, and health path. See
[`examples/pipeline/service.json`](../examples/pipeline/service.json).
Build context and Dockerfile paths are relative to the repository root. The
Dockerfile need not be inside the build context. `validate_service_checkout`
requires existing inputs, resolves symlinks, and rejects paths escaping the checkout.
The builder must later own a pinned, isolated checkout for the whole operation;
this preliminary check does not eliminate filesystem races or audit source code.

`PipelineCatalog` contains operator-owned `PipelineRegistration` entries. See
[`examples/pipeline/catalog.json`](../examples/pipeline/catalog.json). It selects
approved repositories, exact environment branches, pipeline/configuration revisions,
deployment profiles, targets, namespaces, release names, and image repositories.
Source configuration cannot supply those privileges. Unknown fields are rejected
at every contract boundary. Validating a catalog's shape does not authorize its author
or provision its targets; the future controller must load an approved catalog.

The initial provider key is `github`; repository identity uses a case-normalized
`owner/name`, without URL credentials or a `.git` suffix. Full 40- or 64-character
nonzero Git object IDs are required. A syntactically valid revision is not proof it
exists or was reviewed: the authenticated provider adapter must verify that.

Production and test routes have distinct branches and image destinations. Release
identity is `(target, namespace, release)`. Catalog validation rejects conflicting
service identities, source repositories, release identities, and publication paths.
Only test releases can be inactive. Registrations hold immutable tuples of frozen
environment models so later callers cannot change an admitted configuration in place.

Registry paths must include an explicit lowercase DNS registry (optionally with a
port) or `localhost`, plus an untagged repository path. Literal IPv6 registry hosts
are not part of this initial contract. `target` and `deployment_profile` are names
of operator-approved configuration, not arbitrary cluster URLs or shell commands.

## Admission and duplicate handling

`PipelineAdmissionStore(SQLiteStore(path))` adds durable build intent using the
existing persistence boundary. `admit(registration, source_change)` returns
`PipelineAdmission(request, created)`.

The normalized `SourceChange` is a trusted internal input. This method is not a
webhook endpoint. The future adapter must verify event authenticity, installation
scope, repository/branch permissions, and reviewed-merge policy before calling it.

- The first automatic event for a project, service, provider, repository,
  environment, and source commit allocates a UUID build ID and stores the original
  source event plus pipeline/catalog/environment snapshot.
- Redelivering that event returns the original request with `created=False`.
  A different delivery ID for the same revision also returns that request and
  records its own receipt. No additional scheduler job or build is created.
- Reusing a delivery ID with changed source content, actor, or environment routing
  raises `PipelineEventConflict`; the transaction writes nothing.
- Source receipt and build intent commit together. A failure between inserts rolls
  back both, so a later retry can safely create the request.
- Independent SQLite connections serialize admission using a write transaction.
  Unique database keys also protect source and delivery identity.
- Reopening the database preserves request identity. Enumeration is project-scoped,
  ordered by durable sequence, and bounded to 1,000 entries per call.

Use `request.build_id` as the reserved immutable image tag in a future build stage.
Its existence is not proof an image was built, tested, published, or deployed.
The request does not contain an image digest until the separate publication design
is implemented. The database has two additive tables: `pipeline_build_requests` and
`pipeline_receipts`. Existing jobs, schedules, and validation behavior are unchanged.

The stored environment is an admission snapshot. Deployment must later re-read
current desired state under a release lock; an old `active=True` cannot override a
subsequent off switch. A new catalog revision does not rewrite or automatically
rebuild previously admitted source. Explicit build retries/rebuilds require a
separate operation with new build IDs, which is not implemented here.

Callers must consume persisted requests after restart, not rely solely on the
`created` return flag. There is no dispatch acknowledgement or worker claim yet;
adding the next stage requires a durable claim/outbox protocol. Source deduplication
is not a substitute for deployment locking or stale-build rejection.

## Local proof

See the [recorded local results and blocked checks](proofs/pipeline-foundation.md).

From an editable installation at the repository root:

```bash
python -m pytest tests/test_pipeline_contracts.py tests/test_pipeline_store.py
python examples/pipeline/admission_smoke.py
```

The smoke command uses a temporary SQLite database, admits a fixture event, recreates
the store, and verifies a replay returns the same request without scheduling work.
All repository names, hashes, and registry destinations in the fixtures are examples.
No external service or credential is needed.

To generate schemas for an adopter's validation tools, use
`PipelineServiceContract.model_json_schema(by_alias=True)` and
`PipelineCatalog.model_json_schema(by_alias=True)`. These versioned contracts are
alpha interfaces and may evolve while the delivery controller is developed.

## Remaining cluster work

This adapter inherits the existing local SQLite transaction model. It does not
establish distributed storage, high availability, release locks, Kubernetes worker
isolation, or recovery of cluster side effects. Those require the persistence
topology and actual cluster proof in the roadmap. A passing local admission test
must never be reported as end-to-end Kubernetes delivery proof.
