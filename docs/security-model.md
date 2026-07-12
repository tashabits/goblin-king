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
- Mandatory worker contract validation before container execution by default.
- Auth, RBAC, audit, and rate-limit decisions.
- Durable event and heartbeat history.

Per-goblin resource policy is documented in
[`goblin-resource-policies.md`](goblin-resource-policies.md). It defines recommended
defaults, ceilings, Docker mapping, Kubernetes mapping, and proof expectations. Current
runtime enforcement covers timeouts, retries, safe artifact paths, scoped hard
termination, audit, and events; broader CPU, memory, network, filesystem, log, and
artifact byte ceilings should be enforced by Docker/Compose/Helm deployment policy until
runtime-level validation is added.

Worker image validation is documented in
[Goblin Contract Validation](goblin-contract-validation.md). The scheduler may create
proof just-in-time, but it does not execute a container-backed goblin unless validation
passes for the resolved image identity, contract version, and validator version.

## Secrets

Pass secrets only through the deployment mechanism chosen by the project, such as Docker
secrets, Kubernetes Secrets, or externally managed secret injection. Goblins should read
only the specific secret values they need and should never echo them into proof output.

JupyterHub service tokens are deployment secrets. When Hub-backed auth is enabled, put
the Hub service token in a Docker/Kubernetes Secret or environment source and reference
it with `jupyterhub.service_token_env`; do not store it in `goblin-king-api.json`, Helm
ConfigMaps, audit logs, probe results, or worker output.

Generated Kubernetes worker Pods receive only operator-selected Secret names through
`imagePullSecrets`. The typed runtime settings reject raw credential fields, arbitrary
Pod fragments, and Secret names that are not Kubernetes DNS subdomain names. Registry
username, password, token, and Docker config JSON remain inside Kubernetes Secrets or an
external secret controller. Image-pull diagnostics are bounded before entering durable
Run and event records so registry responses cannot create unbounded stored errors.

## Network

Network access should be deliberate. Some goblins need APIs; many do not. Resource
policies should make network mode or egress intent explicit per goblin.

Service goblins expose HTTP endpoints. Register only trusted project service URLs,
prefer cluster-local DNS, and route user access through Goblin King's service proxy when
project-scoped auth is required. The proxy strips standard auth and cookie headers
before forwarding so user bearer tokens are not handed to service containers by
default.

The Goblin Directory is deployment-local sharing, not a public marketplace. Approval is
not a security certification, and validation proves contract compliance rather than
trustworthiness. Operators remain responsible for trusted runner images, auth mapping,
secrets, resource policy, and deployment boundaries.

The King can run chaos. He should not hand chaos the master key.

## Local Docker socket access

In the Docker Compose development stack, the Goblin King scheduler/control plane may need
Docker socket access so it can launch goblin task containers. Treat that socket as
security-sensitive root-equivalent access to the local Docker host.

Goblin task containers should not receive the Docker socket. Keep the socket mounted only
where the control plane needs it, run only trusted project images, and do not expose the
local admin/API stack publicly without proper auth and TLS.
