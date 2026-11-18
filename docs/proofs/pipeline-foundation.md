# Pipeline Foundation Proof

Scope: service/catalog contracts, durable source admission, and the single-node
storage baseline. This is a partial foundation, not a running delivery controller
or completion of the Kubernetes delivery acceptance matrix.
Base: `4a5ebe36060487e02b97a4d83cdf02560b670dd0`.

## Resumed cluster proof: November 16, 2026

I verified local Git writes, Docker engine access, and the `docker-desktop`
Kubernetes API after resuming. Docker Desktop is 4.69.0, Docker Engine is 29.4.0,
Helm is `v4.1.3+gc94d381`, and both `desktop-control-plane` and `desktop-worker`
reported Ready on Kubernetes v1.36.1. The architecture is linux/amd64.

I added the guarded `sqlite-single-node` chart mode and ran the executable harness
twice in fresh namespaces. Both runs passed and removed their fixture namespaces.
The final runtime probe explicitly closes SQLite connections, including backup
connections, and its tests reject missing or replaced databases before replay.

```text
docker build -t goblin-king:pipeline-foundation-proof-v2 .
passed

python scripts/pipeline_cluster_proof.py --context docker-desktop --node desktop-worker --image goblin-king:pipeline-foundation-proof-v2 --storage-class hostpath --output .runlogs/pipeline-cluster-proof-2 --cleanup
result: passed

helm lint charts/goblin-king --set persistence.topology=sqlite-single-node --set persistence.sqliteNode=desktop-worker
1 chart(s) linted, 0 chart(s) failed

python -m pytest tests/test_scheduler.py::test_timeout_configuration_marks_overdue_run tests/test_service_websocket_transport.py tests/test_pipeline_storage_chart.py tests/test_pipeline_persistence_probe.py -q
35 passed, 1 warning in 31.52s

python -m ruff check .
All checks passed!

python -m pytest -q -ra
568 passed, 5 skipped, 2 warnings in 495.05s (0:08:15)
```

| Observation | Evidence from the second run |
| --- | --- |
| Namespace | `gk-pipeline-proof-065dcbf000`, UID `c2701b4e-9095-4d94-9632-b9f171ab59ba` |
| Image observed by Kubernetes | `docker.io/library/goblin-king@sha256:92fc5401d9528470bca465b41c686cf716422badcc7441b5fa23e54171be2be3` |
| Installation and upgrade | Helm revisions 1 and 2; revision 2 deployed before cleanup |
| Placement | API, scheduler, and repository API colocated on `desktop-worker`; one pod each |
| Admission identity | `d4a1e874-beba-4963-a0e2-b4634732f678` |
| Replay from another process | Scheduler pod returned the API pod's original build ID; one request and zero scheduler jobs |
| HTTP boundary | Health 200; unauthenticated project creation 401; authenticated creation 200; audit read 200 |
| Persisted HTTP mutation | Project `0f2b23c2-720f-4874-9c4b-b59d1315c20c` remained in audit history after pod replacement |
| Pod replacement | All three storage-client UIDs changed; replacement repository API returned the original build ID |
| Backup and restore | Online SQLite backup exported to the host and restored into `/tmp/restored.sqlite3`; original build ID retained; integrity check `ok` |
| Cleanup | Helm release and owned namespace removed; original cluster namespaces remained |

The tested runtime is based on `1d6ed15fc9a2e5519f17a3393f37d1a6bbec2ccf` plus
the working-tree storage/probe changes. The harness records `source_dirty: true`,
the diff, and file hashes; the table does not claim that the original commit alone
contains those changes. The image digest identifies the actual runtime bytes.
Development-dependency and documentation edits after the image build do not change
the packaged runtime code.

The [retained summary](pipeline-foundation-cluster.json) contains the primary IDs.
Full local evidence is under `.runlogs/pipeline-cluster-proof-2`: commands, values,
file hashes, before/after pods, nodes, Helm history, PVC metadata, and the exported
fixture backup. Build output is `.runlogs/pipeline-foundation-build-v2.log`.

I also ran the original admission smoke in the built Docker image:

```text
docker run --rm --entrypoint python goblin-king:pipeline-foundation-proof-v2 -m examples.pipeline.admission_smoke
result: passed
build_id: 70337acc-a530-41da-8f7a-b04ac941a915
requests: 1; scheduler_jobs: 0; duplicate_created: false
restart_preserved_identity: true
```

The first resumed full suite completed with `1 failed, 541 passed, 8 skipped` in
503.03s. The existing scheduler timeout test used a real clock with a zero-second
timeout; it failed again in isolation, while the unchanged baseline copy passed.
I made that test deterministic using a controlled two-second elapsed interval and
a one-second timeout. Scheduler implementation and timeout semantics are unchanged.
I also added the missing `pytest-asyncio` development dependency so the three async
transport tests execute instead of being skipped. Current local tooling is Python
3.12.10, pytest 9.2.0, pytest-asyncio 1.5.0, and ruff 0.15.12.

