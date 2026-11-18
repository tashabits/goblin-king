# Complete local delivery acceptance

I completed both fresh-namespace acceptance runs against the same clean revision `437d2ca3debcdc6438dd15568fc62f4d2cc69ba6`. All 25 required scenarios passed in each run, including 33 interruption boundaries per run, storage restoration, upgrade, browser verification and cleanup.

The Git provider, fixture repositories and review records are simulated locally. This does not establish real GitHub App installation or genuine review enforcement. Both applications use a root Dockerfile and root build context.

| Run | Required scenarios | Fault boundaries | Cleanup preserved resources |
|---|---|---|---|
| `3bd4842c-4a7a-4d7a-8569-bee523364b00` | 25/25 | 33/33 | 136 |
| `0dd10f3f-e407-4902-8a75-dc19dda72e1a` | 25/25 | 33/33 | 136 |

I built and installed `goblin_king-0.1.0-py3-none-any.whl` into an isolated package directory, checked the installed CLI, and validated the existing project-ready compatibility fixture. Wheel SHA-256: `d42afb43be25ed4ad8515b91ebe02a79bdc3d8803f01d0ce3b7c5ffacc8a4763`.

| Interface | Version | Compatibility |
|---|---|---|
| Existing goblin, registry, image map and result contracts | `goblin-king/v1alpha1` | Unchanged; installed compatibility fixture passes |
| Delivery service contract | `goblin-king/pipeline-service/v1alpha1` | Optional; root Dockerfile and root build context |
| Delivery catalog contract | `goblin-king/pipeline-catalog/v1alpha1` | Optional; operator-owned deployment profiles |
| HTTP service chart profile | `http-service-v1 / chart 0.1.0` | Digest-only release with bounded workload kinds |
| Historical admissions and jobs | `Additive upgrade` | Preserved; historical admissions do not automatically execute |
| SQLite storage | `Single-node control plane` | Fenced restore and reconciliation; no automatic database failover |

Both runs passed 663 Python tests, lint, 35 Linux filesystem checks, frontend tests/build and Helm validation. The 6 Windows filesystem skips are covered by the separate passing Linux checks.

I also checked the optional authenticated HTTPS notification sink on both installed systems. Invalid credentials were denied. Outage retries and process interruptions before the sink effect, after the effect, after the response but before durable acknowledgement, and after durable acknowledgement preserved one sink effect and one admin event per identity. No extra build or deployment occurred. I restored the original optional-sink configuration after each check.

HTTP delivery can repeat after an interruption. The test receiver deduplicates by the stable `Idempotency-Key`; an adopter's receiver needs the same behavior. Configure `pipeline.notificationSecret` with a Secret containing the `token` key and set `pipeline.settings.notification_url` to the HTTPS destination. Credential values stay outside Git.

The browser audits submitted every registered one-shot kind through the UI in Docker and Helm, verified returned artifacts, and probed the long-running service twice. [Screenshots and job/run identities](README.md). Delivery onboarding also matched UI, API and CLI state and displayed both discovered contracts and routes.

[Machine-readable proof index](delivery-proof.json) includes immutable installation images, namespace identities, every fault boundary, correlated operation/job IDs, Helm history, route observations, isolation denials, storage recovery and cleanup evidence. Full execution captures and private backup archives remain in the local checkpoint directory.

Both database/artifact backup pairs were copied outside their source volumes and verified by SHA-256. Applied retention exported execution evidence before deleting owned Jobs and scratch claims; database records and retained result ledgers stayed intact, and all four application routes remained healthy.

I captured the baseline while the owned Helm audit image cache was still being prepared. The first cleanup check correctly reported its missing UID after the preparation helper removed it. I retained that failed attempt and the original baseline, verified the audit namespace ownership and captured Pod owner references, and classified that one temporary fixture as owned test infrastructure. I then reran cleanup and preservation checks for every other baseline identity, specification and workload health observation. The proof index includes this correction. Complete admin-audit preparation before capturing the baseline when reproducing these runs.

The packaged Helm audit helper verified cached OCI index aliases against the exact node-platform runtime manifest. Raw image identities and hash comparisons remain in the local audit evidence. The packaged preparation helper passed on this revision without local substitutions.

SQLite remains a single-node control plane. Recovery fences the source and restores into a separate claim, then reconciles real releases before enabling delivery. This is not automatic database failover. Review and merge status are separate from the runtime result.
