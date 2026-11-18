# Optional application ingress and egress

Networking is disabled by default. Installation policy determines the available
Gateways, certificate aliases, hostnames, proxy images and outbound destinations.
Project administrators choose within those bounds from **Repositories / Delivery →
Networking**. Preview shows the normalized configuration and its expected version;
Apply returns a durable operation ID. Inspect and Probe report actual observations,
and Diagnostics explains holds, policy drift, certificate expiry and failed probes.

Application Dockerfiles remain at the repository root, with the repository root
as build context. Networking changes reuse the verified image digest. They do not
require application repository edits or another image build. HTTP clients must
honor the supplied proxy environment variables; approved direct destinations are
separate CIDR/port rules.

## Install the optional package

`examples/pipeline/networking-dependencies.json` pins Envoy Gateway, Envoy and
Squid. `charts/pipeline-networking` installs namespace-scoped permissions, approved
Gateways, certificate references and execution boundaries. Its default values
render no resources. The main chart remains usable with neither networking option.

Install a new dedicated controller with explicit identities:

```powershell
python scripts/pipeline_network_install.py install-controller `
  --context docker-desktop --namespace gk-networking `
  --release gk-networking --gateway-class goblin-king `
  --controller-name goblin-king.io/pipeline-networking `
  --output .runlogs/network-controller
```

This command refuses to adopt an existing release, namespace or GatewayClass. It
preserves compatible existing Gateway/Envoy CRDs and rejects incomplete API sets.
Existing Gateway bindings must name the approved controller and listeners. The
package does not own or silently change those Gateways or their certificates.

Keep installation-specific values outside Git. The policy must include the actual
control-plane namespace and protected node, Pod and Service CIDRs. Built-in
loopback, link-local, metadata, multicast and address-translation exclusions cannot
be removed through project configuration. Private upstream exceptions require an
exact approved hostname and exact addresses; broader private ranges stay denied.

Before enabling ingress, run the capability checks against the intended values:

```powershell
python scripts/pipeline_network_install.py preflight `
  --context docker-desktop --values .runlogs/networking-values.json
```

`serviceType` defaults to `ClusterIP`. Operators may choose `NodePort` or
`LoadBalancer` and approve external exposure for individual releases. Public DNS,
reachable addresses and firewall rules remain operator responsibilities. A local
Docker-network or loopback probe is not proof of public Internet reachability.
Egress proxies are private to their release and are never public forward proxies.
Direct egress does not promise a fixed public source IP.

## Certificates and authority

Each Gateway maps certificate aliases to existing TLS Secrets. Project forms select
aliases and never return certificate private keys. Optional `certificateRequests`
use an already installed cert-manager and entries in `approvedIssuers`; preflight
requires the issuer to exist and be Ready. The package creates Certificates only
for managed Gateways and approved hostnames. It does not install issuers or take
over certificates belonging to existing Gateways.

Ingress checks listener/controller acceptance, HTTPRoute and request-limit policy
status, certificate validity and hostname, application readiness, and a TLS request
through the Gateway. Forwarded headers are normalized at the edge. Request size,
timeout and streaming controls are explicit. HTTPS CONNECT authorizes a destination
and port without intercepting encrypted traffic.

Egress uses a separate Squid instance and NetworkPolicy for each release and
environment. Hostnames and ports must both be approved. Direct outbound attempts,
metadata destinations, other releases' proxies and protected cluster destinations
remain denied. DNS answers are checked against the approved address boundaries;
mixed allowed/protected answers fail closed.

## API, CLI and recovery

Use the same authenticated service through the admin UI or CLI:

```powershell
goblin-king pipeline networking capabilities PROJECT
goblin-king pipeline networking inspect RELEASE_KEY
goblin-king pipeline networking preview RELEASE_KEY configuration.json
goblin-king pipeline networking apply RELEASE_KEY preview.json IDEMPOTENCY_KEY
goblin-king pipeline networking probe RELEASE_KEY EXPECTED_VERSION IDEMPOTENCY_KEY
goblin-king pipeline networking diagnostics PROJECT
```

Save the complete preview response in `preview.json`. A stale preview must be
recreated. Disabling networking uses a preview with both features disabled;
deactivating an environment additionally stops application and scheduled activity
while retaining its data. Neither action can be reversed by a concurrent stale
deployment. Tightened outbound policy closes existing application/proxy connections
before restoring activity under the new rules.

A failed route change can restore the last healthy ingress configuration only if
it remains authorized. The newly requested egress restriction remains in force.
The operation reports failure and preserves desired-versus-observed differences.
First exposure has no invented previous route. Ambiguous interrupted effects retain
ownership and a recovery hold until the previous execution is confirmed terminated.

After correcting the cause, inspect the operation and preview any changed policy
before requesting recovery:

```powershell
goblin-king pipeline recover OPERATION_ID EXPECTED_VERSION IDEMPOTENCY_KEY `
  --networking-preview-id PREVIEW_ID
```

Git recording failures retry recording alone. They do not repeat a successful
network change or deployment. Backups include desired and observed networking
state. Restore starts with delivery held and independently verifies live routes,
certificates, policy and application images before enabling delivery. SQLite still
has one active storage node; this is fenced recovery, not database high availability.

## Complete local acceptance

The networking option extends the delivery harness to 46 mandatory scenarios,
including 63 networking effect-boundary interruptions. Each scenario must pass;
missing or skipped cases fail the overall report. Use two fresh output directories
against the same clean source and immutable image manifest:

```powershell
python scripts/pipeline_acceptance.py prepare --output .runlogs/networking-one `
  --images .runlogs/pipeline-images/images.json --image CONTROLLER_DIGEST `
  --networking --gateway-class goblin-king `
  --controller-name goblin-king.io/pipeline-networking
python scripts/pipeline_acceptance.py run --output .runlogs/networking-one
python scripts/pipeline_acceptance.py report --output .runlogs/networking-one
```

The fixture provider, repositories, review responses and App authentication are
local simulations. No external repository access is required. Controlled upstream
HTTP, HTTPS, DNS, TCP and UDP endpoints run outside Kubernetes. Service, background
and scheduled requests are correlated with exact deployed commits. Credentials,
private keys, installation addresses and raw local evidence remain outside Git.
See [local acceptance](pipeline-local-acceptance.md) for node visibility, reports,
cleanup and the separate Docker/Helm admin audits.
