# Pipeline Foundation Local Proof

Scope: service/catalog contracts and durable source admission. This is a partial
foundation, not a running repository delivery controller or Kubernetes acceptance.
Base: `4a5ebe36060487e02b97a4d83cdf02560b670dd0`.

## Passed checks

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

## Blocked checks

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

The full local suite and applicable runtime proof must be completed before this
phase is considered ready to merge. Do not promote the local admission checks to
the cluster acceptance matrix in the [roadmap](../pipeline-delivery-roadmap.md).
