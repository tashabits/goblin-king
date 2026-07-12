# Issue 148: Kubernetes Workload Security Proof

## Compatibility Decision

The secure contract is an explicit `restricted-v1` profile. `legacy` remains the
runtime, constructor, CLI, API, and Helm default. Automated comparison showed the
legacy Pod spec still contains only `containers`, `restartPolicy`, and `volumes`; it has
no new security, token, ServiceAccount, or forwarder-resource fields.

## Automated Gates

The focused implementation gate ran:

```bash
python -m ruff check src/goblin_king/kubernetes_*.py \
  src/goblin_king/api.py src/goblin_king/cli.py src/goblin_king/scheduler.py \
  tests/test_kubernetes_*.py tests/test_helm_runtime_images.py tests/test_validation.py

python -m pytest \
  tests/test_runtime.py \
  tests/test_kubernetes_runtime.py \
  tests/test_kubernetes_workload_security.py \
  tests/test_kubernetes_security_validation.py \
  tests/test_kubernetes_configuration.py \
  tests/test_kubernetes_api_settings.py \
  tests/test_helm_runtime_images.py \
  tests/test_validation.py -q

helm lint charts/goblin-king
```

Observed before the live pass: 46 tests passed, Ruff passed, Helm lint passed, and
`git diff --check` passed. The suite proves legacy compatibility, complete restricted
manifests, resource merging, intentional relaxation rejection, per-kind projected token
isolation, API/scheduler proof identity, settings-file loading, and Helm rendering.

Final gates after the live pass and documentation review observed:

- `python -m pytest -q`: 361 passed in 104.71 seconds;
- `python -m ruff check .`: passed;
- `helm lint charts/goblin-king`: passed with only the existing optional-icon note;
- restricted-profile `helm template`: passed with read-only resource policy enabled;
- `git diff --check` and worktree status: clean.

## Live Kind Proof

Context: `kind-goblin-king-upstream-proof`

Fresh namespace: `goblin-king-issue-148-proof`

Implementation commit: `8c6bf6d`

Exact locally built and node-loaded images:

- `ghcr.io/tashabits/goblin-king:issue148-8c6bf6d`
- `ghcr.io/tashabits/goblin-king-example-echo:issue148-8c6bf6d`

The retained-runtime proof was executed from a namespaced runner with:

```bash
python /tmp/prove_kubernetes_workload_security.py \
  --namespace goblin-king-issue-148-proof \
  --redis-url redis://redis:6379/0 \
  --worker-image ghcr.io/tashabits/goblin-king-example-echo:issue148-8c6bf6d \
  --forwarder-image ghcr.io/tashabits/goblin-king:issue148-8c6bf6d \
  --output /tmp/issue-148-proof
```

The runtime returned `status: success`, echoed `restricted live proof`, reported
`message_length: 21`, and loaded the result through Redis. Both containers terminated
with exit code zero.

The actual completed Pod proved:

- phase `Succeeded`;
- `automountServiceAccountToken: false`;
- only `input`, `result`, and `artifacts` volumes; no ServiceAccount token volume;
- no token mount in either container;
- pod UID/GID/fsGroup `65532`, non-root, and `RuntimeDefault` seccomp;
- both containers non-root, no escalation, not privileged, read-only root,
  `RuntimeDefault` seccomp, and `ALL` capabilities dropped;
- worker resources `100m`/`1` CPU and `64Mi`/`512Mi` memory request/limit;
- forwarder resources `10m`/`100m` CPU and `16Mi`/`64Mi` memory request/limit;
- the forwarder mounted only `/goblin-result`;
- worker image ID
  `ghcr.io/tashabits/goblin-king-example-echo@sha256:207ca3a6318ee111fdf992d5cbdcb88faf4a0776dd44970b5f94c00367856623`;
- forwarder image ID
  `sha256:5a002bf57433419a3e3e2bd8e72f3810c08d583b6d6dd9b3c7595bde2f578876`.

Generated legacy, restricted, and result JSON artifacts were copied to the ignored
local folder `.goblin-king/proofs/issue-148/` for review. The proof namespace was then
deleted successfully. The shared cluster and node image cache were not deleted.

## Reproduction Helper

[`scripts/prove_kubernetes_workload_security.py`](../../scripts/prove_kubernetes_workload_security.py)
creates legacy/restricted manifest artifacts, executes one retained restricted Job, and
prints the result and security-bound validation identity. Run it only in a disposable
namespace and delete that namespace after inspecting the Pod.
