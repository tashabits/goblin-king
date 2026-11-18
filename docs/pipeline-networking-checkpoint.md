# Networking implementation checkpoint

I am preserving the optional ingress and egress implementation in Git before
continuing its integration and acceptance testing. This checkpoint does not
claim the networking finish line in [the plan](pipeline-networking-plan.md).

## Included

- Typed, default-disabled networking configuration with installation-owned
  gateway, certificate, hostname, port, and destination approvals.
- Versioned configuration, previews, route conflict detection, durable operations,
  authenticated API and CLI actions, and the admin networking form.
- Gateway API routes, per-release Squid configuration, restrictive network
  policies, certificate checks, retained reconciliation evidence, and recovery holds.
- An optional networking chart, pinned dependency manifest, and local development
  preparation and capability checks.
- Initial release-phase integration scaffolding. It is not yet connected to the
  complete deployment lifecycle.

## Evidence and limits

I exercised HTTPS through a real Envoy Gateway NodePort from the Docker network,
an allowed HTTP proxy destination, denied hostname and metadata destinations,
and denied direct outbound access on the existing Docker Desktop kind cluster.
These are development capability checks, not complete networking acceptance.

The first scheduled networking operation stopped in a recovery hold before
changing network resources because Kubernetes client versions used different
response arguments. The compatibility fix and its regression test are included;
the held operation still needs recovery with an updated image.

The existing delivery proof at `437d2ca3` remains separate from this networking
checkpoint. Local provider simulation is not evidence of real GitHub App
installation or independently reviewed fixture merges. Credentials, local
addresses, generated certificates, and run logs remain outside Git.

## Remaining work

1. Complete and test networking phases around deployment, activation,
   deactivation, rollback, and recording, including termination checks for nested
   execution and recovery after interruption.
2. Rebuild pinned images and recover the held development operation through the
   authenticated API after verifying termination.
3. Complete controlled external fixtures, destination and identity boundary
   tests, connection revocation, certificate rotation, diagnostics, and UI audit.
4. Extend the mandatory cluster suite and pass it twice in fresh namespaces at
   one pinned revision, including failures, restart, upgrade, and storage recovery.
5. Package the verified installation, update the retained demonstration, and
   publish the proof report and recovery instructions.

I keep networking disabled by default while this work remains incomplete.
