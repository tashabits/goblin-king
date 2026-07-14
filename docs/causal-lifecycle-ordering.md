# Causal Lifecycle Ordering

Goblin King records two related but different facts for lifecycle evidence:

- a UTC timestamp for human correlation with logs and external systems;
- a durable integer sequence for the causal order in which the control plane persisted
  events.

Consumers must use `sequence` as the authoritative event order. UTC timestamps remain
useful presentation metadata, but host, VM, and container clocks can repeat, slew, or
move backward while a short task is running.

## Why Wall Time Is Not Enough

A scheduler can read wall time, launch a sub-second worker, and then read an earlier
wall time after the host synchronizes its clock. Sorting those raw values can show
completion before lease or start, even though the calls occurred in the correct order.
It can also produce a negative Run duration.

Sorting in an API client hides the symptom without repairing the durable record. The
control plane therefore establishes order at write time.

## Event Contract

Every persisted `EventRecord` includes:

- `sequence`: a positive, database-assigned integer;
- `created_at`: a timezone-aware UTC timestamp;
- the existing event type, source, IDs, and payload.

SQLite assigns `sequence` while holding an immediate write transaction. Independent
API and scheduler connections therefore cannot claim the same value. Event listing and
`after_id` pagination use this sequence rather than timestamps or UUID lexical order.
Redis pub/sub and Stream payloads receive the same persisted sequence. The Redis Stream is the
reliable ordered rail: delivery uses the durable sequence, replays predecessors before later
events, and resumes after a process restart. Pub/sub remains a best-effort notification rail; a
consumer that combines notifications from multiple publishers must deduplicate and order them by
`sequence` or read the durable API/Stream.

The store also clamps each new event timestamp after the preceding persisted event. If
the producer clock repeats or moves backward, the next timestamp advances by one
microsecond. Equal Run and worker timestamps may still occur at representation
boundaries; `sequence` remains authoritative whenever values tie.

## Run Contract

For a scheduler attempt:

1. `job.leased` is persisted.
2. `job.running` is persisted.
3. `Run.started_at` is placed after `job.running`.
4. Runtime worker events are persisted in sequence.
5. `Run.finished_at` is placed after its start and the latest worker event.
6. The terminal or retry event is placed after `Run.finished_at`.

Once a Docker runtime emits `worker.started`, it emits exactly one terminal worker
lifecycle event for that attempt. A result rejected by the effective artifact policy
ends with `worker.failed`; its payload retains the goblin `kind`, uses
`phase: artifact_policy`, and includes the policy error. The returned failed
`GoblinResult` remains the existing public shape.

New `RunRecord` values reject `finished_at < started_at`. Scheduler success, failure,
validation rejection, timeout, retry, and unexpected adapter failure all use the same
causal timestamp helpers. Cancelling queued work creates no Run. When cancellation wins while a
worker is already active, the job remains `cancelled`; the eventual Run preserves the worker's
actual result using the existing Run statuses, and no later job-terminal event overwrites the
cancellation. This retains the public status and result-envelope shapes while preserving execution
evidence.

Timeout decisions use `time.monotonic()` elapsed seconds, not UTC subtraction. UTC
time is presentation and correlation data; monotonic elapsed time is duration data.

## Upgrade Behavior

Opening an existing database adds and backfills event sequences in original SQLite row
insertion order, then creates a unique sequence index. Existing Runs whose
`finished_at` precedes `started_at` are repaired to equality. Their historical event
sequence remains available for the more detailed causal order; the migration does not
invent an unobserved duration.

The migration is additive to API payloads. Clients that tolerate additional JSON fields
continue to work. Typed clients should add `sequence: number` to their event model and
use it for cursors, ordering, and displayed evidence.

Existing Python construction remains compatible: an EventRecord created without `sequence`
defaults to zero until persistence assigns a positive value, and `SQLiteStore.save_event()` keeps
its historical no-value return. The public Run status union and nullable `finished_at` model field
are unchanged. Durable storage normalizes a missing terminal finish to the start timestamp, and the
upgrade migration applies the same repair to historical rows.

## Operator Verification

Exercise both an immediate-success and immediate-failure worker, then inspect:

```bash
goblin-king jobs list
goblin-king events list --limit 50
goblin-king runs show <run-id> --with-job
```

Verify that:

- event sequences strictly increase;
- the event list follows lease, running, worker start, worker terminal, and job terminal
  order;
- every terminal Run has `started_at <= finished_at`;
- a retry receives a later attempt and later causal events;
- timeout classification follows actual elapsed work even during a wall-clock rollback.

The React admin prefixes durable events with `#<sequence>` so human reports show the
same ordering contract as API and Stream consumers.

## Verified Docker Proof

Final implementation commit `0435451` was built as image
`sha256:de9c7d03a7db527685d0a79218827aece7f429b73fd1667518d7e0b9de0827ad` and
exercised against the real Docker worker path on July 12, 2026. The scheduler ran as
UID/GID `10001:10001` with a read-only root filesystem, read-only `/config`, and a
separately mounted writable `/data` volume. Redis and every worker ran on the Compose
network; only the scheduler received the Docker socket.

Three short `example.echo` jobs completed through the worker image:

| Run | Job | Started (UTC) | Finished (UTC) |
| --- | --- | --- | --- |
| `b9064102-bb1b-4e4a-8907-39f779867ede` | `77747b5e-25bd-496f-a2a4-a140c6e98e86` | `20:58:47.051522` | `20:58:48.929237` |
| `e686e1b6-521c-4875-bee3-85c3aa4378a4` | `d952708f-fe11-41cc-94cf-d964d7c5f0ae` | `20:58:48.953229` | `20:58:49.889968` |
| `63e40c93-c180-42d1-9efe-9a9a7c1ccd38` | `48e8fc96-c7ed-450e-a7bb-e3582ed84ddc` | `20:58:49.912798` | `20:58:50.887980` |

An assertion pass read the resulting SQLite database directly and reported:

```text
PASS runs=3 events=21 sequences=1..21
PASS every Run satisfies started_at <= finished_at
PASS each job orders leased < running < worker.started < worker.completed < completed
PASS every terminal event follows its Run finish
PASS redis_stream_ids=1-0..21-0 count=21
PASS Redis Stream payload sequence exactly matches SQLite
```

The automated regression suite additionally injects backward wall-clock movement into
immediate success and failure attempts, exercises retries and timeouts, verifies
concurrent event writers, proves that cleanup never reuses a sequence, serializes independent
Stream publishers, recovers ambiguous Redis acknowledgements, drains backlogs, preserves active
cancellation and stale-attempt ownership, and checks both legacy sequence backfill and historical
Run repair.

The full Python gate passed 330 tests with Ruff clean. The admin gate passed all 9
Vitest tests, its TypeScript check, and its Vite production build. A clean `npm ci`
audited 118 packages with no known vulnerabilities.
