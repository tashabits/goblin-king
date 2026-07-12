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
Redis pub/sub and Stream payloads receive the same persisted sequence.

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

New `RunRecord` values reject `finished_at < started_at`. Scheduler success, failure,
validation rejection, timeout, retry, and unexpected adapter failure all use the same
causal timestamp helpers. Cancellation currently has no Run attempt; its
`job.cancelled` event participates in the event sequence.

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

Implementation commit `5c4ff69` was built as an exact scheduler image and exercised
against the real Docker worker path on July 12, 2026. The scheduler ran as UID/GID
`10001:10001` with a read-only root filesystem, read-only `/config`, and a separately
mounted writable `/data` volume. Redis and every worker ran on the Compose network;
only the scheduler received the Docker socket.

Six short `example.echo` jobs completed through the worker image:

| Run | Job | Started (UTC) | Finished (UTC) |
| --- | --- | --- | --- |
| `1f157538-8fb8-4132-b923-f0e4569fcf92` | `2f22710f-e3b3-4f81-8402-53908a4f00e6` | `20:09:56.940260` | `20:09:58.782208` |
| `56ffbb75-fcfa-44ff-9c7d-a88141a9054e` | `68d0df7e-28ea-4d09-a98c-22caccef305d` | `20:11:22.813396` | `20:11:23.706855` |
| `6d3904a9-556e-4841-b779-f0991d231bda` | `4910a2f8-9119-46be-a001-40daca569a5e` | `20:11:23.732078` | `20:11:24.607133` |
| `f8460665-8d26-40f7-ac0a-8523a80bb80b` | `4ea20440-c45b-46a2-87df-9ab4fc38665c` | `20:11:24.630164` | `20:11:25.460403` |
| `3630ea3e-b6e9-4a61-a3a7-67d01a7e48c5` | `80a28c45-5886-492c-9a0d-db377391c77e` | `20:11:25.489200` | `20:11:26.316865` |
| `3dcaa9cf-a87f-45bb-b471-adf6059d9dc4` | `4b2292a0-301e-474e-a8a7-9bd0d8642303` | `20:11:26.342101` | `20:11:27.191168` |

An assertion pass read the resulting SQLite database directly and reported:

```text
PASS runs=6 events=42 sequences=1..42
PASS every Run satisfies started_at <= finished_at
PASS each job orders leased < running < worker.started < worker.completed < completed
PASS every terminal event follows its Run finish
```

The automated regression suite additionally injects backward wall-clock movement into
immediate success and failure attempts, exercises retries and timeouts, verifies
concurrent event writers, proves that cleanup never reuses a sequence, and checks both
legacy sequence backfill and historical Run repair.

The full Python gate passed 315 tests with Ruff clean. The admin gate passed all 9
Vitest tests, its TypeScript check, and its Vite production build. A clean `npm ci`
audited 118 packages with no known vulnerabilities.
