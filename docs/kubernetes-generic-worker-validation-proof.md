# Generic Kubernetes Worker Validation Proof

This proof starts with a fresh Helm release, a preloaded generic worker image, and no
worker-validation rows. It creates proof through the authenticated API, confirms the
exact scheduler identity, then submits the first normal job. Docker validation and
manual database seeding are not used.

## Prerequisites

- Python 3.12 or newer with this checkout installed.
- Docker, `kind`, `kubectl`, Helm, `curl`, and `jq`.
- A disposable kind cluster named `gk-validation-proof`.
- No existing `goblin-king` Helm release in namespace `gk-validation-proof`.

The commands below use the chart defaults: control image `goblin-king:local`, generic
worker `goblin-king-example-hello:local`, bootstrap token `local-dev-token`, and
`IfNotPresent`/`Never` local-image behavior. When a deployment uses a registry, replace
the build/load steps with digest-pinned published images.

Tag-only worker references retain their historical scheduler identity and are not
immutable. This proof uses local tags to match the chart fixture, while production
evidence should use a worker map whose image is pinned as `repository@sha256:...`.

## 1. Build And Preload Only The Required Images

```bash
kind create cluster --name gk-validation-proof

docker build -t goblin-king:local .
python -m goblin_king.cli workers build \
  --images demo-images.json \
  --kind example.hello

kind load docker-image \
  goblin-king:local \
  goblin-king-example-hello:local \
  --name gk-validation-proof
```

This deliberately does not run `workers validate`, start Docker Redis, or create a
SQLite validation row.

## 2. Install A Fresh Minimal Chart

```bash
helm upgrade --install goblin-king charts/goblin-king \
  --namespace gk-validation-proof \
  --create-namespace \
  --set image.pullPolicy=Never \
  --set admin.enabled=false \
  --set workers.exampleLongHello.enabled=false \
  --wait \
  --timeout 5m

kubectl wait --namespace gk-validation-proof \
  --for=condition=available deployment/goblin-king-api \
  --timeout=120s
kubectl wait --namespace gk-validation-proof \
  --for=condition=available deployment/goblin-king-scheduler \
  --timeout=120s
```

Confirm the chart starts with no seeded proof:

```bash
kubectl exec --namespace gk-validation-proof deployment/goblin-king-api -- \
  goblin-king workers validation-status --db /data/goblin-king.sqlite3
```

Expected output is empty.

## 3. Invoke The Authenticated Validation Operation

Keep the port-forward running in its own terminal:

```bash
kubectl port-forward --namespace gk-validation-proof \
  service/goblin-king-api 8000:8000
```

From another terminal:

```bash
curl --fail-with-body -sS \
  -X POST http://127.0.0.1:8000/admin/workers/validate-kubernetes \
  -H "Authorization: Bearer local-dev-token" \
  -H "Content-Type: application/json" \
  -d '{"kinds":["example.hello"],"input":{"name":"Validation"},"require_success":true,"timeout_seconds":120}' \
  | tee /tmp/goblin-kubernetes-validation.json \
  | jq .
```

Required assertions:

```bash
jq -e '.validations | length == 1' /tmp/goblin-kubernetes-validation.json
jq -e '.validations[0].ok == true' /tmp/goblin-kubernetes-validation.json
jq -e '.validations[0].image == "goblin-king-example-hello:local"' \
  /tmp/goblin-kubernetes-validation.json
jq -e '.validations[0].image_digest == "kubernetes:goblin-king-example-hello:local"' \
  /tmp/goblin-kubernetes-validation.json
jq -e '.validations[0].checks | index("kubernetes-job") != null' \
  /tmp/goblin-kubernetes-validation.json
jq -e '.validations[0].checks | index("result-envelope") != null' \
  /tmp/goblin-kubernetes-validation.json
jq -e '.validations[0].logs.worker != null' /tmp/goblin-kubernetes-validation.json
jq -e '.validations[0].logs["result-forwarder"] != null' \
  /tmp/goblin-kubernetes-validation.json
```

`artifacts` is an array of result-envelope metadata and is empty for `example.hello`.
Use an artifact-producing configured worker when non-empty metadata is part of the
deployment proof.

Confirm the API persisted the exact identity without a seed:

