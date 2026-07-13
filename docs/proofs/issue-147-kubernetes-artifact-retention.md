# Issue 147 Kubernetes Artifact Retention Proof

This proof builds the current control-plane and artifact-worker images, installs the chart into a
single-node kind cluster, authenticates generic Kubernetes validation, verifies its persisted
scheduler identity and complete validation-directory cleanup, then submits the PNG/ZIP proof mode.
It waits for transient Job deletion, downloads both retained files, verifies SHA-256, invokes
artifact cleanup, and proves downloads then return `404`.

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
  --namespace goblin-artifact-proof \
  --release goblin-artifact-proof
```

A passing JSON receipt contains:

- `"status": "passed"`;
- the non-empty `"validation_identity"` persisted for `example.artifact`;
- `"validation_artifact_cleanup": "proved"`, including no files or empty hashed directories below
  the artifact root before normal scheduling;
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
artifact subpath. The worker does not have `retained-artifacts`. Under `restricted-v1`, inspect the
Pod `fsGroup` and confirm the retained root is setgid/group-writable for that same group.

Remove the disposable environment:

```bash
helm uninstall goblin-artifact-proof --namespace goblin-artifact-proof
kind delete cluster --name gk-artifact-proof
```

## Local Verification Recorded During Implementation

Date: 2026-07-12

Base after the generic Kubernetes validation rebase: `4942180ce97d8547efdc8eb620f038806a5c9104`.

Corrective commits reviewed here:

- `8b45879`: retention-enabled runs wait for the forwarder-owned Redis result key;
- `9db7a0b`: descriptor-safe copying, shared-volume modes, validation cleanup, retention-bound
  identities, and validation-first acceptance proof.

Observed after the corrective review and before live cluster proof:

```text
Focused corrective gate: 33 passed, 5 skipped.
Independent combined retention/security gate: 95 passed, 5 skipped.
Uncontended full suite: 405 passed, 5 skipped, 1 full-suite-order-only failure.
Isolated scheduler timeout regression: 1 passed.
Ruff: All checks passed!
1 chart(s) linted, 0 chart(s) failed
Default, persistence-disabled, and restricted-retention Helm renders completed.
Wheel build: goblin_king-0.1.0-py3-none-any.whl, SHA-256
df10ae68f2da303a1d6c2ee6c32bedfdd42326920db17437136ec164ed9f5d99.
```

The five skipped tests require POSIX symbolic-link, no-follow descriptor, group-ID, or setgid-mode
behavior unavailable to this Windows test process. Traversal, external containment, source-swap,
count, size, media type, digest, unconfigured storage, atomic retention, idempotency, validation-run
cleanup, API download after source deletion, cleanup, manifest isolation, typed settings, legacy
inline-forwarder compatibility, retention identity staleness, restricted retention mount
composition, kind-specific security, observed-run diagnostics, shared runtime construction, and
failed-Job retention tests ran.

The one full-suite failure was the pre-existing zero-timeout scheduler timing assertion: it expected
`timed_out` but observed `completed` in full-suite order. The same test passed immediately in
isolation. No artifact-retention code path was involved; this remains an upstream timing-test blocker
rather than being masked by this change.

## Live Resource-Floor Diagnosis

A live kind run on clean commit `99792e6` reached the retention sidecar but exposed a concrete
resource blocker. The worker exited zero, while the packaged forwarder was exit `137`, reason
`OOMKilled`, in about three seconds at its former 16 MiB request and 64 MiB limit. The exact control
image digest was
`sha256:be88e534ff937666edcf58879f5c69f2e0b4d841d4e96f6322bfec69cf92ad60`; the exact artifact-worker
digest was
`sha256:8948af7487eb86adc194d5a2cfef539742d6619d941aba95712fb462169a6df3`.

The storage boundary was healthy during that failure. The API ran as UID/GID `10001:10001` with
supplementary group `65532`, and `/data/artifacts` was mode `02770`, UID `10001`, GID `65532`.

An otherwise identical manual restricted Pod with a 64 MiB forwarder request and 128 MiB limit
completed both containers with exit zero in about five seconds. It retained a 68-byte PNG and
162-byte ZIP with directory/file modes `02770`/`0660` and UID/GID `65532:65532`. This measured floor
is now the `restricted-v1` default; worker resources and legacy Pod shape remain unchanged.

## Final Automated Acceptance

The complete acceptance passed on clean commit `3b35be4` in a freshly recreated namespace and
database. The exact control-plane/result-forwarder image was
`ghcr.io/tashabits/goblin-king@sha256:cd5f42618aa3788fb9658aea8cec39963a037de8f9c946154f566d28a21aac77`;
the exact artifact-worker image remained
`ghcr.io/tashabits/goblin-king-example-artifact@sha256:8948af7487eb86adc194d5a2cfef539742d6619d941aba95712fb462169a6df3`.

Authenticated validation passed first and persisted identity
`kubernetes:ghcr.io/tashabits/goblin-king-example-artifact@sha256:8948af7487eb86adc194d5a2cfef539742d6619d941aba95712fb462169a6df3:workload-security:de56f23ca5aa1f208c4429adf7af1568455b281e212fff2e765f25fa0406d5e9`.
The effective policy recorded `restricted-v1`, no automatic or projected worker token, complete
container security, worker memory `64Mi`/`512Mi`, and forwarder memory `64Mi`/`128Mi`. Validation
returned PNG/ZIP metadata and left no validation-owned directory or file before normal scheduling.

Normal job `18237e29-0371-493c-b45c-305874f91840` produced Run
`936e7602-c08b-4d36-85ff-1a6e7bc74431`. Both transient Job and input ConfigMap were gone before the
acceptance returned. Authorized downloads produced:

- `artifact-proof.png`: SHA-256
  `431ced6916a2a21a156e38701afe55bbd7f88969fbbfc56d7fe099d47f265460`;
- `artifact-proof.zip`: SHA-256
  `aa4f0b02a8a9778b438b8feb9fd6537df83061cdbae7ed3fa984958c730aa7aa`.

Policy cleanup selected both files and all 230 retained bytes, deleted them, and both download
routes then returned `404`. The API, scheduler, and Redis deployments remained ready with zero
restarts. The API ran as UID/GID `10001:10001` with supplementary group `65532`; the retained root
remained setgid/group-writable for `65532`. This closes the diagnostic limitation above with a real
validation-first, cross-identity, cleanup-complete cluster receipt.
