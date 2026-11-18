# Release changes and recovery

I serialize deployment, activation, rollback and recording for each release. Every
operation has a durable ID, expected state version and idempotency key. Execution
rechecks the desired generation and activation state while holding the release.
An older completed build cannot replace a newer desired revision.

The `http-service-v1` chart supports an HTTP Deployment and Service, probes,
optional persistent storage, background activity and scheduled work. The rendered
resources must match the approved namespace, identities, image digest, Secret
references and resource bounds. Test and production use separate Kubernetes
identities. Helm history uses ConfigMaps.

Rollout has a five-minute default timeout. Readiness and the application Service
route must both be healthy. A failed rollout restores and independently verifies
the previous healthy release. A failed first installation has no previous release
to invent. The observed rollback is recorded even though the attempted operation
ends in failure.

Deactivation scales the service and background Deployment to zero, suspends
schedules, settles active scheduled Jobs and retains data. New builds remain
inactive until explicit activation selects the newest verified image. Emergency
rollback leaves a persistent hold that requires an explicit clear operation.

Lease expiry does not authorize another Helm operation. Recovery requires retained
Job and Pod identities and confirmed termination, including init containers. An
inspection operation then compares actual Helm and workload state. If the desired
release already succeeded, recovery verifies it without another upgrade; otherwise
it restores a known healthy release or keeps the ambiguity held. Missing evidence
does not authorize replacement.

The local acceptance harness retains operation IDs, Helm history, health evidence
and Pod identities for failed rollout, contention and interrupted execution. The
complete acceptance report remains the completion authority.
