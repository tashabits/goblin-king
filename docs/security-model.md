# Goblin Security Model

Goblin King uses containers to create a practical boundary around worker dependencies,
filesystems, processes, and runtime configuration. Containers are useful isolation, not
a complete security guarantee by themselves.

## Goals

- Keep goblin dependencies out of the host project process.
- Limit writable paths to contract mounts.
- Make results, artifacts, logs, events, and heartbeats auditable.
- Allow Docker and Kubernetes to apply resource and security controls.
- Preserve project-scoped API access to jobs, runs, artifacts, events, and services.

## Worker Image Practices

Prefer:

- Non-root users.
- Minimal runtime images.
- Read-only root filesystems where possible.
- No package managers or compilers in runtime images unless needed.
- Clear Dockerfiles that can be reviewed quickly.

Avoid:

- Writing secrets to logs, results, events, or artifacts.
- Mounting broad host directories.
- Relying on network access when a goblin can run offline.
- Adding large frameworks to tiny contract examples.

## Runtime Controls

Docker and Kubernetes can enforce some controls directly:

- CPU and memory limits.
- Read-only root filesystem.
- Non-root users.
- Dropped Linux capabilities.
- Network mode or NetworkPolicy.
- PID/process limits where supported.
- Job deadlines or container timeouts.

Goblin King enforces or records:

- Job timeout status.
- Scoped hard-kill of Goblin King-labeled runtime objects.
- Artifact path safety.
- Auth, RBAC, audit, and rate-limit decisions.
- Durable event and heartbeat history.

Per-goblin resource policy is documented in
[`goblin-resource-policies.md`](goblin-resource-policies.md). It defines recommended
defaults, ceilings, Docker mapping, Kubernetes mapping, and proof expectations. Current
runtime enforcement covers timeouts, retries, safe artifact paths, scoped hard
termination, audit, and events; broader CPU, memory, network, filesystem, log, and
artifact byte ceilings should be enforced by Docker/Compose/Helm deployment policy until
runtime-level validation is added.

## Secrets

Pass secrets only through the deployment mechanism chosen by the project, such as Docker
secrets, Kubernetes Secrets, or externally managed secret injection. Goblins should read
only the specific secret values they need and should never echo them into proof output.

## Network

Network access should be deliberate. Some goblins need APIs; many do not. Resource
policies should make network mode or egress intent explicit per goblin.

The King can run chaos. He should not hand chaos the master key.
