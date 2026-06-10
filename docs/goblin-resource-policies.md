# Goblin Resource Policies

Resource policies describe the limits and runtime expectations for each goblin kind.
They are the bridge between the Goblin Container Contract and the deployment platform
that actually runs the worker container.

Phase 33 established the policy model and deployment mappings. Phase 34 adds runtime
loading, ceiling validation, queue-time rejection, persisted effective-policy proof,
Docker option mapping, Kubernetes Job resource mapping, artifact count/byte checks where
the runtime can inspect artifacts, events, audit records, and admin/API/CLI visibility.
Secret allow-lists and deeper cluster admission policies remain deployment-specific
follow-up work.

The King is generous with work. He is not generous with unbounded work.

## Goals

- Give each goblin kind explicit resource expectations.
- Keep worker limits reviewable next to registry and image-map configuration.
- Map one policy model onto Docker, Docker Compose, and Kubernetes/Helm.
- Enforce supported limits without changing the worker contract.
- Make operator proof clear when a goblin is intentionally constrained.

## Policy Shape

A project may keep policies in a dedicated file such as
`goblin-resource-policies.json`, or fold equivalent metadata into deployment-specific
values. The recommended shape is:

```json
{
  "version": 1,
  "defaults": {
    "timeout_seconds": 60,
    "max_retries": 0,
    "cpu": {
      "request": "100m",
      "limit": "500m"
    },
    "memory": {
      "request": "64Mi",
      "limit": "256Mi"
    },
    "filesystem": {
      "read_only_root": true,
      "artifact_max_bytes": 10485760
    },
    "network": {
      "mode": "none"
    },
    "logs": {
      "max_bytes": 1048576
    },
    "concurrency": {
      "max_running": 2
    }
  },
  "goblins": {
    "example.hello-go": {
      "timeout_seconds": 30,
      "cpu": {
        "request": "50m",
        "limit": "250m"
      },
      "memory": {
        "request": "32Mi",
        "limit": "128Mi"
      }
    },
    "example.behavior-artifact": {
      "filesystem": {
        "read_only_root": true,
        "artifact_max_bytes": 52428800,
        "artifact_max_files": 20
      }
    }
  },
  "ceilings": {
    "timeout_seconds": 3600,
    "cpu": {
      "limit": "2"
    },
    "memory": {
      "limit": "2Gi"
    },
    "filesystem": {
      "artifact_max_bytes": 1073741824
    },
    "concurrency": {
      "max_running": 25
    }
  }
}
```

Adopter project configs can also carry a lighter project-owned default profile:

```json
{
  "apiVersion": "goblin-king/v1alpha1",
  "kind": "GoblinProject",
  "defaults": {
    "resources": {
      "timeout_seconds": 60,
      "memory": {"limit": "256Mi"},
      "filesystem": {"read_only_root": true},
      "network": {"mode": "none"}
    }
  },
  "goblins": {
    "myproject.hello": {
      "image": "myproject-hello:local",
      "context": "workers/myproject.hello",
      "resources": {
        "timeout_seconds": 30
      }
    }
  }
}
```

`defaults.resources` uses the same field names as a resource policy. During project
loading it is deep-merged into each inline goblin's `resources`; the goblin override wins
for fields it names. When a project is supplied to API, CLI, or scheduler flows, the
effective runtime policy is resolved as:

```text
effective policy = merge(operator defaults, project defaults, goblin override)
```

The final effective policy is persisted on the job and run, shown through API/CLI/admin
surfaces, and validated against configured ceilings. If `goblin-resource-policies.json`
is present beside the project config or supplied to the runtime command, the project
defaults and per-goblin overrides are validated against those ceilings.

## Policy Fields

| Field | Meaning |
| --- | --- |
| `timeout_seconds` | Maximum runtime before the scheduler/runtime marks the job timed out. |
| `max_retries` | Maximum retry attempts for scheduler-managed failures. |
| `cpu.request` | Scheduling hint for expected CPU. Maps naturally to Kubernetes requests. |
| `cpu.limit` | Hard or soft CPU ceiling depending on runtime. |
| `memory.request` | Scheduling hint for expected memory. Maps naturally to Kubernetes requests. |
| `memory.limit` | Maximum memory before the platform may terminate the container. |
| `process.pids_limit` | Maximum process count where Docker or the platform supports it. |
| `network.mode` | `none`, `default`, or a named project network profile. |
| `filesystem.read_only_root` | Whether the worker image should run with a read-only root filesystem. |
| `filesystem.tmpfs` | Writable temporary paths that may be mounted by the platform. |
| `filesystem.artifact_max_bytes` | Maximum artifact bytes a run should produce. |
| `filesystem.artifact_max_files` | Maximum artifact count a run should produce. |
| `logs.max_bytes` | Maximum captured log bytes to preserve for proof/debugging. |
| `concurrency.max_running` | Maximum active jobs of this kind at one time. |

Unknown fields are rejected. That makes typos visible before a project accidentally runs
with a policy the operator did not actually enforce.

## Defaults And Ceilings

Policies should have two layers:

- **Defaults**: the normal limits used when a goblin kind does not override them.
- **Ceilings**: maximum values a goblin kind may request.

Defaults keep small examples safe. Ceilings keep a single registry change from turning
one goblin into the whole kingdom's appetite.

Goblin King rejects any effective policy above the configured ceiling before it queues or
launches work. Rejections create audit and event records so operators can see exactly why
the job was refused.

For project-defined goblins, `defaults.resources` is an adopter-facing source of default
resource fields. It does not replace ceilings: ceilings remain the guardrail from the
resource policy file. Treat project defaults as the team's desired baseline and ceilings
as the operator's maximum allowed envelope.