The final complete suite passed. Its five skips are Windows limitations: two
symlink tests, one POSIX `O_NOFOLLOW` test, and two POSIX group/setgid tests. The
two warnings concern AnyIO's pytest alias and Starlette's HTTP test-client adapter.
Full output is retained in `.runlogs/pipeline-foundation-full-pytest.log`.

I then ran the filesystem test file inside the built linux/amd64 image, with the
checkout mounted read-only and pytest/pytest-asyncio installed only in the
disposable container:

```text
python -m pytest tests/test_kubernetes_artifacts.py -q -p no:cacheprovider
18 passed in 1.14s
```

That run includes all five filesystem cases skipped on Windows. Its output is
`.runlogs/pipeline-foundation-linux-filesystem.log`.

## Remaining acceptance

The full browser-driven Docker/Helm admin audit, application worker execution,
release state machines, node-loss recovery, and an intended-target pilot remain
outstanding. This run used one storage node in a two-node local cluster; it is
not evidence of multi-node SQLite access or high availability. The fresh-install
and same-chart-upgrade checks do not prove migration from every prior deployment.
No application build, registry publication, rollout, or Git recording was performed.
No pipeline PR is ready to merge on the strength of these results alone.

See [storage setup and reproduction](../pipeline-storage.md) and the
[planned state transitions](../pipeline-state-design.md).

## Original local checks before access was restored

Windows, Python 3.12.10, pytest 8.3.4, ruff 0.15.12. Commands below use that Python
interpreter; source imports resolve to the tested checkout's `src` directory.

```text
python -m pytest tests/test_pipeline_contracts.py tests/test_pipeline_store.py -q
74 passed in 4.78s

python -m pytest tests/test_pipeline_contracts.py tests/test_pipeline_store.py tests/test_store.py tests/test_contracts.py tests/test_scheduler_leases.py -q
112 passed in 12.64s

python -m ruff check .
All checks passed!
```

With `PYTHONPATH=src`:

```text
python -m examples.pipeline.admission_smoke
{
  "result": "passed",
  "build_id": "090c7d3b-586f-49a0-8b84-21a8fd1044cb",
  "source_commit": "cccccccccccccccccccccccccccccccccccccccc",
  "requests": 1,
  "scheduler_jobs": 0,
  "duplicate_created": false,
  "restart_preserved_identity": true,
  "cluster_execution": "not exercised"
}
```

| Objective | Evidence |
| --- | --- |
| Reusable source and operator configuration | Generic JSON fixtures load; strict nested models reject unknown privilege fields and ambiguous identities |
| Safe preliminary build input selection | Relative path validation and resolved escape rejection; existing context/Dockerfile checks |
| Unambiguous service routing | Repository/branch selection; catalog rejects source, release, and publication conflicts |
| Stable automatic source admission | Same event or another delivery of the same commit returns one original build ID |
| Concurrency | Six separate SQLite connections race on identical and distinct delivery IDs; one request wins |
| Restart | Recreated store preserves original request and replay identity |
| Partial persistence failure | Injected failure between request and receipt rolls back both; retry succeeds |
| Existing database compatibility | Pre-admission schema containing an existing job gains the new tables without losing the job |
| Scope and bounded enumeration | Explicit project scope; durable sequence cursor; bounded page size |
| No premature execution | Admission smoke observes zero scheduler jobs; no image publication or deployment operation exists in this slice |

## Historical access blockers (superseded by the resumed checks)

`python -m pytest` collected 550 tests but stalled before the first HTTP test
completed. It was interrupted rather than reported as a passing suite.

The same first test was then run against an untouched archive of the base commit:

```text
python -m pytest tests/test_api.py::test_health_and_goblins_endpoints -vv -o faulthandler_timeout=10
collected 1 item
Timeout (0:00:10)!
```

The diagnostic stack stops in `socket.accept`, called by
`socket._fallback_socketpair` during the Windows asyncio event loop's
`_make_self_pipe`. Starlette TestClient is waiting for the AnyIO portal to start.
A bounded diagnostic wrapper stopped that baseline test process after 30 seconds.
This reproduces without the pipeline changes. The complete HTTP suite remains
unverified in this execution environment.

Docker engine access returns permission denied on its named pipe. The local
Kubernetes API connection is denied by the session's socket permissions.
Therefore no Docker/Helm admin runtime audit, screenshots, multi-node run,
live rollout, identity isolation, or cluster recovery result is claimed here.

These were the observed restrictions at the original pause. The resumed checks
above supersede those access results without completing the remaining delivery
acceptance matrix in the [roadmap](../pipeline-delivery-roadmap.md).
