# Kubernetes Placement And Federation Roadmap

This roadmap describes future work for evolving Goblin King from its current
project-adoptable scheduler model into Kubernetes placement and, later,
geographically distributed or federated execution. It is a planning document,
not an implemented feature.

Core principles:

```text
Placement chooses where a validated goblin container runs. It does not change
what a goblin is.
```

```text
Federation is a later layer above the existing goblin contract, validation gate,
resource policies, and result/artifact model. It must not replace them.
```

## Goals

- Document a staged path from current Kubernetes support to placement-aware
  scheduling.
- Make single-cluster Kubernetes placement the first milestone.
- Make multi-cluster and geographic federation a later milestone.
- Preserve the container-first goblin model.
- Preserve validation before scheduling.
- Preserve resource policy enforcement and global ceilings.
- Avoid conflating Docker Compose local development with Kubernetes placement.
- Avoid implying a single managed Kubernetes cluster always spans arbitrary
  global regions.
- Make eventual distributed experiment and testbed use cases possible without
  implementing them yet.

## Current Starting Point

- Goblin King schedules validated, contract-compliant Docker/OCI goblin
  containers.
- Docker Compose and local Docker remain the local development path and do not
  support geographic placement.
- Kubernetes and Helm are the natural future runtime path for placement because
  Jobs and Pods can use node labels, node selectors, affinity, tolerations, node
  pools, and zone-aware scheduling.
- Single-cluster placement can target node pools, zones, hardware pools,
  dedicated worker pools, or labeled probe pools inside one Kubernetes cluster.
- True Tokyo-to-LA style geography usually requires multiple clusters, remote
  runner agents, or a multi-cluster control layer.
- The roadmap should prove single-cluster Kubernetes placement before attempting
  federation.

## Additional Guardrails

- Do not change the Goblin Container Contract.
- Do not weaken the mandatory validation gate.
- Do not allow placement to bypass validation.
- Do not allow placement to bypass resource policies or global ceilings.
- Do not make Docker Compose pretend to support placement.
- Do not add raw Kubernetes pod spec injection from project config.
- Do not allow arbitrary unvalidated Kubernetes fields in project config.
- Use allowlisted placement labels and fields.
- Do not give goblin task containers Kubernetes credentials or the Docker socket.
- Do not introduce federation before single-cluster placement is proven.
- Do not imply a single managed Kubernetes cluster can always span arbitrary
  global regions.
- Do not imply untrusted third-party container execution is supported.
- Do not imply production multi-tenant safety is complete.
- Keep all future placement and federation work compatible with project-defined
  goblins.

## Placement Phase 1: Single-Cluster Kubernetes Placement Model

Plan a future design for running Goblin King in one Kubernetes cluster and
creating goblin Jobs or Pods with placement rules.

This should support node pools, zones, labeled probe nodes, hardware-specific
nodes, dedicated worker pools, and regional or multi-zone clusters where the
cluster provider supports them.

Future placement intent might look like:

```yaml
placement:
  required:
    goblin-king.io/pool: measurement-tokyo
```

or:

```yaml
placement:
  required:
    goblin-king.io/zone: us-west1-a
```

These examples are future design sketches, not current supported config.

## Placement Phase 2: Project Config Placement Fields

Plan future placement fields in project config. Placement should be scheduling
metadata, not part of the goblin container contract.

A future project goblin might eventually express placement intent like:

```yaml
goblins:
  tokyo-probe:
    image: example/network-probe:local
    placement:
      required:
        goblin-king.io/city: tokyo
      preferred:
        goblin-king.io/provider: gke
```

Field names are future design examples and must align with project config
versioning when implemented.

## Placement Phase 3: Placement Validation And Allowlisted Labels

Plan validation for placement requests before scheduling. Future validation
should cover allowed placement label keys, allowed values where practical,
runtime support, and whether a requested selector can be accepted safely.

Future errors should be clear, for example:

```text
Placement is only supported by the Kubernetes runtime.
Runtime: docker
Requested placement: goblin-king.io/city=tokyo
```

or:

```text
No Kubernetes nodes match requested placement.
Selector: goblin-king.io/city=tokyo
```

Placement validation must be separate from container contract validation, and
both must pass before scheduling.

## Placement Phase 4: Kubernetes Runtime Mapping

Plan future mapping from placement policy to Kubernetes primitives:

- `nodeSelector`
- `nodeAffinity`
- `preferredDuringSchedulingIgnoredDuringExecution`
- tolerations for dedicated pools
- labels and annotations for run traceability
- resource requests and limits from effective resource policy
- active deadline and timeout mapping where already modeled

Raw pod spec injection should remain out of scope for the first placement pass.
Placement chooses nodes; effective resource policy controls consumption.

## Placement Phase 5: Placement Visibility In CLI And Admin

Plan future CLI and admin visibility for placement decisions and failures.

Run details should eventually show requested placement, resolved placement
policy, runtime, Kubernetes namespace, Job or Pod name, scheduled node name,
placement-relevant node labels, and placement failure reason when scheduling
cannot proceed.

