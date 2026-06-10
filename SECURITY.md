# Security Policy

Goblin King is project-adoptable alpha software for trusted, self-hosted projects. It is
intended for teams that build or trust their own goblin container images and want a local
control plane for validating, scheduling, and inspecting those workers.

## Supported Use

- Trusted self-hosted development and internal project deployments.
- Contract-compliant Docker/OCI task containers that the project builds or trusts.
- Local Docker Compose and optional Helm deployments where operators control the worker
  images and runtime environment.

## Unsupported Use

- Untrusted third-party container execution.
- Public multi-tenant workloads.
- Production deployment without additional hardening, monitoring, image governance, and
  environment-specific security review.

Goblin contract validation proves that a worker image follows the Goblin King container
contract. It does not prove that the image is safe, trustworthy, or free of malicious
behavior. Run only goblin images you build or trust.

## Docker Socket Safety

The Docker socket is security-sensitive and can grant root-equivalent control of the
Docker host. In the local Docker runtime, the Goblin King control plane may need Docker
socket access to launch task containers. Goblin task containers should not receive the
Docker socket.

Keep Docker socket mounts limited to trusted control-plane services, avoid sensitive
host mounts in worker containers, and do not expose local admin/API services publicly
without proper auth and TLS.

## Reporting Security Issues

Please report security issues privately through GitHub's private vulnerability reporting
or security advisory flow for this repository when available. If that is not available,
contact the repository maintainers directly before opening a public issue.

For the broader threat model and current hardening expectations, see
[docs/security-model.md](docs/security-model.md).

