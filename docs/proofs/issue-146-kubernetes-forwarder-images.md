# Issue 146: Kubernetes Forwarder Image Proof

## Scope

This pass proves configuration propagation, compatibility, failure behavior, and Helm
rendering for digest-pinned worker and result-forwarder images.

## Automated Evidence

Run from the repository root:

```bash
python -m ruff check .
python -m pytest -q
helm lint charts/goblin-king
python -m pytest \
  tests/test_kubernetes_runtime.py \
  tests/test_kubernetes_configuration.py \
  tests/test_kubernetes_failure_lifecycle.py \
  tests/test_kubernetes_api_settings.py \
  tests/test_helm_runtime_images.py \
  tests/test_validation.py -q
```

The focused suite observed 25 passing tests before the upstream rebase. It covers:

- the established runtime import and legacy constructor/pull-policy behavior;
- separate worker and forwarder pull policies and immutable image references;
- Secret-name de-duplication, DNS-name validation, and rejection of credential/raw-Pod
  fields;
- scheduler static and dynamic worker adapters, direct submission, and API settings;
- both in-cluster API validation routes receiving the same settings instance;
- a bounded `ImagePullBackOff` failure, Job and ConfigMap cleanup, one terminal failed
  job event, and successful execution of the next scheduler job;
- legacy Helm tag rendering, map/string pull-secret compatibility, digest precedence,
  exact control-image inheritance, and separate forwarder overrides.

## Rendered Identity Check

```bash
helm template issue146 charts/goblin-king \
  --set-string image.repository=registry.example/control \
  --set-string image.tag=ignored \
  --set-string image.digest=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  --set image.pullSecrets[0].name=registry-main \
  --set-string scheduler.resultForwarder.image.repository=registry.example/forwarder \
  --set-string scheduler.resultForwarder.image.digest=sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb \
  --set-string scheduler.workerImagePullPolicy=Never \
  --set-string scheduler.resultForwarder.pullPolicy=Always \
  --set-string scheduler.workloadImagePullSecrets[0]=registry-backup
```

The automated render assertion verifies the scheduler control image is
`registry.example/control@sha256:aaaa...`, its forwarder argument is
`registry.example/forwarder@sha256:bbbb...`, both policies are preserved, and both
Secret names reach the scheduler/API workload settings.

## Live-Cluster Boundary

This isolated implementation pass did not publish images or mutate a shared Kubernetes
cluster. A live acceptance run still requires accessible immutable control/worker image
digests and existing registry Secret names. The final integrator should install those
values, submit one validated task, and capture:

```bash
kubectl get pod -l goblin-king.worker=true \
  -o jsonpath='{range .items[*].status.containerStatuses[*]}{.name}{"\t"}{.image}{"\t"}{.imageID}{"\n"}{end}'
```

Expected evidence is one worker and one `result-forwarder` resolved to the configured
immutable identities without a local compatibility tag. If the current validation gate
has no Kubernetes proof for that worker, create the proof first; that bootstrap concern
is tracked separately from forwarder image configuration.
