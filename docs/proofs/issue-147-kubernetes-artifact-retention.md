# Issue 147 Kubernetes Artifact Retention Proof

This proof builds the current control-plane and artifact-worker images, installs the chart into a
single-node kind cluster, submits the PNG/ZIP proof mode, waits for transient Job deletion, downloads
both retained files, verifies SHA-256, invokes artifact cleanup, and proves downloads then return
`404`.

## Prerequisites

- Docker Engine
- kind
- kubectl
- Helm
- Python 3.12 or newer

Run from the repository root. Use a disposable cluster because the final command deletes it.

## Build And Install

```bash
kind create cluster --name gk-artifact-proof
docker build -t goblin-king:local .
docker build -t goblin-king-example-artifact:local workers/example.artifact
kind load docker-image goblin-king:local --name gk-artifact-proof
kind load docker-image goblin-king-example-artifact:local --name gk-artifact-proof
kubectl create namespace goblin-artifact-proof
helm upgrade --install goblin-artifact-proof charts/goblin-king \
  --namespace goblin-artifact-proof \
  --set image.pullPolicy=Never \
  --set admin.enabled=false \
  --set workers.exampleLongHello.enabled=false \
  --wait --timeout 5m
```

The current chart uses the control-plane image as its result forwarder. The `goblin-king:local` tag
is therefore deliberately built and loaded above. A separately configurable forwarder identity is
tracked independently and is not claimed by this proof.

In another terminal, keep the API port-forward running:

```bash
kubectl port-forward --namespace goblin-artifact-proof \
  deployment/goblin-artifact-proof-api 18000:8000
```

Run the automated acceptance check:

```bash
python scripts/kubernetes_artifact_retention_proof.py \
  --api-url http://127.0.0.1:18000 \
  --token local-dev-token \
  --namespace goblin-artifact-proof
```

A passing JSON receipt contains:

- `"status": "passed"`;
- durable Job and Run IDs;
- `"job_cleanup": "proved"`;
- SHA-256 values for `artifact-proof.png` and `artifact-proof.zip`;
- the artifact-cleanup response selecting at least both files.

Inspect the effective worker Pod contract during a run if additional evidence is required:

```bash
kubectl get pods --namespace goblin-artifact-proof \
  --selector goblin-king.worker=true -o yaml
```

The worker has the transient `artifacts` mount. The forwarder has that mount read-only plus the PVC
artifact subpath. The worker does not have `retained-artifacts`.

Remove the disposable environment:

```bash
helm uninstall goblin-artifact-proof --namespace goblin-artifact-proof
kind delete cluster --name gk-artifact-proof
```

## Local Verification Recorded During Implementation

Date: 2026-07-12

Base after rebase: `6e1118e5db22d8b8dbda17031106f39689f71408`

Observed before live cluster proof:

```text
345 passed, 1 skipped in 88.76s
All checks passed!
1 chart(s) linted, 0 chart(s) failed
helm_retention_variants=passed
retention_modules_present=True
Admin UI: 9 passed; production build completed.
```

The skipped test is the symbolic-link case when the Windows test account lacks link-creation
permission. Traversal, containment, count, size, media type, digest, unconfigured storage, atomic
retention, idempotency, API download after source deletion, cleanup, and manifest isolation tests ran.
The package wheel contains all three retention/forwarder modules. Helm rendering proved that
persistence-enabled API/scheduler Pods receive the artifact PVC settings and persistence-disabled
Pods omit the claim while retaining the URI-root setting.

After the final failed-Job artifact regression was added, 52 affected lifecycle/runtime tests and
all 7 Docker runtime tests passed. One complete-suite attempt reached 345 passed and one skipped
before the disposable Redis fixture reset a localhost connection in an unchanged Docker test; that
exact test and then the complete Docker test file passed in isolation. The earlier complete branch
checkpoint passed 345 tests with one skipped. This external fixture instability is recorded rather
than presented as a clean post-change full-suite receipt.

Live kind execution was not performed in this implementation worktree. The publishing verifier must
run the commands above and replace this limitation with the observed commit SHA, image identities,
Job/Pod state, receipt, and cleanup result. No live output is inferred here.
