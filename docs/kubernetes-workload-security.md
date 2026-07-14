# Kubernetes Workload Security

Generated Kubernetes Jobs support two versioned workload-security profiles. `legacy`
remains the default so existing adopters keep the exact manifest and runtime behavior
they had before this feature. `restricted-v1` is an explicit secure migration path for
operators who can verify their worker images and resource policies against a non-root,
read-only contract.

## Profile Contract

`legacy` adds no explicit token-control, security-context, service-account, or
forwarder-resource fields and therefore preserves Kubernetes/default
ServiceAccount-token behavior. Existing constructor defaults, CLI defaults, statuses,
result envelopes, and worker behavior remain unchanged.

`restricted-v1` adds this complete contract:

- `automountServiceAccountToken: false` on every generated Pod;
- pod-level non-root UID/GID, `fsGroup`, and `RuntimeDefault` seccomp;
- worker and result-forwarder container contexts with non-root UID/GID,
  `allowPrivilegeEscalation: false`, `privileged: false`, read-only root,
  `RuntimeDefault` seccomp, and all Linux capabilities dropped;
- complete CPU and memory requests/limits for both containers;
- only the result volume mounted writable in the result forwarder when retention is disabled;
- when retention is enabled, the forwarder additionally receives the transient artifact source
  read-only and only the configured PVC artifact subpath writable, while the worker never receives
  the PVC;
- no arbitrary Pod fragment, credential value, command, volume, capability, or security
  context accepted from configuration.

The default restricted IDs are `65532:65532` with `fsGroup: 65532`. Worker resources
default to `100m`/`1` CPU and `64Mi`/`512Mi` memory request/limit. Forwarder resources
default to `10m`/`100m` CPU and `64Mi`/`128Mi` memory. The forwarder memory floor is
versioned with `restricted-v1`: the packaged retention module reproducibly exceeded the former
64 MiB ceiling, while the same Pod completed at a 64 MiB request and 128 MiB limit.

Mount composition happens before the restricted security profile is applied. A retention-enabled
forwarder therefore keeps `readOnlyRootFilesystem: true` while writing only through its result
volume and narrow PVC subpath; `/artifacts` remains a read-only source mount. The legacy profile
with retention disabled keeps the established inline forwarder command and original mount shape.

## Helm Migration

Enable the profile and align the resource policy in one values change:

```yaml
scheduler:
  workloadSecurity:
    profile: restricted-v1
    restricted:
      runAsUser: 65532
      runAsGroup: 65532
      fsGroup: 65532
      workerResources:
        cpuRequest: 100m
        cpuLimit: "1"
        memoryRequest: 64Mi
        memoryLimit: 512Mi
      resultForwarderResources:
        cpuRequest: 10m
        cpuLimit: 100m
        memoryRequest: 64Mi
        memoryLimit: 128Mi
      workerServiceAccounts: {}

resourcePolicies:
  defaults:
    filesystem:
      read_only_root: true
```

The bundled resource-policy default remains `read_only_root: false` for legacy
compatibility. Enabling `restricted-v1` without changing it intentionally fails the Job
before creation with `restricted-v1 rejects filesystem.read_only_root=false`. This is a
visible conflict, not a silent override. Per-kind resource policies may still supply CPU
and memory fields; those fields merge over the restricted worker defaults and continue
to pass through the existing operator ceiling validation.

Render and inspect before applying:

```bash
helm lint charts/goblin-king
helm template goblin-king charts/goblin-king \
  --set-string scheduler.workloadSecurity.profile=restricted-v1 \
  --set resourcePolicies.defaults.filesystem.read_only_root=true
```

## Direct And Scheduler Configuration

The chart mounts `/config/goblin-kubernetes-runtime.json` and passes it through the
additive `--kubernetes-runtime-settings` option. The file is merged over the established
image and pull-secret flags, then validated as `KubernetesRuntimeSettings`.

A direct deployment can use the same partial JSON:

