# Goblin King Phase Proof Table

This table records the completed phase work and the remaining gaps. It is a maintainer
receipt, not a user guide.

## Phase Proof

| Phase | Scope | Met | Not Met / Outstanding |
| --- | --- | --- | --- |
| 1 | Library kernel | Python package skeleton, contracts, registry loading, in-process execution, SQLite persistence, CLI, example goblin, tests, and contribution guidance were added. | None known. |
| 2 | Scheduler kernel | Durable schedules, queued jobs, leases, retry/timeout fields, scheduler loop, CLI, and local-only test policy were added. | None known. |
| 3 | Docker execution | Docker runtime, per-worker Dockerfiles, worker image map, Redis result transport, Compose assets, Docker-first docs, and Docker proof were added. | None known. |
| 4 | FastAPI control plane | API settings, auth token, jobs/schedules/runs/artifacts endpoints, API CLI/Compose support, tests, and API roadmap were added. | Later API surfaces were intentionally deferred at the time and later covered. |
| 5 | Reusable package integration | Project settings, multiple registries, entry point discovery, template generator, CLI/API integration, and package reuse docs were added. | Public PyPI publication remains out of scope. |
| 6 | Fanout and retry APIs | Durable fanouts, job metadata, fanout/retry services, API endpoints, CLI commands, tests, and docs were added. | None known. |
| 7 | Events, streaming, and heartbeats | SQLite event log, Redis pub/sub, WebSocket run stream, scheduler/worker heartbeats, API/CLI commands, worker heartbeat contract, and docs were added. | None known. |
| 8 | Production API hardening | Local users, teams, projects, memberships, hashed API tokens, project scoping, audit logs, rate limits, pagination, OpenAPI contracts, and tests were added. | External identity beyond generic OIDC/JWT remains out of scope. |
| 9 | Optional Kubernetes and admin proof | Optional Helm deployment, FastAPI-served admin proof path, long-running service support, hello/sample goblins, ingress notes, and user guide were added. | Kubernetes remains optional, not the default path. |
| 10 | React admin tester interface | Separate React/Vite admin service, Docker/Helm routing, admin panels for current control-plane paths, long-service controls, cleanup controls, screenshots, and docs were added. | Hard container/pod termination was later strengthened in Phase 20. |
| 11 | Stable internal package boundary | Public root imports, public/semi-public/internal module guidance, internal wheel compatibility policy, and adoption guide were added. | Public PyPI hardening remains out of scope. |
| 12 | Project plugin SDK | Generated plugin SDK path, worker folder templates, entry point metadata, validation commands, tests, and docs were added. | Official SDKs for every language remain out of scope. |
| 13 | Deploy-time discovery reload | Runtime reload for registry/project/image sources, admin discovery endpoints, scheduler refresh behavior, admin Discovery panel, and reload tests were added. | None known. |
| 14 | Host project deployment integration | Docker Compose extension fixture, Helm values pattern, project build/reload/proof commands, and admin-visible host-project goblin proof were added. | Cloud-specific deployment recipes remain out of scope. |
| 15 | Project-ready release and upgrade | Release checklist, compatibility matrix, upgrade guide, migration guide, first-hour guide, wheel/adoption proof, and fixture tests were added. | Public package distribution remains out of scope. |
| 16 | Production Kubernetes hardening | Helm resources, HPA, PDB, service accounts/RBAC, network policies, ingress TLS/options, PVC settings, and existing-secret support were added. | Managed ingress/storage/secret-provider recipes remain deployment-specific. |
| 17 | Redis Streams durable delivery | Redis Streams alongside pub/sub and SQLite events, stream status/read APIs, CLI/admin visibility, tests, and docs were added. | Redis remains live/replay transport; SQLite remains durable truth. |
| 18 | OIDC authentication | OIDC/JWT bearer validation, JWKS cache settings, claim mapping, local-token precedence, RBAC regression tests, and docs were added. | Provider-specific login flows remain out of scope. |
| 19 | Volume-backed artifact management | Artifact storage health/status, cleanup dry-run/execution, max-age/max-byte policy, admin artifact management UI, Compose volume and Helm PVC docs were added. | Object storage providers remain out of scope. |
| 20 | Scoped runtime termination | Docker container IDs/labels, Kubernetes Job/Pod identifiers, API/CLI/admin hard-kill controls, audit/events, and safe no-op behavior were added. | Runtime kill is scoped only to Goblin King-owned objects. |
| 21 | Image promotion and deployment orchestration | Promotion records, build/push/mark flows, deployment records, Helm render intent, discovery reload proof trail, CLI/admin panels, and tests were added. | Registry-specific promotion automation remains out of scope. |
| 22 | Production roadmap closeout | Roadmap/docs/screenshots were audited and closeout docs captured proof surfaces and deferred items. | Later phases added more language/runtime work after this closeout. |
| 23 | Repo-wide code cleanup | Oversized backend/admin/test areas were split, focused helpers were introduced, duplication was reduced, and cleanup docs were added without behavior changes. | Ongoing cleanup should continue as new features land. |
| 24 | Formal Goblin Container Contract | Canonical language-agnostic container contract was documented, including inputs, context, results, artifacts, events, heartbeats, exit codes, and WASI wrapper model. | Native Kubernetes WASI scheduling remains out of scope. |
| 25 | Goblin authoring documentation | Human-facing author docs, Dockerfile guidance, security model, and language-agnostic worker guidance were added. | Official language SDKs remain out of scope. |
| 26 | Minimal cross-language hello goblins | Hello workers for .NET, Go, Java, Node.js, PHP, Python, Ruby, Rust, shell, and supporting tests/docs were added. | None known. |
| 27 | Container-wrapped WASI goblins | C/WASI and Rust/WASI examples running through Wasmtime in containers were added with tests/docs. | Native host-level WASI runtime remains out of scope. |
| 28 | Cross-language runtime proof | Cross-language registry/image map, Docker runtime proof target, API visibility test, and docs were added. | These goblins were separate from the default admin registry until the later demo-registry update. |
| 29 | Cross-language contract behaviors | Artifact, progress, cancellable, transform, failure, and WASI context behavior samples were added with tests and proof targets. | None known. |
| 30 | Goblin contract validation | `goblin-king workers validate`, validation docs, tests, and Makefile validation targets were added. | Deep conformance certification beyond practical validation remains out of scope. |
| 31 | Container-first admin/docs wording | Admin UI and docs were updated to present goblins as language-agnostic OCI worker containers. | None known. |
| 32 | Language-agnostic closeout | Language/runtime docs and samples were audited; closeout doc captured proof commands and deferred non-goals. | Deferred items are tracked below. |
| 33 | Per-goblin resource policies | `docs/goblin-resource-policies.md` documented policy shape, defaults, ceilings, Docker mapping, Kubernetes/Helm mapping, and proof expectations. | Runtime-level CPU, memory, process, network, filesystem, log, artifact byte, concurrency, and secret ceiling enforcement remains outstanding. |

## Outstanding Items

- Runtime-level enforcement for per-goblin CPU, memory, process, network, filesystem,
  log, artifact byte, concurrency, and secret ceilings.
- Persisting the effective resource policy used for each run.
- API, CLI, and admin display of effective per-run resource policy.
- Audit/event records for resource-policy validation failures and enforcement outcomes.
- Public PyPI/package-publication hardening.
- Cloud-provider-specific managed service recipes.
- Object storage providers beyond Docker volumes and Kubernetes PVCs.
- Native Kubernetes WASI scheduling and host-level Wasm runtime support.
- Official language SDKs beyond the current language-agnostic container contract and
  examples.
- Deep goblin conformance certification beyond the practical local validation command.

