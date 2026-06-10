# Goblin Container Contract

This is the canonical worker interface for Goblin King.

A goblin is always an OCI/Docker container. Goblin King schedules containers, not
language runtimes. The code inside the container may be Python, Go, Rust, Node.js, Java,
.NET, Ruby, PHP, shell, container-wrapped WASI/WebAssembly, or any other runtime that
can obey this contract.

Python helpers and package entry points are optional conveniences for definitions,
tests, and local debugging. They are not the worker model.

## Contract Layers

Goblin King keeps four concepts separate:

1. Goblin registry metadata: kind, display name, module metadata, retry defaults, and
   timeout defaults.
2. Worker image mapping: build context, Dockerfile, and image tag used by the runtime.
3. Container runtime contract: environment variables, mounted files, result envelopes,
   artifacts, logs, events, heartbeats, and exit behavior.
4. Optional language helpers: SDKs or generated code that make the contract easier to
   implement in one language.

## Required Environment Variables

Every short-running worker container receives these variables:

| Variable | Meaning |
| --- | --- |
| `GOBLIN_RUN_ID` | Durable run ID assigned by the King. |
| `GOBLIN_JOB_ID` | Durable job ID, when available. |
| `GOBLIN_KIND` | Goblin kind being executed. |
| `GOBLIN_WORKER_ID` | Runtime worker/container owner ID for heartbeat records. |
| `GOBLIN_INPUT_PATH` | Mounted JSON input file path. |
| `GOBLIN_CONTEXT_PATH` | Mounted JSON context file path. |
| `GOBLIN_RESULT_PATH` | Mounted fallback result JSON file path. |
| `GOBLIN_ARTIFACT_ROOT` | Mounted writable artifact directory. |
| `GOBLIN_REDIS_URL` | Redis URL used for result transport. |
| `GOBLIN_HEARTBEAT_REDIS_URL` | Redis URL used for heartbeat transport. |
| `GOBLIN_HEARTBEAT_CHANNEL` | Redis pub/sub channel for heartbeat envelopes. |
| `GOBLIN_HEARTBEAT_KEY` | Redis list key used for heartbeat ingestion. |
| `GOBLIN_HEARTBEAT_INTERVAL_SECONDS` | Requested heartbeat interval for long work. |

Workers may receive additional runtime metadata variables in later phases. Unknown
variables must be ignored.

## Mounted Paths

The runtime mounts a per-run directory containing:

```text
input.json
context.json
result.json
artifacts/
```

Workers must treat `input.json` and `context.json` as read-only. Workers may write only
to `result.json` and the artifact directory unless the worker image declares and
receives additional explicit mounts in a later phase.

## Input JSON

The input file contains the job input object submitted through the API, CLI, schedule,
fanout, or retry path. It must be a JSON object.

Example:

```json
{
  "message": "hello from Goblin King",
  "count": 1
}
```

## Context JSON

The context file contains run metadata the worker may use for correlation and artifact
paths.

Example:

```json
{
  "run_id": "run-123",
  "artifact_root": "/goblin/artifacts",
  "metadata": {
    "job_id": "job-123",
    "kind": "example.hello"
  }
}
```

## Result JSON

Every worker must write a result envelope to `GOBLIN_RESULT_PATH`. When Redis is
available, workers should also write the same envelope to
`goblin-king:results:{GOBLIN_RUN_ID}`.

Successful result:

```json
{
  "status": "success",
  "data": {
    "message": "Hello World"
  },
  "artifacts": [],
  "metrics": {},
  "handoff": [],
  "error": null
}
```

Failed result:

```json
{
  "status": "failed",
  "data": {},
  "artifacts": [],
  "metrics": {},
  "handoff": [],
  "error": "explain the failure"
}
```

The result envelope fields are:

| Field | Required | Meaning |
| --- | --- | --- |
| `status` | Yes | `success` or `failed`. |
| `data` | Yes | JSON object returned to the caller. |
| `artifacts` | Yes | Artifact metadata list. |
| `metrics` | Yes | Small scalar metrics. |
| `handoff` | Yes | Structured follow-up payloads. |
| `error` | Yes | Failure text or `null`. |

## Artifacts

Workers write artifact bytes under `GOBLIN_ARTIFACT_ROOT` and return metadata in the
result envelope:

```json
{
  "name": "report.txt",
  "uri": "report.txt",
  "media_type": "text/plain"
}
```

Artifact URIs must be relative paths under the artifact root unless a future storage
provider explicitly supports another URI scheme. Workers must not return path traversal
segments or files outside the mounted artifact root.

## Logs

Workers may write normal diagnostic output to stdout and stderr. Logs should be concise
and must not contain secrets. Structured data that callers need should go in the result
envelope, artifacts, events, metrics, or handoff payloads instead of only appearing in
logs.

## Progress And Events

Short jobs can report progress by writing durable events through supported transport
helpers when available. A worker that cannot publish events directly should still write
enough progress information to logs or artifacts for debugging.

Event payloads must be JSON objects and should include the run ID, job ID, kind, and a
small progress/status payload.

## Heartbeats

Workers that run long enough to need liveness proof should publish heartbeat envelopes
using the provided Redis heartbeat variables. Short workers may publish only `running`
and `completed` heartbeats.

Heartbeat envelope:

```json
{
  "owner_id": "worker-run-123",
  "owner_type": "worker",
  "status": "running",
  "last_seen_at": "2026-06-09T00:00:00Z",
  "job_id": "job-123",
  "run_id": "run-123",
  "payload": {
    "kind": "example.hello"
  }
}
```

Malformed heartbeat envelopes are recorded as failed heartbeat events by the host.

## Exit Codes

The result envelope is the source of truth. Exit codes still matter:

- Exit `0` after writing a successful result.
- Exit nonzero after writing a failed result.
- If the process exits nonzero without a result, Goblin King records a failed run.
- If the process times out, Goblin King records a timed-out run.

## Timeout And Cancellation

Timeouts are assigned by registry/schedule/job policy, not by the worker. Workers should
handle termination signals when their runtime supports it and write a best-effort failed
or cancelled result if there is time.

Goblin King may stop the container when timeout or hard-kill controls require it.

## Security And Resources

Workers should be built to run with least privilege:

- Run as non-root where practical.
- Keep writable paths limited to `GOBLIN_RESULT_PATH` and `GOBLIN_ARTIFACT_ROOT`.
- Avoid leaking secrets to logs, results, events, metrics, and artifacts.
- Support read-only root filesystems where practical.
- Treat CPU, memory, process, network, log, and artifact limits as platform policy.

Per-goblin resource policy is documented in
[`docs/goblin-resource-policies.md`](goblin-resource-policies.md). Current runtime
enforcement covers policy loading, ceiling validation, timeouts, retries, Docker
resource flags, Kubernetes resource fields, artifact count/byte checks where artifacts
are inspectable, scheduler concurrency deferral, safe artifact paths, scoped hard
termination, audit, and events.

## WASI And WebAssembly

WASI/WebAssembly goblins are also containers. The supported model is:

```text
Goblin King launches a container.
The container runs a pinned WASI runtime.
The WASI runtime executes a .wasm module.
The .wasm module reads mounted input/context and writes result/artifacts.
The container exits.
Goblin King records the run.
```

Goblin King does not schedule `.wasm` modules directly in this contract. Native WASI
scheduling, Kubernetes runtime classes, and host-level Wasm runtimes are future optional
infrastructure work.

## Compatibility

Workers must tolerate additional environment variables and context metadata fields.
Goblin King must preserve the result envelope shape or document compatibility changes in
the compatibility matrix and migration guide.

The King welcomes every language at court. The contract is the crown.