Admin should clearly distinguish pending Kubernetes scheduling, placement
validation failure, no matching nodes, and active execution on a matched node.

## Placement Phase 6: Placement Examples And Documentation

Plan future placement examples and docs:

- zone-specific hello goblin
- GPU or hardware pool goblin
- network probe goblin from a labeled node pool
- same-region multi-zone test
- dedicated measurement pool example

Docs should explain the difference between local Docker mode, single-cluster
Kubernetes placement, and future multi-cluster or geographic federation.

## Federation Phase 1: Multi-Cluster Problem Statement

Plan the problem statement for true geography. Tokyo-to-LA style experiments
normally require more than node placement inside one Kubernetes cluster.

The roadmap should stay provider-neutral and explain that single Kubernetes
clusters often have regional boundaries, multi-zone does not necessarily mean
multi-region, and geographic experiments usually require multiple clusters,
remote runners, or a multi-cluster executor.

## Federation Phase 2: Cluster Or Runner Registry

Plan a future registry of execution locations. The registry could represent
clusters, runner agents, or execution backends, so the roadmap should not pick a
final implementation prematurely.

A future registry might eventually describe locations like:

```yaml
locations:
  tokyo:
    labels:
      goblin-king.io/city: tokyo
      goblin-king.io/country: jp
      goblin-king.io/provider: gke
      goblin-king.io/runtime: kubernetes

  los-angeles:
    labels:
      goblin-king.io/city: los-angeles
      goblin-king.io/country: us
      goblin-king.io/provider: gke
      goblin-king.io/runtime: kubernetes
```

These fields are future examples, not current config.

## Federation Phase 3: Remote Runner Agent Or Multi-Cluster Executor

Evaluate two future approaches.

Remote runner agents:

- each location runs a Goblin King runner or agent
- the agent registers with a central control plane
- the agent pulls eligible runs
- the agent launches goblin containers locally
- the agent streams results, artifacts, events, and heartbeats back

Multi-cluster executor:

- central Goblin King has credentials for multiple clusters
- the scheduler chooses a target cluster based on placement
- the executor creates Kubernetes Jobs in that cluster
- the control plane collects status, results, artifacts, and events

Remote agents may be safer or easier for NAT, firewall, and geographic setups,
but both approaches should be evaluated before implementation.

## Federation Phase 4: Geographic Placement Policy

Plan future placement expressions for geography:

```yaml
placement:
  required:
    goblin-king.io/city: tokyo
```

or:

```yaml
placement:
  required:
    goblin-king.io/region: asia-northeast
```

Future paired goblins might eventually express intent like:

```yaml
goblins:
  la-listener:
    placement:
      required:
        goblin-king.io/city: los-angeles

  tokyo-probe:
    placement:
      required:
        goblin-king.io/city: tokyo
```

These are future design examples, not current support.

## Federation Phase 5: Distributed Run Aggregation

Plan future aggregation for distributed runs. The system should eventually be
able to collect run group or experiment ID, per-goblin run status, per-location
status, logs and events, artifacts, result envelopes, timing or latency
metadata, placement metadata, and failure reasons.

This should not become a full experiment workflow engine in the first placement
or federation pass.

## Federation Phase 6: Safe Network Experiment Patterns

Plan safe patterns for Japan-to-LA or similar experiments:

- outbound-only probes first
- explicit listener goblins only later
- short TTLs
- explicit allowed ports
- no arbitrary public exposure by default
- audit records
- cleanup guarantees
- network egress policy
- resource limits
- validation still required

Future examples might include HTTP checks from Tokyo to LA, DNS behavior
comparison, CDN/API latency probes, game server route probes, and edge service
availability checks.

Avoid scraping-oriented or abusive examples.

## Federation Phase 7: Federation Closeout And Adoption Docs

Close out future federation work with docs, admin screenshots, proof table
updates, troubleshooting, security-model updates, examples, limitation notes,
and honest alpha or beta status language.

Closeout proof should show placement metadata and distributed run aggregation
without implying untrusted public execution or production multi-tenant safety.

## Non-Goals

- No implementation in this roadmap document.
- No federation in the current local Docker path.
- No Docker Compose geographic placement.
- No raw pod spec injection.
- No bypass of the validation gate.
- No bypass of resource policies or global ceilings.
- No untrusted third-party container execution.
- No public multi-tenant production claim.
- No provider-specific lock-in.
- No native WASI scheduling.
- No full workflow or DAG engine in the first placement pass.
- No public listener exposure by default.
- No automatic opening of firewall rules in early phases.

## Acceptance Criteria

- The roadmap clearly separates current state, single-cluster Kubernetes
  placement, and later multi-cluster or geographic federation.
- Docker Compose is explicitly documented as not supporting this placement
  model.
- All goblins remain validated contract-compliant containers.
- Placement does not bypass validation, resource policy, ceilings, audit/event
  visibility, or the existing result/artifact model.
- The roadmap provides a phased path from node-pool placement to geographic
  federation.
- Non-goals and safety guardrails are explicit.
- No implementation code or behavior changes are introduced by this roadmap.
