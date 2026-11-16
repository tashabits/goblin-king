# Repository Delivery Pipeline Roadmap

Status: planned. This branch begins the design and acceptance work; no repository
controller, delivery service, or cluster proof is implemented by this document.

This roadmap defines reusable Git-driven build and release management for Goblin
King. It follows the deployment requirements discussed in
[the deployment proposal](https://github.com/engageLively/coreshell-drive-deployment/issues/26).
The reusable implementation stays Apache-2.0. Organization-specific repository
registrations, cloud resources, credentials, data destinations, and application
charts belong to adopting projects.

## Product outcome

Install Goblin King delivery once on an existing Kubernetes cluster. An operator
connects an approved repository, reviews its discovered service contract and
environment mapping, and enables management. Reviewed merges then build, test,
publish, deploy, verify, and record releases without an additional manual step.

Onboarding another conforming repository must require configuration only. It must
not require a new pipeline implementation, copied workflow, controller code change,
or service-specific conditional. Prove this by onboarding a second independent
fixture repository using the same installed controller.

The first version supports one deployable service per repository and separate test
and production releases. Installation and organization integration are one-time
administrator work. A repository still needs a repeatable container build,
credential-free tests, a health contract, and declared runtime requirements.
Missing requirements must produce actionable validation errors before activation.

## Onboarding contract

1. Install the controller and configure its approved Git provider installation,
   registry destinations, cluster targets, durable storage, and notification route.
   Reference externally provisioned credentials without placing credential values
   in source, generated configuration, logs, or release records.
2. Connect an allowed repository through the admin UI or the equivalent API/CLI.
   Verify access and branch policy, discover the service contract, and show a
   preview of the intended test and production releases.
3. Validate build context, Dockerfile, test entry point, health probe, ports,
   resources, storage, secret references, and scheduled/background work. Check
   referenced paths against the pinned checkout, including symlink escapes.
4. Bind the repository to a centrally approved deployment profile: target cluster,
   namespace, registry, chart revision, environment branches, and release names.
   Source files cannot grant themselves privileges or select arbitrary targets.
5. Validate test isolation and show the initial revision and activation policy.
   Enabling management establishes the initial desired state explicitly; attaching
   a repository must not unexpectedly deploy a historical production revision.
6. Manage subsequent reviewed merges, activation changes, retries, rollback,
   status reporting, and reconciliation through the same installed service.

Use a conventional Dockerfile and test entry point as discoverable defaults. Allow
declarative overrides for conforming projects. Offer a centrally maintained chart
profile for ordinary HTTP services; allow operator-reviewed chart bindings for
services with additional storage or scheduled work. Do not attempt to infer cloud
permissions, data isolation, or application secrets from source code.

## Execution and trust boundaries

A persistent controller verifies Git events and reconciles approved branch state.
It dispatches finite build, deployment, and recording jobs through Goblin King's
existing validated container model. Keep the mandatory validation gate, resource
policies, durable evidence, and language-independent worker contract.

- Build/test workers check out the full event commit and run application code with
  no deployment identity, cluster credential, or production application secret.
  Ordinary PR checks are credential-free. Publication uses an isolated identity
  restricted to the approved registry destination.
- Each source build attempt receives an immutable build ID. The image tag uses
  that build ID; retain the full source commit and pipeline revision as metadata.
  Capture the registry digest and deploy by digest. Do not create moving tags.
- Deployment workers run centrally maintained, pinned code and approved chart
  configuration. They verify the authoritative successful build and image
  provenance rather than trusting a completion notification.
- The recorder writes only approved values and release-record paths, including an
  adopter-configured change log such as `docs/CHANGES.md`. Git permissions alone
  do not enforce path restrictions; trusted recorder code must do so.
- Runtime identities and data destinations remain separate from build and deploy
  identities. Test workloads cannot write production data or consume
  production-only secrets.
- Builders must not depend on a developer's Docker socket or local checkout.
  Select and prove an isolated builder supported by the target cluster's security
  policy. Do not grant task goblins host Docker socket access.

Provider-specific adapters may initially support GitHub, Artifact Registry, and
GKE. The core catalog, state machine, evidence, and worker interfaces must not
hard-code an adopter's organization or cloud account.

## Cluster foundation comes first

The current Helm deployment uses a shared SQLite path and a ReadWriteOnce PVC.
API, scheduler, and optional repository components mount that data volume.
Increasing replicas or installing on a multi-node cluster does not by itself
establish safe storage access or high availability.

Before advertising cluster delivery:

- Define and enforce the supported persistence and placement topology. A limited
  single-writer mode must constrain placement and replicas and document node
  recovery. Multi-node or highly available control-plane operation needs a
  compatible durable store and tested migration, rather than an assumed shared
  SQLite filesystem.
- Persist registrations, source events, build identity, desired state, observed
  state, release locks, reconciliation holds, and audit evidence across pod
  replacement. Redis delivery is not the only durable record.
- Package the controller and workers in the Helm installation with scoped service
  accounts, bounded resources, startup/readiness/liveness probes, cleanup limits,
  registry authentication, and explicitly documented network requirements.
- Install into isolated test namespaces without replacing unrelated resources,
  modifying existing release values, or broadening cluster-wide permissions.
- Prove installation, upgrade, restart, node rescheduling, backup/restore, and
  cleanup against a real cluster. Report supported Kubernetes, Helm, architecture,
  and storage configurations from observed proof.
- Keep cluster connection logic behind one adapter so private API connectivity can
  change without changing repository contracts.

A local single-node Kubernetes run is the first proof environment. A multi-node
environment and a staging or dedicated pilot namespace on the intended cluster
are required before claiming the production target is supported.

## Delivery state and recovery invariants

Track accepted image, desired activation, observed running release, and Git
recording state independently. A successful image build is not a successful
deployment; a successful rollout is not a completed Git record.

- Duplicate events converge on one durable source event and intended operation.
  Explicit build retries get new build IDs; deployment retries retain the
  successful source build ID and digest.
- Serialize deployment, activation, rollback, and recording per service and
  environment. Different services can progress concurrently.
- Scheduler job leases do not replace release locks. Expiry is never sufficient
  evidence that the old Helm process stopped. Confirm termination and inspect
  actual release state before resuming; ambiguous recovery stays blocked.
- Re-read the latest desired revision and activation state under the release lock.
  Default to marking older builds superseded so a slow build cannot replace a
  newer intended release. Keep this policy explicit in registration.
- Pin source, pipeline, chart, and deployment-configuration revisions. Render and
  validate before applying. Use bounded rollout waits, atomic rollback where
  supported, and health checks that verify both workload and service route.
- If rollout succeeds but recording fails, retain the observed result, enter a
  reconciliation hold, and retry recording without rebuilding or blindly
  redeploying. Git writes use compare-and-swap with bounded conflict retries.
- Recorder commits matching observed state are no-ops for deployment. Do not
  suppress all commits from a particular author.
- Inactive test releases accept and record new images while staying inactive.
  Deactivation stops scheduled and background work as well as scaling workloads
  down, and preserves storage.
- Reviewed rollback and recorded emergency rollback retain prior build/digest,
  configuration, and Helm revision. An explicit emergency hold prevents normal
  reconciliation from immediately undoing the rollback.
- Failures produce durable evidence and notifications. Failure in one repository
  must not block all other services.

## Branch and PR sequence

Each row is a delivery phase, not a claim of current implementation. Split a phase
further when necessary to keep review scope coherent. Use feature branches and
PRs into `main`; never commit implementation directly to `main`. Advance from the
updated base after the preceding phase is reviewed and merged.

| Branch | Objective | Required evidence |
| --- | --- | --- |
| `feature/pipeline-foundation` | Catalog and source contracts; durable operation identity and state; executable cluster test harness; supported persistence topology | Contract and transition tests; duplicate/concurrent admission and restart proof; fresh cluster installation baseline |
| `feature/pipeline-git-events` | Attach repository through UI/API/CLI; verify events; reconcile missed events; enforce allowed repos and protected branches; report status | Forged/replayed/out-of-order event tests; missed-event recovery; attachment visible through admin; actual HTTP and cluster controller proof |
| `feature/pipeline-build-workers` | Exact-commit tests/builds; isolated cluster builder; immutable publication; verified successful-build handoff | Build/test/push failure proof; amd64 image and digest proof; worker validation; identity boundary checks on cluster |
| `feature/pipeline-deployment` | Trusted deployment jobs; release locking; freshness; rollout health and rollback | Concurrent/expired-lock proof; no overlapping Helm operation; failed rollout recovery; actual target digest and health evidence |
| `feature/pipeline-reconciliation` | Concurrent-safe recording; change log; activation; restart recovery; emergency hold | Conflicting Git writes; rollout-success/recording-failure recovery; scheduled work off/on; durable audit and notification evidence |
| `feature/pipeline-cluster-adoption` | Reusable installation and repository onboarding; upgrade/recovery documentation; second-repository proof | Entire acceptance matrix on cluster; multi-node and target-pilot proof; admin runtime audit; second repository added without code changes |

Runtime and persistence testing begins with the foundation and continues in each
phase. The final adoption phase consolidates that evidence; it must not be the
first time the implementation runs on Kubernetes.

## End-to-end cluster acceptance matrix

All rows require actual observations, retained IDs, and reproducible commands.
Unit tests, mocked APIs, and Helm rendering complement these checks but cannot
substitute for them. A denied or unreachable runtime is blocked, never passed.

| Scenario | Passing result |
| --- | --- |
| Install beside existing workloads | Controller ready; storage persists; existing workloads and unrelated releases unchanged |
| Attach first repository | Contract and branch policy validated; reviewed mapping visible; no repository-specific pipeline code |
| Merge to test | Event commit becomes immutable build ID and digest; test release is healthy; Git record and change log agree |
| Merge to production | Separate approved target and identity; exact digest deployed and recorded without a second approval step |
| Attach second repository | Same controller and pipeline manage it using only registration and standard contract files |
| Invalid contract or unapproved source | Clear rejection; no build publication or deployment |
| Failed tests, build, or push | Failure recorded; current release and successful Git record preserved |
| Duplicate and reordered events | No duplicate successful rollout; stale commit cannot overwrite intended revision |
| Concurrent services | Independent progress; Git writeback preserves both services' records |
| Competing operations on one release | Deployment, activation, and rollback serialize; expired lease does not cause overlap |
| Controller/worker interruption | Durable intent survives; actual worker and release state checked; recovery does not blindly rerun side effects |
| Rollout succeeds, Git unavailable | Reconciliation hold persists across restart; recording repaired without rebuild or repeat rollout |
| Bad rollout or health response | Previous healthy digest restored and verified; failure recorded and notified |
| Test off, then a new build | New image accepted; workloads remain off; scheduled/background jobs stop; data retained |
| Test on again | Newest accepted image activated with approved scheduled work and healthy route |
| Emergency rollback | Known-good release restored; Git reconciled; hold prevents automatic reversal |
| Build identity attempts cluster access | Denied by actual identity/RBAC/network boundaries; no deployment credential in build environment |
| Test identity accesses production data | Denied using harmless sentinel fixtures; no real customer data used in proof |
| Upgrade and pod/node replacement | Registrations, records, holds, locks, and artifacts retain supported semantics |
| Backup and restore | Durable state restored; controller reconciles actual workloads before resuming delivery |
| Cleanup | Only test-owned resources removed; unrelated namespaces, PVCs, and releases preserved |

Record commit SHA, image digests, cluster/runtime versions, topology, namespace,
worker/job/run IDs, Helm revision, observed state, and relevant logs for each run.
Retain the browser-driven Docker and Helm admin audit required by
[the contribution guide](CONTRIBUTING.md) and
[admin runtime audit](admin-runtime-audit.md).
Run local pytest and ruff; include frontend tests/builds, HTTP proof, Docker proof,
and Helm/cluster proof when applicable. GitHub Actions are not the required gate.

## Adoption decisions and current limits

The reusable foundation can be developed with fixture repositories and isolated
test namespaces. Live onboarding still needs confirmed repository mappings,
registry and workload identities, runtime/data isolation, branch protection,
notification ownership, cluster connectivity, and Helm history policy.

Application secret provisioning, Hub installation/configuration, infrastructure
administration, and application data migrations are outside ordinary delivery.
Helm metadata storage must be chosen and rehearsed explicitly where application
Secret access is prohibited. Reference existing application secrets by name.

The initial pilot is a test release. Existing production delivery remains in place
until cluster failure, recovery, isolation, and rollback evidence passes. This
roadmap does not claim compliance certification or production readiness.
