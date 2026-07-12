# Issue 146: Kubernetes Forwarder Image Proof

Tested implementation: `5157af5` (rebased onto upstream `6e1118e`)

Proof date: 2026-07-12

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

The focused suite observed 25 passing tests before the upstream rebase. After rebasing
onto upstream main at `6e1118e`, the final repository gates observed:

- `python -m ruff check .`: passed;
- `python -m pytest -q`: 349 passed in 167.50 seconds;
- `helm lint charts/goblin-king`: one chart passed with only the existing optional-icon
  recommendation;
- `git diff --check`: passed with a clean worktree.

The focused coverage includes:

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

## Live kind Proof

The publishing review ran the chart on 2026-07-12 in the disposable kind cluster
`goblin-king-upstream-proof`, namespace `issue146-proof`. The exact branch images were
built, loaded into the kind node, and registered there by their OCI manifest digests.
The chart used the digest-qualified control-plane identity below with pull policy
`Never`; no `goblin-king:local` compatibility tag was created:

- control plane and result forwarder:
  `ghcr.io/tashabits/goblin-king@sha256:f75416368f2d1755ad096a8b106a55d913b4af881ef32be4b873dbe6339ac9eb`;
- worker:
  `ghcr.io/tashabits/goblin-king-example-echo@sha256:207ca3a6318ee111fdf992d5cbdcb88faf4a0776dd44970b5f94c00367856623`.

Helm reported revision 1 as `deployed`. The API, scheduler, and Redis Deployments each
reached one ready replica. The scheduler command contained the exact forwarder digest,
separate `Never` worker/forwarder policies, and the configured
`issue146-registry` Secret name.

The first task completed successfully as run
`12b479a9-0888-4ca5-8efd-374acc4846d5`. A Kubernetes watch captured the generated Pod
before cleanup. Its two requested images and resolved image IDs were identical:

```text
worker           ghcr.io/tashabits/goblin-king-example-echo@sha256:207ca3a6318ee111fdf992d5cbdcb88faf4a0776dd44970b5f94c00367856623
result-forwarder ghcr.io/tashabits/goblin-king@sha256:f75416368f2d1755ad096a8b106a55d913b4af881ef32be4b873dbe6339ac9eb
imagePullSecrets issue146-registry
status           completed / success
```

The failure proof then supplied a nonexistent digest-qualified forwarder image. Run
`40fd87ae-55b5-4990-8738-7daf2d60dd7f` failed in 3,195 ms with a bounded message naming
the Job, Pod, `result-forwarder` container, and `ErrImagePull`. Three seconds later no
generated worker Pod or input ConfigMap remained.

Finally, recovery run `db78b250-2641-43b1-8dc2-602f79ddde52` used the valid digest
again and completed successfully. The API and scheduler remained ready, and no transient
Job or worker ConfigMap remained. This proves a pull failure is contained to its run and
does not prevent the next task from completing.
