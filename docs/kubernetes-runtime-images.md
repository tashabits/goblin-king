# Kubernetes Runtime Images

Goblin King creates a Kubernetes Job for each finite task. Every generated Pod has a
project worker container and a Python result-forwarder sidecar. The worker writes the
standard result file; the forwarder publishes that envelope to Redis without requiring
the worker language to include a Redis client.

Both image identities are operator settings. Neither is inferred from a running Pod,
and a generated workload never receives raw registry credentials or an arbitrary Pod
fragment.

## Helm Defaults

The chart makes the forwarder use the exact control-plane image by default. That remains
true for a legacy repository/tag deployment and for a digest-pinned deployment:

```yaml
image:
  repository: registry.example/tashabits/goblin-king
  tag: "0.1.0"
  digest: sha256:<control-plane-digest>
  pullPolicy: IfNotPresent
```

When `image.digest` is non-empty, the rendered API and scheduler image is
`repository@digest`; `tag` is ignored. The scheduler passes that same immutable
reference to every result-forwarder sidecar.

Leave all `scheduler.resultForwarder.image` fields empty to retain that exact-image
default. To publish the forwarder separately, set its own repository plus tag or digest:

```yaml
scheduler:
  workerImagePullPolicy: IfNotPresent
  resultForwarder:
    image:
      repository: registry.example/tashabits/goblin-king-forwarder
      tag: "0.1.0"
      digest: sha256:<forwarder-digest>
    pullPolicy: IfNotPresent
```

A forwarder digest takes precedence over its tag. If a separate forwarder repository
or tag is supplied without a digest, missing repository/tag fields inherit the matching
control-plane value.

## Private Registries

`image.pullSecrets` continues to configure the chart Pods and is also inherited by
generated worker Pods. It accepts the chart's established map or string forms:

```yaml
image:
  pullSecrets:
    - name: primary-registry

scheduler:
  workloadImagePullSecrets:
    - backup-registry
```

The two lists are combined, de-duplicated in order, and passed to generated Pods as
`imagePullSecrets`. Configure Secret values through Kubernetes or an external secret
controller. These settings accept only Kubernetes Secret names; username, password,
token, Docker config JSON, mount, command, and arbitrary Pod-spec fields are rejected.

## Scheduler And Direct Commands

The same typed settings boundary is used by `scheduler run`, `scheduler run-once`, and
direct Kubernetes submission:

```bash
goblin-king scheduler run \
  --runtime kubernetes \
  --result-forwarder-image registry.example/control@sha256:<digest> \
  --worker-image-pull-policy IfNotPresent \
  --result-forwarder-image-pull-policy IfNotPresent \
  --workload-image-pull-secret primary-registry \
  --kubernetes-runtime-settings /etc/goblin-king/kubernetes-runtime.json

goblin-king jobs submit example.echo \
  --runtime kubernetes \
  --input examples/input.json \
  --result-forwarder-image registry.example/control@sha256:<digest> \
  --workload-image-pull-secret primary-registry

goblin-king workers validate \
  --runtime kubernetes \
  --input examples/input.json \
  --kind example.echo \
  --result-forwarder-image registry.example/control@sha256:<digest> \
  --worker-image-pull-policy IfNotPresent \
  --result-forwarder-image-pull-policy IfNotPresent \
  --workload-image-pull-secret primary-registry
```

Repeat `--workload-image-pull-secret` to attach more than one existing Secret.

The API uses the equivalent additive JSON member for generic, notebook, and repository
validation Jobs:

```json
{
  "kubernetes_runtime": {
    "result_forwarder_image": "registry.example/control@sha256:<digest>",
    "worker_image_pull_policy": "IfNotPresent",
    "result_forwarder_image_pull_policy": "IfNotPresent",
    "workload_image_pull_secret_names": ["primary-registry"]
  }
}
```

All control-plane paths use one typed runtime factory. Generic validation cannot replace
the configured forwarder or pull settings through its HTTP request, and scheduler/API
construction retains the same namespace discovery and bounded diagnostic helpers. The
optional CLI settings file also carries `restricted-v1` and per-kind ServiceAccount
settings into generic proof, using the same identity as scheduled execution.

Older JSON settings files and older constructor calls remain valid. The Python
constructor still accepts `image_pull_policy` and `result_forwarder_image`; when no
separate forwarder policy is supplied, the legacy pull policy applies to both
containers. `from goblin_king.runtime import KubernetesRuntime` remains supported.

## Failure Behavior

The runtime checks generated Pod container states while it waits for a Job. Known image
startup failures such as `ImagePullBackOff`, `ErrImagePull`, `ErrImageNeverPull`, and
`InvalidImageName` immediately produce a bounded failed result naming the Job, Pod,
container, and Kubernetes reason. The Job and input ConfigMap are then cleaned up.

The scheduler records one failed Run and terminal job event for that attempt and keeps
processing later leased jobs. Diagnostic Pod queries have a five-second client timeout,
and the durable error is capped at 500 characters so registry responses cannot create
unbounded run records.

For every ordinary scheduled run that creates a Job, the runtime also captures the
bounded `worker` container log before cleanup and emits `worker.container_logs` before
the terminal worker event. Kubernetes returns a combined stream; the payload therefore
uses `stdout` for that text, an empty `stderr`, and `stream_mode: combined`. Only the
worker container is projected into this event. The `result-forwarder` log may remain in
explicit validation diagnostics, but it is not user worker output.

The retained worker text is capped at the smaller of 64 KiB and the effective
`logs.max_bytes` policy. A one-byte probe makes truncation explicit without reading an
unbounded Pod log. A truncated event sets `byte_count_exact: false` because Kubernetes
cannot report the complete stream length through that bounded read. If the log request
itself fails, the user event contains empty output and also marks the byte count inexact;
cluster transport details remain confined to explicit diagnostics. Job and ConfigMap
deletion happens only after the bounded event has been persisted.

## Verification

Render before applying:

```bash
helm lint charts/goblin-king
helm template goblin-king charts/goblin-king \
  --set-string image.repository=registry.example/control \
  --set-string image.digest=sha256:<control-digest>
```

After deployment, submit a worker whose image map also uses an immutable digest. Inspect
the generated Pod before cleanup (or collect it through cluster events) and confirm the
worker and `result-forwarder` `imageID` values resolve to their configured digests. No
local retag of `goblin-king:local` should be necessary.

See [issue #146 proof](proofs/issue-146-kubernetes-forwarder-images.md) for automated
coverage, commands, and the live-cluster proof boundary.

For generated Pod credentials, security contexts, and both-container resource controls,
continue with [Kubernetes Workload Security](kubernetes-workload-security.md).