```bash
curl --fail-with-body -sS \
  -H "Authorization: Bearer local-dev-token" \
  http://127.0.0.1:8000/goblins \
  | jq -e '.[] | select(.kind == "example.hello") |
      .validation.status == "passed" and
      .validation.image_digest == "kubernetes:goblin-king-example-hello:local"'
```

The validation Job and ConfigMap are transient. Before submitting the normal job, wait
for background deletion and require both checks to print nothing:

```bash
kubectl get jobs --namespace gk-validation-proof \
  -l goblin-king.worker=true --no-headers
kubectl get configmaps --namespace gk-validation-proof -o name \
  | grep 'gk-example-hello-' || true
```

## 4. Submit The First Normal Generic Job

```bash
JOB_ID="$(curl --fail-with-body -sS \
  -X POST http://127.0.0.1:8000/jobs \
  -H "Authorization: Bearer local-dev-token" \
  -H "Content-Type: application/json" \
  -d '{"kind":"example.hello","input":{"name":"First Job"},"timeout_seconds":60}' \
  | jq -r .id)"

for attempt in $(seq 1 60); do
  STATUS="$(curl --fail-with-body -sS \
    -H "Authorization: Bearer local-dev-token" \
    "http://127.0.0.1:8000/jobs/${JOB_ID}" | jq -r .status)"
  printf '%s\n' "${STATUS}"
  case "${STATUS}" in
    completed) break ;;
    failed|timed_out|cancelled) exit 1 ;;
  esac
  sleep 1
done

test "${STATUS}" = "completed"
```

Prove the run used the validated kind and returned the expected result:

```bash
curl --fail-with-body -sS \
  -H "Authorization: Bearer local-dev-token" \
  "http://127.0.0.1:8000/runs?kind=example.hello&limit=10" \
  | jq -e --arg job_id "${JOB_ID}" \
      '.items[] | select(.job_id == $job_id) |
       .status == "completed" and
       .result.data.canonical_message == "Hello World"'
```

If the scheduler reports that no current Kubernetes proof exists, compare its identity
in the error with `validations[0].image_digest`; they must be byte-for-byte equal.

## 5. Capture Evidence And Clean Up

Retain these outputs with the change review:

- the validation response with `ok`, identity, checks, logs, and artifact metadata;
- the `/goblins` persisted validation record;
- the completed first job and run result;
- `kubectl get events --namespace gk-validation-proof --sort-by=.lastTimestamp`;
- confirmation that transient validation resources were removed.

Then remove the disposable environment:

```bash
helm uninstall goblin-king --namespace gk-validation-proof
kubectl delete namespace gk-validation-proof --wait=true
kind delete cluster --name gk-validation-proof
rm -f /tmp/goblin-kubernetes-validation.json
```

## Failure Interpretation

- `401` or `403`: use an admin bearer token; ordinary project members cannot execute
  this operation.
- Unknown kind or missing mapping: repair active registry/image-map discovery and retry.
- Image pull failure: preload the configured image or publish the exact reference the
  worker map names.
- Invalid/missing result: inspect the returned bounded `logs` and fix the worker
  container contract.
- Valid failed result with `require_success: true`: either fix the worker input/behavior
  or intentionally set `require_success: false` when a failed envelope is the expected
  contract proof.
- Identity mismatch: make API and scheduler use the same active worker image map;
  digest-pin production references and revalidate after image changes.

## Shared Runtime Configuration Proof

The minimal commands above intentionally use the chart defaults. For deployment proof,
render or install with a digest-pinned control/forwarder image, distinct worker and
forwarder pull policies, and at least one symbolic workload pull Secret. Confirm the API
ConfigMap `kubernetes_runtime` object and scheduler arguments describe those exact
values, then invoke generic validation. The generated validation Pod must contain the
same forwarder identity, policies, and `imagePullSecrets` as normal scheduler Pods.

Deterministic tests also prove that API generic/notebook/repository validation,
scheduler static/dynamic workers, and direct CLI execution all use the shared typed
runtime factory. Pod log reads retain the same 64 KiB per-container and five-second
request bounds. Under `restricted-v1`, the persisted identity also includes the effective
profile and per-kind ServiceAccount decision. A passing legacy identity therefore cannot
authorize restricted execution, and changing a kind's ServiceAccount requires fresh
proof. No independent validation-only configuration path remains.
