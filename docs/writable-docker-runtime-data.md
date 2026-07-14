# Writable Docker Runtime Data

Docker task execution needs one host-visible writable directory for contract input,
context, fallback results, and artifacts. That directory must not depend on the
scheduler process working directory: hardened deployments commonly run with a
read-only root filesystem and mount project configuration read-only at `/config`.

## Configuration Rule

When `GOBLIN_KING_DOCKER_DATA_VOLUME` names a Docker volume, also configure
`GOBLIN_KING_RUN_ROOT` as an absolute path inside the scheduler container's mount of
that same volume. The parent of the run root is the Docker data root. Relative artifact
paths are resolved beneath that data root.

For example, this placement uses `/data/runs` and `/data/artifacts`:

```yaml
services:
  scheduler:
    read_only: true
    working_dir: /config
    environment:
      GOBLIN_KING_DOCKER_DATA_VOLUME: my-project_goblin-data
      GOBLIN_KING_RUN_ROOT: /data/runs
      GOBLIN_KING_DOCKER_NETWORK: my-project_default
    volumes:
      - ./goblin-config:/config:ro
      - goblin-data:/data
      - /var/run/docker.sock:/var/run/docker.sock

volumes:
  goblin-data:
    name: my-project_goblin-data
```

The scheduler can receive the same value explicitly:

```bash
goblin-king scheduler run \
  --runtime docker \
  --run-root /data/runs \
  --db /data/goblin-king.sqlite3
```

`--run-root` is also available on `scheduler run-once`, `jobs submit`, `workers
validate`, and `workers validate-image`. The environment value remains useful for API
and notebook execution paths that construct Docker runtimes internally.

## Fail-Fast Behavior

If a named Docker data volume is configured without a run root, startup fails with an
actionable configuration error. A relative run root is rejected in named-volume mode.
This is intentional: silently resolving `.goblin-king/runs` below the process working
directory can target a read-only config mount and strand work after it is leased.

Without a named data volume, local host execution retains the
`.goblin-king/runs` default. Standalone contract validation continues to use a temporary
directory unless an explicit run root or environment value is supplied.

Filesystem and launch errors during a Docker worker attempt become failed result
envelopes. A just-in-time validation exception becomes a failed Run, clears the job
lease, emits `validation.scheduling_rejected`, and does not end the scheduler pass.

## Lease Recovery

Jobs in `leased` or `running` state become claimable again after `leased_until`. This
allows a replacement scheduler to recover a process that stopped after marking a job
running but before recording its Run. The recovered execution increments the attempt
count and follows the normal retry and terminal-state rules.

Each live scheduler renews every lease in its claimed synchronous batch, including the
attempt currently executing and claimed jobs waiting behind it. Renewal is conditional
on the scheduler still owning an active `leased` or `running` record, so a late renewal
cannot take a job back from a replacement scheduler. Claim transactions and renewals
are serialized in SQLite to keep the expiry boundary deterministic across scheduler
processes. When a scheduler stops, its renewal activity stops as well and the final
persisted deadline makes the job recoverable.

Recovery remains an at-least-once boundary because a scheduler process can fail at an
external side effect or at the lease boundary. A worker that performs externally
visible side effects should continue to use the stable job ID as an idempotency key.

## Verification

For a hardened Compose deployment:

1. Make the scheduler root filesystem and `/config` mount read-only.
2. Mount the named data volume at the parent of `GOBLIN_KING_RUN_ROOT`.
3. Run first-use worker validation and one task.
4. Confirm input, context, result, and artifact files appear beneath the data mount.
5. Inject an unwritable run root and confirm a failed Run and terminal job event are
   recorded while the scheduler continues.
6. Keep a job active beyond its initial lease deadline and confirm a second scheduler
   cannot claim either that job or another job waiting in the same synchronous batch.
7. Stop a scheduler after a job reaches `running`, wait for the last renewed lease to
   expire, start a replacement scheduler, and confirm the job is reclaimed
   deterministically.

Only the scheduler/control-plane container should receive the Docker socket. Worker
containers receive the narrow data-volume mount, declared resource policy, and scoped
network—not Docker authority.

## Observed Proof

The implementation commit `d7b34d6` was exercised on 2026-07-12 with image
`goblin-king:issue-144-d7b34d6`. The scheduler ran as UID/GID 10001, with its root
filesystem read-only, `/config` mounted read-only, a named volume mounted at `/data`,
and `/data/runs` supplied as the run root.

- Job `5d119b19-722f-4de0-9f8d-d68ab098336a` completed as Run
  `b686b57e-c4b6-417b-82bd-e86d8691e96e`.
- The named volume contained validation and task `input.json`, `context.json`, and
  `result.json` files under `/data/runs`, plus sibling `/data/artifacts` directories.
- A fresh database run with `/config/runs` intentionally targeted the read-only mount.
  Job `7a97f725-2014-4384-af9b-af3a131d05fa` became failed with Run
  `e90a4162-3491-4f82-b4b1-b5d5e365f7a1` and the recorded `Errno 30` validation
  reason.
- A second scheduler pass against the failure database returned no work instead of
  ending the process or reclaiming the terminal job.
- Repository-wide validation passed `python -m ruff check .` and 306 tests in 85.19
  seconds. An earlier focused writable-path and lifecycle gate passed 70 tests in 23.19
  seconds.

The commands used the fixed `example.echo` worker image, the existing local Redis
service, a disposable named volume, and the mounted Docker socket only in the scheduler
container. The task worker did not receive the socket.
