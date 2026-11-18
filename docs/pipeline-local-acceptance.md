# Local delivery acceptance

I use the complete scenario list in `scripts/pipeline_acceptance.py` as the finish
line. A missing, skipped, or failed required scenario makes the report incomplete.
The two final runs use fresh namespaces and the same clean source revision and image
digests. Development runs remain useful evidence, but cannot produce a passing final
report while the source is dirty or differs from the pinned installation.

The local environment uses Docker Desktop's two-node kind cluster. Both application
fixtures and their release-state repository are hosted inside the local provider.
Its review and GitHub App responses are explicitly synthetic. This proves local
delivery behavior; it does not prove real GitHub App installation or owner reviews.
Application Dockerfiles live at the repository root and use the root build context.

## Prepare the installation

I first run the Python, frontend, lint, and Helm checks, commit the intended source,
then build the installation images:

```powershell
python scripts/pipeline_build_images.py --output .runlogs/pipeline-images/images.json
```

For an initial environment, create the authenticated registry once. Supply the
controller digest from that image manifest as `HELPER_IMAGE`. The installer creates
repository-scoped publication identities and adds only this registry's CA to each
node. It refuses to adopt an existing namespace. Secrets are generated in memory
and retained in Kubernetes; they do not belong in Git or command history.

```powershell
python scripts/pipeline_local_registry.py --context docker-desktop `
  --namespace gk-local-registry --node-port 30443 `
  --helper-image HELPER_IMAGE --output .runlogs/local-registry/registry.json
```

The local preparation uses this registry namespace and discovers its NodePort.
Docker Desktop exposes locally built images through its registry mirror. Image
preparation verifies actual cached image digests on both nodes before using them.
Other Kubernetes installations require their own image publication and trust setup.

```powershell
python scripts/pipeline_acceptance.py prepare --output .runlogs/acceptance-one `
  --images .runlogs/pipeline-images/images.json --image CONTROLLER_IMAGE
python scripts/pipeline_acceptance.py run --output .runlogs/acceptance-one
python scripts/pipeline_acceptance.py resume --output .runlogs/acceptance-one
python scripts/pipeline_acceptance.py report --output .runlogs/acceptance-one
```

`CONTROLLER_IMAGE` is the digest-pinned controller image. `--only SCENARIO` runs a
development subset without changing the finish line. Use a second fresh output
directory for the second complete run. Do not run two commands against the same
output directory concurrently.

## Node loss and restoration

The storage scenario requires Docker Desktop's system-container visibility. It
matches the worker container to the Kubernetes node by its kind labels and IP
address, pauses that exact container, and verifies node unavailability without
replacement database writers or new delivery executions. The control-plane node
stays available. A separate process resumes the container after 150 seconds if the
main test is interrupted; normal completion resumes it sooner.

The scenario then fences the original database clients, exports state and image
archives outside the cluster, restores onto a separate claim on the other node,
and checks that the restored API holds delivery in recovery mode. Release history,
workload activation, and service health must reconcile before delivery is enabled.
The original claim remains available. This is deliberate restoration, not automatic
SQLite failover. See [storage and recovery](pipeline-storage.md) for operator steps.

## Browser evidence

I run the [admin runtime audit](admin-runtime-audit.md) through both Docker and Helm
admin surfaces. `scripts/pipeline_admin_prepare.py` prepares an isolated Helm audit
installation, pins the example images, verifies both node caches, and runs the
contract checks before browser submissions. Long-running services are probed through
Services rather than submitted as one-shot jobs.

If local CRI status reports a different OCI index alias, preparation verifies the
cached reference and both index hashes against the exact node-platform runtime
manifest. A different runtime or ambiguous platform still fails. The raw image IDs
and comparison evidence remain in the audit directory.

Each acceptance directory needs `docker-admin-audit/browser-proof.json` and
`helm-admin-audit/browser-proof.json`. These identify the acceptance run, source
revision and hash, loopback API endpoint, job/run IDs, checksummed browser-submission
captures, downloaded artifacts, and screenshots for kinds, runs, events, heartbeats,
services, and artifacts. Two service-probe records must show HTTP 200 and advancing
timestamps. Set `GOBLIN_KING_AUDIT_TOKEN` in the environment for verification.
`scripts/pipeline_admin_acceptance.py` checks these captures against current API
results and artifact bytes; it never submits replacement jobs. Missing browser
evidence stays incomplete even when API-only checks pass.

## Reports and cleanup

The output directory retains `acceptance.json`, `report.md`, command observations,
source identities, operation and execution records, health checks, and fault evidence.
The normal cleanup scenario runs only after all other required scenarios pass.
Explicit `cleanup` is available for an abandoned run and checks namespace ownership
and UIDs before deletion. Unrelated baseline resources must retain their identities
and specifications. Local evidence remains after cleanup.

I retain a separately installed demonstration after final acceptance. Its namespace,
revision, image digests, admin access, repository registrations, recovery commands,
and review status belong in the final proof report. Passing a development subset or
opening a PR does not establish completion.
