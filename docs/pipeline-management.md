# Delivery management

I manage delivery through the authenticated API. The **Repositories / Delivery**
admin panel and `goblin-king pipeline` commands use the same service layer and
authorization checks. Neither interface edits the database directly.

The API exposes repositories, builds, releases and operations under `/pipeline`.
Discovery previews contracts and routes before attachment. Management changes,
rebuilds and release actions require expected versions and idempotency keys.
Long-running actions return durable operation or build IDs.

`goblin-king pipeline --help` lists discovery, attachment, inspection, management,
rebuild, release, recovery and diagnostics commands. Authentication comes from
`GOBLIN_KING_API_TOKEN`; I do not put tokens in command-line arguments or Git.
Installation-wide recovery diagnostics require administrator access.

After a healthy rollout, recording writes one current record per release, an
immutable operation record and a changelog to the separate release-state Git
repository. Each record includes source, configuration, pipeline and chart
revisions, image digest, build and operation IDs, Helm revision, activation and
health evidence. Non-force compare-and-swap updates preserve concurrent edits.

A recording outage keeps the observed healthy deployment and retries recording
with bounded backoff. It does not rebuild or redeploy. The record document is
persisted before publication, so a restart can recognize the same immutable
operation instead of generating different content.

Admin events and the optional authenticated HTTPS notification destination use
durable deduplicated delivery identities. Credentials come from Secret references.
Application test failures require an explicit rebuild with a new UUID; transient
provider and recording failures can retry without another application build.

For an interrupted build on hold, I use **Verify termination and settle interrupted
build** in the build details, or `goblin-king pipeline recover-build BUILD_ID
EXPECTED_VERSION IDEMPOTENCY_KEY`. Recovery checks the original retained execution
and every remaining source and publication Job and container in its recorded
namespace. Missing evidence or running containers keep the build on hold.
The installation grants the API read-only Job and Pod access in the configured
source namespaces for this check. It grants no source mutation or Secret access.
Successful recovery preserves the result as a failed attempt; I can then explicitly
rebuild with a new UUID. Recovery itself never publishes or deploys an image.
