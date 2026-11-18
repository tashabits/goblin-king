# Optional ingress and egress delivery plan

This plan defines the networking scope and completion criteria. It extends the
local delivery proof at `437d2ca3debcdc6438dd15568fc62f4d2cc69ba6` with ingress and
egress on Docker Desktop's existing two-node kind cluster. I keep the current
demonstration available until its replacement passes acceptance.

See [installation and operation](pipeline-networking.md) for the implemented
interfaces and [the development checkpoint](pipeline-networking-checkpoint.md) for
historical validation. Release-specific acceptance reports identify the exact
verified revision, completed scenarios and limits. This plan and its development
checkpoint do not themselves claim complete networking acceptance.

## Finish line

I can install Goblin King with neither capability, either capability, or both.
From the authenticated admin UI I can configure each application's test and
production ingress and egress, preview the effect, apply a versioned change, and
inspect independently observed results. The API and CLI provide the same actions.

Both local fixture applications must complete the delivery lifecycle with the
networking options enabled, through configuration alone. A fresh installation and
an upgrade must also preserve the current behavior when both options are disabled.
The full delivery suite, extended with the scenarios below, must pass twice in
fresh namespaces against the same pinned implementation. Failed or skipped required
scenarios prevent completion.

The local proof will exercise access from outside Kubernetes through the real
gateway. It will not label loopback or Docker-network reachability as public
Internet reachability. Public DNS, a reachable address, firewall configuration,
and a certificate remain installation prerequisites for Internet exposure.

## Scope and implementation choices

| Capability | Initial supported behavior |
| --- | --- |
| Ingress | HTTP and HTTPS; exact hostnames; exact or prefix paths; TLS termination; HTTP-to-HTTPS redirect; WebSocket upgrades; bounded request timeouts and request sizes |
| Managed ingress | Package a pinned Envoy Gateway release and compatible stable Gateway API resources; use dedicated test and production Gateways |
| Existing ingress | Bind to an operator-approved existing Gateway and listeners; validate required capabilities before enabling a route |
| Egress proxy | Package a pinned Squid forward proxy per release/environment, with exact hostname and port allowlists for HTTP and HTTPS CONNECT |
| Direct egress | Optional operator-approved destination CIDRs and TCP/UDP ports, enforced by NetworkPolicy; useful for dependencies that cannot use an HTTP proxy |
| Certificates | Reference approved Kubernetes TLS Secrets; optionally reference Certificates issued by an already installed cert-manager with approved issuers |
| Exposure | Internal by default; explicit NodePort or LoadBalancer exposure within the installed policy; local demonstration uses a loopback-only entry point |

