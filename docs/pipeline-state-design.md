# Delivery state and transaction boundaries

This is the implementation boundary for the next delivery slices. I have
implemented immutable build requests and delivery receipts in `pipeline_store.py`.
The registration versions, dispatch outbox, release state, and transition journal
below are still planned. The cluster persistence proof does not exercise those
future records.

## Separate identities

| Record | Identity | Responsibility |
| --- | --- | --- |
| Registration | Project and service plus monotonic version | Approved repository, worker/configuration revisions, environment routes, management enablement |
| Source receipt | Provider and delivery ID | Authenticated event fingerprint and original build request |
| Automatic build request | Project, service, provider, repository, environment, full commit | One automatic admission, immutable build ID and original registration snapshot |
| Dispatch intent | Build ID plus stage and attempt | Stable scheduler job ID, pinned worker image and validated policy identity |
| Build result | Build ID | Verified source/pipeline revisions, registry repository and image digest, test/publication evidence |
| Release state | Target, namespace, release | Current desired generation, activation, observed release, recording status, recovery hold |
| Release operation | Release identity plus operation ID | Serialized deploy/activate/deactivate/rollback/record intent and observations |

Database sequence is a pagination cursor, not Git ancestry. Delivery timestamps,
arrival order, and build completion order cannot establish which commit is newer.
The provider adapter must reconcile authoritative branch state and ancestry before
advancing the desired generation. A delayed build can remain historical evidence
without being eligible to deploy.

## Registration and admission

Registration writes will require an authenticated project/admin boundary and an
expected version. Creating a binding requires expected version zero; updating it
requires the observed version. Validate conflicts against the whole stored catalog
inside the write transaction, including other projects' reserved release and image
destinations. Do not expose those projects' private configuration in conflict errors.

Attaching a repository must not silently deploy its existing production head.
Management enablement records the initial provider cursor and desired-state policy
explicitly. Rebinding targets must not reinterpret existing requests: they retain
their original snapshots and become ineligible if current authorization no longer
permits their route. Removing a registration should retain a tombstone and history;
it is not an instruction to uninstall a release.

Provider verification happens before admission. Verify signatures over raw bytes,
installation and numeric repository identity, the registered repository/branch,
review policy, and authoritative commit state. A signed delivery alone is not
proof of an approved merge. Reconciliation must apply the same checks as webhooks.

The eventual admission transaction will persist the receipt, immutable build
request, desired-state change where applicable, dispatch intent, and audit event
together. Dispatch must never depend solely on the return value `created=True`.
Existing admitted requests need an explicit migration/reconciliation decision;
upgrading the database must not unexpectedly enqueue historical production work.

## Scheduler dispatch and restart

Reserve a deterministic scheduler job ID in the dispatch intent before enqueueing.
The scheduler store must support an atomic insert-or-read operation for that ID,
rejecting an existing job with different kind, payload, project, or policy identity.
A controller crash after enqueueing but before acknowledgement then recovers the
same job rather than creating another one.

Claims need a version/fencing token and bounded lease. Record acknowledgement only
for the still-current claim. Expired dispatch claims may be retried using the same
job ID; they do not authorize repeating external deployment side effects. Recheck
the approved immutable worker image and resource policy before dispatch. Preserve
the scheduler's mandatory validation gate. A configuration mismatch leaves the
intent blocked with a durable reason, rather than substituting a moving image tag.

Suggested dispatch transitions are `pending -> claimed -> dispatched`, with
`claimed -> pending` only through guarded recovery and `pending/claimed -> blocked`
for authorization or configuration failures. Explicit recovery from `blocked`
requires revalidation. Repeated controller observations are idempotent.

## Build and release observations

Keep these dimensions independent:

- Build stage: admitted, dispatched, running, tested, published, failed, superseded.
- Desired release: source generation, accepted build/digest, activation, configuration.
- Observed release: actual digest, Helm revision, health result and observation time.
- Git recording: pending, recorded, conflict/retry, or recovery hold.

A worker completion event is a prompt to inspect durable scheduler results and
registry provenance; it is not sufficient authority to mark a digest deployable.
A new explicit build attempt gets a new build ID linked to its predecessor. Retrying
deployment or Git recording retains the already verified build ID and digest.

Release operations will use compare-and-swap state versions plus a distinct release
lock. Before an external action, persist its intent and the expected desired
generation. Under the lock, re-read activation and desired revision, suppress stale
work, and record the selected immutable chart/configuration revisions.

Lease expiry does not prove a previous Helm process stopped. After interruption,
confirm worker termination, inspect Helm and workload state, and reconcile against
the recorded operation. Ambiguous ownership or effects enter a durable recovery
hold. Do not automatically steal a release lock and repeat Helm.

After a healthy rollout, persist the observed digest and Helm revision before Git
recording. A recording failure retains that observation and retries only recording.
Use Git compare-and-swap and bounded conflict retries to preserve concurrent edits.
Inactive test releases can accept new builds while desired activation remains off.
Deactivation must account for scheduled/background activity and retained data, not
only Deployment replica count. Emergency rollback retains an explicit hold so
normal reconciliation cannot immediately undo it.

## Required transition proof

Each stage must test legal and illegal transitions, stale versions, competing
connections, rollback of partial writes, and reopen/restart behavior. Inject crashes
before and after every external effect and its acknowledgement. Correlate requests,
dispatch IDs, scheduler jobs/runs, release operations, and provider/Git receipts.

The current SQLite topology supports serialized transactions on one storage node.
It does not establish fencing of external workers or distributed lock safety.
Those properties need their own actual worker and cluster evidence before the
later delivery phases can be considered complete.