```json
{
  "workload_security_profile": "restricted-v1",
  "restricted_workload": {
    "run_as_user": 65532,
    "run_as_group": 65532,
    "fs_group": 65532,
    "worker_resources": {
      "cpu_request": "100m",
      "cpu_limit": "1",
      "memory_request": "64Mi",
      "memory_limit": "512Mi"
    },
    "result_forwarder_resources": {
      "cpu_request": "10m",
      "cpu_limit": "100m",
      "memory_request": "64Mi",
      "memory_limit": "128Mi"
    },
    "worker_service_account_names": {}
  }
}
```

```bash
goblin-king scheduler run \
  --runtime kubernetes \
  --kubernetes-runtime-settings /config/goblin-kubernetes-runtime.json
```

The same nested members are accepted under `kubernetes_runtime` in the API settings
file. Unknown or relaxation-shaped fields are rejected because every settings model
uses `extra="forbid"`.

## Explicit Worker Service Accounts

Most tasks should not receive Kubernetes API credentials. A kind that genuinely needs
cluster access may be mapped to one pre-existing, narrowly scoped ServiceAccount:

```yaml
scheduler:
  workloadSecurity:
    profile: restricted-v1
    restricted:
      workerServiceAccounts:
        project.cluster-reader: goblin-project-reader
```

Configuration contains only kind and ServiceAccount names. It never contains tokens,
kubeconfig, passwords, client certificates, or registry credentials. The Pod still has
`automountServiceAccountToken: false`. For the named kind only, Goblin King sets
`serviceAccountName` and creates a one-hour projected token/CA/namespace volume mounted
only in the worker container. The result forwarder never receives that mount. RBAC and
ServiceAccount creation remain explicit operator responsibilities.

## Validation And Proof Identity

Restricted validation identities hash the effective profile, UID/GID, security
contexts, resources, and per-kind ServiceAccount decision. A legacy proof cannot
authorize a restricted Job, and changing the restricted contract makes the prior proof
stale. Durable validation records expose the effective fields under
`effective_policy.kubernetes_workload_security` without changing the validation result
or Run envelope.

Whenever artifact retention is enabled, both legacy and restricted scheduler identities also hash
the normalized PVC claim, subdirectory, URI root, and forwarder mount path. Changing that storage
boundary makes prior proof stale. With retention disabled, the legacy identity remains exactly
`kubernetes:<image>` for backward compatibility.

## Adoption Checklist

1. Confirm the worker and forwarder images can run as the configured non-root IDs.
2. Confirm they write only to mounted result/artifact paths and that the artifact root is group-owned
   by the configured `fsGroup` with directory mode `02770`.
3. Set resource-policy `read_only_root: true` and keep CPU/memory within ceilings.
4. Render Helm and inspect the runtime settings JSON.
5. Validate each worker again because the restricted identity differs from legacy.
6. Run one task and inspect the real Pod for token volumes, contexts, mounts, resources,
   exit codes, and resolved image IDs.
7. Add per-kind ServiceAccounts only after reviewing their RBAC.

See [issue #148 proof](proofs/issue-148-kubernetes-workload-security.md) for the exact
automated and live-cluster evidence.

## Notebook-Authored Service Pods

Notebook-authored ASGI services use a fixed restricted manifest independently of the
scheduler Job profile. Their generated Kubernetes Deployment keeps the existing labels,
ConfigMap bundle, Service, runtime selection, and lifecycle API while applying these
non-negotiable defaults:

- automatic ServiceAccount-token mounting is disabled;
- the pod runs as UID/GID and `fsGroup` 65532 with `RuntimeDefault` seccomp;
- the service container cannot escalate privileges, is explicitly unprivileged, drops
  every Linux capability, and uses a read-only root filesystem;
- CPU/memory requests are `100m`/`64Mi` and limits are `1`/`512Mi`;
- the source bundle remains read-only; and
- one size-limited `emptyDir` is mounted at `/tmp` for dependency installation and
  application scratch data.

The runner installs declared Python requirements into
`/tmp/goblin-service-runtime`, and `PYTHONPATH` points to that location. Existing
service source and dependency declarations therefore retain their established loading
contract without requiring writes to the image filesystem. Application code that needs
scratch space must use `/tmp`; it does not receive a ServiceAccount credential or an
arbitrary volume escape hatch.
