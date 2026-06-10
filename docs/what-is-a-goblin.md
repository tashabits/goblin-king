# What Is A Goblin?

A goblin is a short-lived, contract-compliant OCI/Docker container task that Goblin King
can queue, schedule, run, observe, and record.

The container is the scheduled unit. The language inside the container is an
implementation detail.

## A Goblin Is

- A container image with an entrypoint.
- Registered by a stable goblin kind such as `example.hello`.
- Launched with mounted input, context, result, and artifact paths.
- Expected to write a valid result JSON envelope.
- Expected to exit clearly with success or failure.
- Isolated from the host project by the container boundary.

## A Goblin Is Not

- A Python function, even if Python helpers exist.
- A Celery task, shell script, or package plugin by itself.
- A forever-running daemon in the short-job model.
- Allowed to invent its own result protocol.
- Allowed to choose its own runtime resource limits.

## Why Containers?

Containers give each goblin its own dependency set, process space, filesystem view, and
runtime packaging. That boundary lets one project run a Rust goblin, a Node.js goblin,
a shell goblin, and a Python goblin through the same scheduler and result model.

Containers are useful isolation, not magic armor. Stronger isolation depends on runtime
settings such as non-root users, read-only root filesystems, dropped capabilities,
resource limits, network controls, and secret scoping.

## How Goblin King Runs One

1. The API, CLI, schedule, fanout, or retry path creates a job.
2. The scheduler claims due work.
3. The runtime launches the configured worker container image.
4. The worker reads `input.json` and `context.json`.
5. The worker writes `result.json`, artifacts, logs, events, and heartbeats.
6. Goblin King records the run and final job status.

The King welcomes many languages. The contract keeps them from arguing in the hallway.
