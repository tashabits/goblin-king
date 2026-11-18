# Networking implementation checkpoint

I have integrated the networking lifecycle and am completing the required cluster
acceptance. This checkpoint does not claim the finish line in
[the plan](pipeline-networking-plan.md). Installation and recovery commands are in
[the operating guide](pipeline-networking.md).

## Included

- Typed, default-disabled networking configuration with installation-owned
  gateway, certificate, hostname, port, and destination approvals.
- Versioned configuration, previews, route conflict detection, durable operations,
  authenticated API and CLI actions, and the admin networking form.
- Gateway API routes, per-release Squid configuration, restrictive network
  policies, certificate checks, retained reconciliation evidence, and recovery holds.
- An optional networking chart, pinned dependency manifest, and local development
  preparation and capability checks.
- Networking phases around deployment, activation, deactivation, rollback and
  recording, with confirmed termination before interrupted execution is replaced.
- Protected destination checks, independent probes, diagnostics, connection
  revocation and currently authorized route fallback.
- A 46-scenario acceptance harness, including 63 networking effect-boundary
  interruptions, a separate upgrade fixture, storage reconciliation and headless
  admin audits.

## Evidence and limits

I exercised HTTPS through a real Envoy Gateway NodePort from the Docker network,
an allowed HTTP proxy destination, denied hostname and metadata destinations,
and denied direct outbound access on the existing Docker Desktop kind cluster.
These are development capability checks, not complete networking acceptance.

The Kubernetes client compatibility issue and Helm field-ownership conflict are
fixed. Real deployments and off/on operations completed with retained images and
data. Later development checks passed for all four external TLS routes, denied
outbound boundaries, DNS rebinding/mixed answers, certificate expiry and rotation,
request limits, WebSockets, live connection revocation and dependency outages.

The networking form passed an isolated headless UI audit, with final state compared
against the API and CLI. A real HTTP-origin failure in request ID generation is
fixed and covered by a frontend regression test. The live route-failure check
restored healthy ingress without widening newly restricted egress. First exposure
retained an accurate hold for an invalid certificate and recovered after repair.
An independent installation upgraded from `437d2ca3` with its application and data
preserved and networking disabled.

The latest complete Python run at `39a8a6d3` passed 770 tests with six
platform-specific skips. Those skips require the Linux supplement in final
acceptance. Frontend tests and the production build passed after the request ID
fix. Newer harness changes still require validation at the final combined revision.

Both complete Docker and Helm admin audits passed at `492769ee`, covering all 23
one-shot kinds, artifacts, events, heartbeats and service probes in headless
containers. All four fixture revisions also produced HTTP and verified HTTPS
activity from services, background processes and schedules. A localhost probe
needed an explicit direct connection because the application environment enables
the outbound proxy.

Interruption selection now distinguishes nested networking from deployment
execution. The deliberately ambiguous absent-Job case uses the isolated upgrade
fixture and retains its hold; it cannot prevent the main fixture's remaining
networking checks and cleanup.

The existing delivery proof at `437d2ca3` remains separate from this networking
checkpoint. Local provider simulation is not evidence of real GitHub App
installation or independently reviewed fixture merges. Credentials, local
addresses, generated certificates, and run logs remain outside Git.

## Remaining work

1. Complete cluster validation of the full interruption matrix, optional package
   combinations, existing Gateway ownership, node loss and fenced restoration.
2. Reproduce background and scheduled HTTP/HTTPS activity and the complete
   Docker/Helm admin audits in each final acceptance run.
3. Pass the mandatory cluster suite twice in fresh namespaces at
   one pinned revision, including failures, restart, upgrade, and storage recovery.
4. Merge through PRs, package the verified installation, update the retained demonstration, and
   publish the proof report and recovery instructions.

I keep networking disabled by default while this work remains incomplete.

## Integration validation

I validated the implementation at `a8064cf7` before integration:

- Full Python suite: 715 passed, 6 platform-dependent skips.
- Linux filesystem and networking subset: 58 passed in a container against the
  checked-out source, including the filesystem cases unavailable on Windows.
- Ruff: passed.
- Frontend: 14 tests passed; TypeScript and production build passed.
- Helm: all three charts passed lint; the service chart used an immutable image
  digest, and the networking chart rendered no resources with its default values.

These checks do not replace the outstanding full networking cluster acceptance.
The before-integration release is `v0.1.0-alpha.7` at
`4a5ebe36060487e02b97a4d83cdf02560b670dd0`.