Envoy Gateway provides HTTPRoute-based routing. Squid provides explicit forward
proxy access controls. Standard Kubernetes NetworkPolicy provides IP/port
enforcement, not a hostname firewall. These are complementary protections:
[Envoy HTTP routing](https://gateway.envoyproxy.io/docs/tasks/traffic/http-routing/),
[Squid access controls](https://www.squid-cache.org/Doc/config/http_access/), and
[Kubernetes NetworkPolicy](https://kubernetes.io/docs/concepts/services-networking/network-policies/).

I will select compatible maintained component versions during the capability
spike, pin chart versions and container digests, retain their license notices, and
record the compatibility matrix. I will not replace kindnet or reset the cluster.
Existing Gateway support is conditional on the declared capability checks; this
is not a promise of compatibility with every controller or custom annotation.

HTTPS CONNECT authorizes a destination and port, not URL paths or encrypted
content. There is no TLS interception. Applications must use a proxy-aware client;
ignoring proxy configuration must fail closed unless a direct rule was explicitly
approved. Arbitrary TCP/UDP proxying, automatic fixed public source IPs, transparent
service-mesh interception, automatic cloud/firewall provisioning, and public
forward-proxy access are outside this version. Direct CIDR rules do not establish
a fixed-source-IP guarantee.

## Package ownership and authority

I will add a networking package with an installation policy and dedicated
namespace-scoped runtime permissions. The privileged installation step installs
CRDs, dependency controllers, required Roles, allowed namespaces, GatewayClasses,
hostname suffixes, exposure modes, certificate references, and destination bounds.
Existing controllers and CRDs are discovered and never silently adopted, replaced,
or removed.

The UI can enable, disable, and configure capabilities within that installed
policy. If the installation omitted a required capability, the UI shows the exact
package change needed; it does not acquire cluster-admin authority to install it.
Cluster-level permissions and the permitted exposure ceiling stay installation
owned. Project administrators select only approved options for their projects.

The application chart continues to create internal ClusterIP Services. Networking
resources use a separate approved renderer and execution identity. I will not
expand the existing application's resource allowlist into a general YAML escape
hatch. No arbitrary annotations, raw proxy configuration, scripts, cross-project
backends, or unrestricted Secret references are accepted through the API.

Test and production have separate Gateways, certificate references, outbound
proxies, and policies. Shared dependency controllers do not imply shared
application authority. Proxy access is bound to the release's immutable namespace
identity and selected pods, not merely a caller-supplied header. I will reject a
configuration where the network implementation cannot enforce those boundaries.

The admin service and GitHub webhook are not automatically exposed when application
ingress is enabled. Existing admin access remains independently configured.

## Admin UI, API, and CLI

I will add **Delivery / Networking** with two levels:

1. Installation administrators inspect available capabilities, installed versions,
   Gateway/listener health, permitted domains and destinations, exposure addresses,
   certificate readiness, and policy diagnostics.
2. Project administrators configure ingress and egress for each release and
   environment using typed forms, with a preview before applying changes.

Ingress fields include enabled state, managed/existing Gateway selection,
internal/external exposure, hostname, path type/path, approved certificate or
issuer reference, redirect behavior, and bounded timeout/body-size settings.
Egress fields include enabled state, approved hostname/port pairs, optional
approved direct CIDR/port/protocol rules, and proxy connection settings.
The forms explain which application clients must honor proxy settings. The API
never returns certificate private keys, proxy credentials, or other Secret values.

Preview displays the client URL, selected backend, TLS status, allowed outbound
destinations, policy differences, any workload restart, and effects on existing
connections. It validates DNS syntax and canonicalization, rejects wildcards in
the first version, and checks hostname/path ownership across projects without
revealing another project's private configuration. Conflicting exact/prefix routes
are rejected instead of relying on controller precedence. SNI and Host mismatches
must not route into another environment.

Proposed endpoints, all using the existing authenticated service layer:

| Endpoint | Capability |
| --- | --- |
| `GET /pipeline/networking/capabilities` | Inspect available installation policy and component health, filtered by authorization |
| `POST /pipeline/networking/preview` | Validate a proposed release configuration and return a policy-bound preview |
| `GET /pipeline/releases/{key}/networking` | Read desired and observed configuration, versions, health, and operations |
| `POST /pipeline/releases/{key}/networking` | Apply or disable settings with expected version and idempotency key; return operation ID |
| `POST /pipeline/releases/{key}/networking/probe` | Request bounded checks of already approved endpoints; return operation ID |
| Existing operation/recovery endpoints | Inspect progress, retry eligible work, reconcile interruptions, and manage holds |

The preview is bound to the configuration and installation-policy versions. Apply
revalidates both, authorization, reservations, activation, and recovery holds.
Probe actions cannot take an arbitrary URL or become an internal-network scanning
endpoint. CLI commands under `goblin-king pipeline networking` provide inspect,
preview, apply, disable, probe, and diagnostics with equivalent authorization.

Saving a draft changes no workloads. Applying ingress or policy changes does not
rebuild an application image. Updating proxy environment variables may roll the
same verified digest; the preview identifies that restart. Initial configuration
does not activate an inactive release or deploy an existing repository head.

## Ingress behavior

The managed Gateway routes approved host/path combinations to the release's
internal Service. Backend references stay within approved namespaces and service
identities; cross-namespace grants are narrowly scoped where necessary. The
gateway can reach only the selected application ports. Unknown hosts and paths
receive a denial or not-found response and never a default application backend.

TLS references are validated for type, hostname coverage, expiry, and listener
readiness. Certificate selection and rotation do not expose key material through
the API or application pods. HTTP redirects must not form an open redirect, and
forwarded headers are trusted only from the configured gateway path.

Ingress success requires controller acceptance/resolution/programming conditions,
internal application health, and a probe through the configured listener with the
real Host, SNI, certificate verification, path, and expected application identity.
For an external address, external reachability is a separate observed condition.
An in-cluster probe alone cannot mark Internet exposure verified. The local suite
uses an external-to-Kubernetes client and a test CA without disabling TLS checks.

Deactivation removes the application's route before settling and stopping its
workloads. Reactivation restores only the latest approved route after application
health passes. Disabling ingress leaves the internal service and data intact.
Disabling a shared managed gateway requires a preview of all affected releases,
then withdraws their routes; it does not silently delete another installation's
resources.

## Egress behavior and bypass prevention

With managed egress enabled, application service, background, and scheduled pods
may reach approved DNS, their dedicated proxy, and explicitly approved direct
destinations. They cannot reach another release's proxy, Kubernetes authority,
control-plane storage/services, other environments' data, or arbitrary addresses.
Source tests and image builds receive none of these application egress privileges.

I will provide upper/lowercase proxy variables where appropriate and a narrowly
generated NO_PROXY list for approved internal dependencies. Clients that do not
honor those settings need an explicit integration or approved direct rule. Both
fixture implementations will demonstrate actual proxy-aware requests.

The proxy denies IP-literal and unapproved hostname destinations, arbitrary
CONNECT ports, local/metadata/cluster/node addresses, and unsupported protocols.
Hostname checks and resolved-address restrictions both apply. NetworkPolicy adds
an independent address/port boundary, including actual pod, service, and node
ranges and relevant IPv4/IPv6 special ranges. A DNS name that resolves or changes
to a forbidden address must remain blocked. Acceptance will cover IPv6 where
enabled; an unsupported address family must be explicitly rejected or blocked,
never silently left unrestricted.

I will inspect all policies selecting the protected pods because NetworkPolicy
allow rules are additive. Unexpected broad allow policies cause diagnostics and a
hold; adding a nominal deny policy does not override an existing allow rule.
DNS permission is not a general data-loss-prevention guarantee, and allowing a
remote endpoint does not restrict what that endpoint itself can do with a request.

Policy changes use tested, fail-closed ordering. Narrowing permissions may briefly
deny extra traffic; it must not transiently allow an unapproved destination.
Revocation includes terminating affected proxy tunnels and, for direct rules,
restarting affected application/background pods and settling scheduled jobs when
needed to close established connections. The operation is not complete until new
and already-established forbidden connections have stopped. A safety revocation
is never automatically undone to recover application availability.

Disabling managed egress restores the previous DNS-only application policy and
removes owned proxy access; it never switches to allow-all. Proxy outage, invalid
configuration, or dependency restart also fails closed. Proxy caches are disabled
for the initial profile, and access logs exclude credentials, query strings, and
request/response bodies while retaining bounded destination/decision evidence.

## Durable operations and release integration

I will add versioned desired/observed networking records, installation-policy
revision, route reservations, configuration hashes, and durable operation evidence
through additive migrations. Existing registrations migrate with networking off;
upgrade cannot expose a service or dispatch historical work.

Application deployment, network changes, activation, rollback, and recording share
the release's existing serialization boundary. Shared Gateway and hostname/path
reservations use additional transactional conflict checks with a documented lock
order. A route to the same application cannot be modified by overlapping release
operations. Configuration drift is reported and reconciled only within owned
resources; ambiguous external ownership produces a hold.

Each external side effect persists intent first and observation before
acknowledgement. Restart recovery inspects actual resource UIDs, generations,
controller conditions, connections, and current operation ownership. Lease expiry
alone never authorizes a replacement mutation. Backups include networking state
and required configuration references, with Secret recovery handled separately.
Restore starts in recovery mode and reconciles actual exposure before enabling
delivery.

A failed application rollout restores the previous healthy digest under the
currently approved network policy. A failed route change restores the last
healthy route only if it remains authorized. Failed first exposure reports failure
without inventing a previous route. Tightened security policy remains in force
even when rollback would otherwise restore broader access.

Observed Git release records add the network configuration/policy revisions,
gateway and route identities, listener/hostname/path, certificate fingerprint and
expiry, approved egress rules, and bounded health/denial evidence. No Secret values
are recorded. Git recording failures retry recording alone, preserving successful
network changes without rebuilding, redeploying, or repeatedly changing routes.

## Implementation sequence and estimates

These are review boundaries, not alternative finish lines. I will use coherent
`codex/` branches and an integration branch, keep owner review before merge, and
validate the final combined revision after any review changes.

| Phase | Changes and exit evidence | Initial machine-hour estimate |
| --- | --- | ---: |
| 1. Capability spike and fixtures | Pin dependency versions; verify kindnet enforcement, Gateway status and proxy ACL behavior; prepare independent clients/upstreams, local TLS, and resource baseline | 4–6 |
| 2. Durable configuration and management | Add models/migrations, authorization, previews, reservations, operations, typed UI forms, and equivalent API/CLI | 6–10 |
| 3. Ingress package | Implement managed/existing Gateway bindings, route validation, TLS, exposure settings, listener probes, activation and recovery | 6–10 |
| 4. Egress package | Implement isolated proxies, direct rules, policy validation, proxy settings, connection revocation, and recovery | 6–10 |
| 5. Complete cluster proof | Extend full harness, exercise failures/restarts and security boundaries, run all regression checks and two clean acceptance runs | 10–18 |
| 6. Installation and handoff | Finish packaging/upgrades/backups/docs, retain verified demo, collect evidence, prepare reviewable PRs | 4–6 |
| Total | Both additions and the complete local proof | 36–60 |

These are initial execution estimates, not measured remaining time. I will update
them from completed phases; review waits, external DNS/firewall provisioning, and
unavailable runtime dependencies are separate. The capability spike must resolve
policy enforcement and component compatibility before the estimate is tightened.

Primary integration points are `pipeline_api.py`, `pipeline_service.py`,
`pipeline_operations.py`, the state/schema modules, `pipeline_cli.py`,
`admin-ui/src/PipelinePanel.tsx`, the installation chart, and
`scripts/pipeline_acceptance.py`. I will add separate networking validation and
execution modules rather than weaken `pipeline_deployment.py`'s application
resource restrictions. The application chart gains only validated proxy settings
and configuration identity needed for safe workload restarts.

## Mandatory cluster acceptance

I will extend prepare/run/resume/report/cleanup in the existing harness. Every
required row below must have real runtime evidence. Unsupported optional external
integrations are reported separately and never substituted for a required local
scenario. Local fixture repositories remain inside the local provider; I will not
access other remote repositories to implement or test this work.

| Scenario | Required observation |
| --- | --- |
| Installation matrix | Fresh installs with neither, ingress only, egress only, and both behave correctly; disabled features create no exposure or unintended authority |
| Upgrade from current proof | Existing admissions, releases, jobs, data, and admin access survive; networking stays off until explicitly configured; no historical dispatch |
| Managed and existing Gateway | Both binding modes route correctly; existing ownership is preserved; missing CRDs or unsupported capabilities give actionable errors |
| Admin/API/CLI agreement | Headless browser forms preview/apply/disable/probe successfully; all three interfaces show matching versions, operation IDs, and observed state |
| Authorization and conflicts | Unauthorized roles, stale versions, idempotency conflicts, hostname/path overlaps, cross-project references, and unapproved exposure produce no mutation |
| Two repositories, two environments | Four independently configured routes and egress policies work through the same implementation; exact delivered digests and Git records agree |
| External-to-cluster ingress | An independent client reaches actual gateway HTTP/HTTPS listeners; Host/path/SNI, TLS verification, redirects, WebSockets, and configured limits work |
| Negative ingress | Unknown hosts/paths, mismatched SNI/Host, wrong namespaces/ports, spoofed forwarded headers, invalid certificate references, and admin/webhook paths do not bypass policy |
| TLS lifecycle | Invalid/expired/not-yet-valid/wrong-host certificates fail verification; valid rotation succeeds with new fingerprint evidence; unavailable issuer remains pending or failed |
| Allowed egress | Both applications and declared background/scheduled work reach approved HTTP/HTTPS proxy destinations and explicit direct TCP/UDP fixtures; upstream logs corroborate actual traffic |
| Denied egress and bypass | Direct proxy bypass, another release's proxy, IP literals, alternate CONNECT ports, redirects to denied hosts, metadata/node/cluster endpoints, and cross-environment data access fail |
| DNS and policy edge cases | Rebinding and mixed A/AAAA answers cannot reach forbidden addresses; broad additive policies are detected; applicable address-family restrictions are enforced |
| Policy updates and revocation | Allow and deny changes reach the observed version; long-lived CONNECT/WebSocket/direct connections are closed on revocation; no transient broadened access |
| Fail closed | Proxy loss, bad config, DNS failure, controller restart, and invalid network policy never yield open outbound access or route to an unintended backend |
| Activation and off/on | Route withdrawal precedes shutdown; data survives; schedules/background work stop; builds while inactive remain inactive; reactivation selects approved latest state |
| Rollout and route failure | Previous healthy application/route restored where still authorized; first-install failure has accurate evidence; security restrictions are not rolled back implicitly |
| Concurrency and restart | Deploy, network apply, disable, rollback, and recovery cannot overlap unsafely; stale claims and before/after-effect crashes reconcile or hold without duplicate effects |
| Recording outage/conflict | Observed routing/policy remains intact; recording repair changes no image, route, or policy generation; concurrent service records are preserved |
| Multiple nodes and node loss | Clients/applications/proxies run across both nodes; loss causes bounded failure or recovery, never unauthorized traffic; no dependence on shared control-plane storage |
| Backup/restore | Fenced restoration preserves network state and reservations; recovery mode prevents new exposure until observed resources reconcile |
| Cleanup and uninstall | Only owned resources removed; external Gateways/controllers/certificates and unrelated workloads retain identities and health; retained demo remains healthy |

External clients and controlled upstreams will run in isolated Docker containers
outside Kubernetes, with explicitly scoped fixture addresses. These prove the
cluster boundary without depending on public websites. In-cluster probes will
add cross-node, cross-namespace, and forbidden-target checks. I will retain the
distinction between controlled external fixtures and actual Internet exposure.

Fault injection surrounds each new external effect and its acknowledgement:
gateway/route apply and removal, proxy config reload, NetworkPolicy changes,
certificate rebinding, workload restart/connection settlement, and Git recording.
Reports include source revision, dependency digests, chart/configuration hashes,
resource UIDs and generations, operation and job IDs, controller conditions,
verified TLS observations, allowed/denied connection evidence, screenshots,
baseline preservation, and cleanup results. Transient probe failures must be
distinguished from a verified policy denial using control requests and target logs.

I will run the full Python suite and lint, Linux filesystem checks, frontend
tests/build, Helm/schema/render validation, and the existing Docker and Helm
admin audits, extended with networking UI coverage. Browser checks run headlessly
in containers; they do not use the desktop, visible browser, mouse, or keyboard.
I will prepare audit fixtures before taking unrelated-resource baselines so
temporary owned fixtures cannot be misclassified during cleanup.

## Retained demonstration and final handoff

After both full acceptance runs pass, I will retain the verified implementation
with both local applications attached, test/production ingress configured, and
demonstrated permitted/denied outbound access. Local exposure stays loopback-only.
The admin shows how to select an approved external exposure mode and reports the
remaining DNS/address/certificate prerequisites without silently opening it.

The handoff will link the proof reports and document URLs, installation values,
certificate setup/rotation, proxy client requirements, allowed destination rules,
diagnostics, rollback/revocation, restore, cleanup, and public-exposure setup. It
will state exactly which external path was tested, distinguish simulated provider
events from real GitHub integration, retain the single-node SQLite storage limit,
and report owner review/merge status separately from local runtime acceptance.
