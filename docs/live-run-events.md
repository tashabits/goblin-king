# Live run events

Fixed task workers can publish bounded progress and text output while they are still running. The
channel is additive: workers that only write a final result or heartbeat continue to work without
any change, and all existing Job, Run, result, heartbeat, and status JSON shapes remain unchanged.

The channel is intended for honest user-facing task feedback. It is not a general message bus and
does not grant a worker any scheduler or API operation.

## Quick start for Python workers

Workers that install the `goblin-king` package can build a publisher from the runtime-provided
environment:

```python
from goblin_king import RunEventPublisher

events = RunEventPublisher.from_environment()
events.stdout("loading input\n")
# Perform enough work to respect the 50 ms publication interval.
events.progress(25, "input loaded")
```

`emit()` reports contract or transport errors to worker code. `try_emit()` returns `None` instead,
which is useful when progress is advisory and must never change the task result:

```python
events.try_emit("message", {"phase": "indexing", "items": 42})
```

The `example.progress` fixed worker is a self-contained reference for workers that deliberately do
not install the Python package.

## Worker contract

Docker and Kubernetes inject the same variables into the worker container:

| Variable | Meaning |
| --- | --- |
| `GOBLIN_RUN_EVENT_CONTRACT_VERSION` | `goblin-king/v1alpha1` |
| `GOBLIN_RUN_EVENT_REDIS_URL` | Existing worker result/heartbeat Redis transport |
| `GOBLIN_RUN_EVENT_STREAM` | Exact durable Redis Stream for this run |
| `GOBLIN_RUN_EVENT_SEQUENCE_KEY` | Exact run-local monotonic counter |
| `GOBLIN_RUN_EVENT_RATE_KEY` | Exact run-local publication-rate gate |
| `GOBLIN_RUN_EVENT_MAX_EVENTS` | `256` retained events |
| `GOBLIN_RUN_EVENT_MAX_PAYLOAD_BYTES` | `4096` UTF-8 JSON bytes per payload |
| `GOBLIN_RUN_EVENT_MIN_INTERVAL_MS` | `50` ms between accepted events |
| `GOBLIN_RUN_EVENT_TTL_SECONDS` | `3600` seconds of retained replay |

An accepted stream entry has one `event` field containing this JSON envelope:

```json
{
  "sequence": 7,
  "created_at": "2026-07-16T23:58:01.125Z",
  "event_type": "progress",
  "run_id": "5b091deb-8359-4afb-895e-107425b46d91",
  "payload": {
    "percent": 35,
    "message": "indexed 350 of 1000 documents"
  }
}
```

`event_type` is one of `progress`, `stdout`, `stderr`, or `message`. Sequence values are strictly
increasing for a run but may contain gaps if a worker loses transport after reserving a sequence.
Consumers must order and resume by `sequence`, never by wall-clock time.

The supported publication transaction is:

1. acquire `GOBLIN_RUN_EVENT_RATE_KEY` with `SET ... NX PX 50`;
2. reserve a sequence with `INCR GOBLIN_RUN_EVENT_SEQUENCE_KEY`;
3. `XADD` the JSON event to `GOBLIN_RUN_EVENT_STREAM` with exact `MAXLEN 256`;
4. set the stream and sequence-key expiry to 3600 seconds.

Use the provided publisher where possible. It validates that the supplied stream, sequence, and
rate keys belong to `GOBLIN_RUN_ID`, enforces the fixed upper bounds, and uses an exact rather than
approximate stream trim.

## Authenticated client API

Replay retained events after a known sequence:

```http
GET /runs/{run_id}/events?after_sequence=7&limit=100
Authorization: Bearer <project token>
```

The response is additive and does not alter `GET /runs/{run_id}`:

```json
{
  "items": [
    {
      "sequence": 8,
      "created_at": "2026-07-16T23:58:01.250Z",
      "event_type": "stdout",
      "run_id": "5b091deb-8359-4afb-895e-107425b46d91",
      "payload": {"text": "batch 4 complete\n"},
      "job_id": "d650b954-028b-49c7-8e68-25922090fb42",
      "project_id": "project-a"
    }
  ],
  "meta": {"limit": 100, "offset": 7, "count": 1},
  "next_sequence": 8
}
```

For live delivery, connect to:

```text
WS /ws/runs/{run_id}/events?token=<project-token>&after_sequence=7
```

The WebSocket first replays retained events after the requested sequence, then sends new validated
event records in order. Reconnect with the last received sequence. The 256-event window is bounded,
so a client that remains disconnected for longer than the retained window may observe a sequence
gap and should say so rather than inventing missing output.

Both endpoints load the persisted Run and Job before reading Redis. The API derives `project_id`
and `job_id` from those records, ignores project claims in worker bytes, and applies normal project
authorization. A project token cannot read or stream another project's run. Workers receive no API
token, scheduler token, administration credential, or route that can mutate another run. This
feature reuses the Redis URL already supplied for result and heartbeat transport and adds no new
secret class.

The Scheduler inserts the run-scoped `running` identity immediately before worker execution and
finalizes that same row after the worker returns. Finalization permits one `running`-to-terminal
transition with the original job, kind, project, and attempt lineage. It rejects duplicate or foreign
transitions atomically, including any accompanying job-state update. The general `save_run()` API
remains insert-only, so existing callers still receive a duplicate-key error instead of an implicit
replacement.

Malformed, oversized, foreign-run, duplicate, and non-monotonic entries are not projected to API
clients. A Redis outage returns HTTP 503 or closes the WebSocket with a transport error; it does not
change the task's final result.

## Backend parity and compatibility

`DockerRuntime` and `KubernetesRuntime` call the same environment builder. Kubernetes does not
depend on Pod log scraping, and Docker does not depend on the wrapper process exiting. A fixed
worker therefore uses precisely the same publication code under either backend.

Existing workers may ignore every `GOBLIN_RUN_EVENT_*` variable. Existing API clients may ignore
the new routes. No field was added to Job, Run, `GoblinResult`, heartbeat, or existing event
responses. Result completion, retry, timeout, cancellation, artifacts, and scheduler leases retain
their established semantics.

The channel intentionally has finite retention. Final durable business data still belongs in the
result envelope or retained artifacts. Run events are replayable operational feedback, not an
unbounded log store.