Per-goblin overrides belong in the goblin's `resources` block or in the operator policy
file's `goblins.<kind>` entry. They should be used only for real exceptions, such as a
report renderer that needs more memory or an introspection goblin that should run with a
shorter timeout.

## Docker Mapping

Docker-backed workers can map policies to container options:

| Policy | Docker mapping |
| --- | --- |
| CPU limit | `--cpus` or CPU quota options. |
| Memory limit | `--memory`; optionally `--memory-swap` for stricter deployments. |
| PID limit | `--pids-limit`. |
| Network disabled | `--network none`. |
| Named network profile | `--network <name>` from the deployment. |
| Read-only root | `--read-only`. |
| Temporary writable paths | `--tmpfs <path>`. |
| Artifact/result paths | Existing Goblin King bind mounts. |
| Runtime ownership | Existing Goblin King labels for safe scoped termination. |

Docker Compose deployments should put stable defaults in service definitions and use
image-map or policy metadata to document per-goblin exceptions. Compose does not provide
all of Kubernetes' scheduling semantics, so proof should focus on the limits Docker can
actually enforce locally.

## Kubernetes And Helm Mapping

Kubernetes-backed deployments can map policies onto Job, Pod, Deployment, and Service
settings:

| Policy | Kubernetes mapping |
| --- | --- |
| CPU and memory requests | `resources.requests`. |
| CPU and memory limits | `resources.limits`. |
| Timeout | `activeDeadlineSeconds` for Jobs, plus Goblin King timeout status. |
| Retries | `backoffLimit` for Kubernetes Jobs and Goblin King retry metadata. |
| Read-only root | `securityContext.readOnlyRootFilesystem`. |
| Non-root runtime | `securityContext.runAsNonRoot` and user/group IDs. |
| Network profile | `NetworkPolicy` and service account rules. |
| Artifact storage | PVC-backed artifact root and cleanup policy. |
| Long-running services | Deployment resources, readiness/liveness probes, and Service exposure. |
| Completion cleanup | `ttlSecondsAfterFinished` where supported. |

The Helm chart should keep Kubernetes resource policy optional and configurable. Projects
with existing cluster policy may turn off Goblin King-specific defaults and supply their
own admission controls, quotas, or network policies.

## Current Enforcement

The current implementation enforces or records these controls:

- Loading `goblin-resource-policies.json` for API, CLI, scheduler, Docker, and Helm.
- Loading `defaults.resources` from project config for inline project goblin discovery
  and validation visibility.
- Effective policy resolution from operator defaults plus project defaults plus
  per-goblin overrides.
- Ceiling validation before API queueing, fanout, retry, schedule creation, and scheduler
  materialization.
- Job `timeout_seconds` and scheduler/runtime timeout outcomes.
- Job `max_retries` and retry status transitions.
- Persisted effective policy metadata on jobs and runs.
- API, CLI, and admin run-detail visibility for effective policy data.
- Audit and event records for policy validation failures.
- Docker mappings for CPU, memory, PID, network, read-only root, tmpfs, and log byte
  options where Docker supports those flags.
- Kubernetes Job mappings for CPU/memory requests/limits, read-only root filesystem, and
  active deadline timeouts.
- Artifact count and byte ceilings after worker completion when artifact metadata or local
  artifact files are available.
- Per-kind scheduler concurrency deferral for leased/running jobs.
- Scoped hard termination for Docker and Kubernetes runtime objects created and labeled
  by Goblin King.
- Safe artifact path serving under the configured artifact root.
- Project-scoped auth, audit, events, and rate limits.
- Docker/Helm deployment configuration for resource requests, limits, volumes, services,
  ingress, and security settings.

The following remain deployment-specific or future hardening targets:

- Secret allow-lists.
- Network egress allow-lists beyond Docker network mode and Kubernetes NetworkPolicy.
- Cloud/provider admission controls, quotas, and policy engines.
- Object-storage quota enforcement outside the local volume/PVC artifact path.

## Validation And Proof

Resource-policy proof should include the same local-first evidence used throughout the
project:

```bash
python -m pytest
python -m ruff check .
goblin-king resource-policies inspect example.hello --policies examples/resource-policy-proof.json
goblin-king workers validate --registry examples/cross-language-goblins.json --images examples/cross-language-images.json --build --require-success
helm template goblin-king charts/goblin-king
```

Policy-aware validation should also prove:

- A goblin with an allowed policy launches successfully.
- A goblin above global ceilings is rejected before execution.
- Timeout policy creates a `timed_out` run when exceeded.
- Docker command construction includes expected CPU, memory, network, and filesystem
  options.
- Helm rendering maps policy fields to resources, security context, network policy, and
  storage values.
- API, CLI, admin, audit, and event surfaces show the effective policy used for a run.

## Worker Author Guidance

Goblin authors should:

- Keep resource needs small and explicit.
- Document why a worker needs network access, secrets, high memory, or long timeouts.
- Write artifacts only under `GOBLIN_ARTIFACT_ROOT`.
- Write the result only to `GOBLIN_RESULT_PATH`.
- Handle termination signals where the language/runtime supports it.
- Avoid long-running work in one-shot goblins unless the timeout policy is intentional.
- Prefer separate long-running service workers when repeated probes or streaming status
  are the actual requirement.

## Non-Goals

This policy guide does not add:

- Cloud-provider-specific managed service recipes.
- A public policy language or external policy engine.
- Automatic resource tuning.
- Native Kubernetes WASI scheduling.
- Object storage providers beyond the current volume/PVC model.

Those can be added later without changing the container contract: a goblin still reads
input/context, writes result/artifacts, emits optional heartbeats/events, and exits.
