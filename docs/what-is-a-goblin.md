# What Is A Goblin?

A goblin is a validated container-backed workload managed by Goblin King. Goblins may
be short-lived tasks, managed long-running services, notebook-authored functions,
notebook-authored ASGI services, or deployment-local Directory entries.

The container boundary is the core unit. The language inside the container is an
implementation detail, and notebook-authored goblins still execute through configured
runner containers.

## A Goblin Is

- A validated container-backed workload with a stable kind or Directory name.
- Registered or resolved through project config, registry metadata, notebook APIs, or
  the deployment-local Directory.
- Run through a configured container image or configured runner container.
- Governed by validation proof and resource policy before runtime use.
- Scoped to a project or deployment and visible through API, admin, CLI, notebook, or
  Directory surfaces.
- Isolated from the host project by the container boundary.

## A Goblin Is Not

- Raw local Python execution, even when Python helpers author notebook goblins.
- A Celery task, shell script, or package plugin by itself.
- An arbitrary unmanaged daemon.
- Allowed to invent its own result protocol.
- Allowed to choose its own runtime resource limits.
- A public marketplace submission by default.

## Workload Lifecycles

Task goblin:

A short-lived container that reads input, writes result/artifacts/logs, and exits.

Service goblin:

A long-running container-backed workload that becomes ready, answers probes/proxy
requests, reports health/logs, and is stopped or managed by Goblin King.

Notebook function goblin:

A source-authored Python function bundled and executed inside the configured notebook
function runner container.

Notebook service goblin:

A source-authored ASGI/FastAPI service bundled and executed inside the configured
notebook service runner container.

Directory goblin:

A deployment-local shared goblin that has been submitted, validated, reviewed, and
published for other authorized users in the same Goblin King deployment.

## Why Containers?

Containers give each goblin its own dependency set, process space, filesystem view, and
runtime packaging. That boundary lets one project run a Rust goblin, a Node.js goblin,
a shell goblin, and a Python goblin through the same scheduler and result model.

Containers are useful isolation, not magic armor. Stronger isolation depends on runtime
settings such as non-root users, read-only root filesystems, dropped capabilities,
resource limits, network controls, and secret scoping.

## How Goblin King Runs A Task

1. The API, CLI, schedule, fanout, or retry path creates a job.
2. The scheduler claims due work.
3. The runtime launches the configured worker container image.
4. The worker reads `input.json` and `context.json`.
5. The worker writes `result.json`, artifacts, logs, events, and heartbeats.
6. Goblin King records the run and final job status.

Services add readiness/probe/proxy lifecycle state instead of ending with only a result
envelope. Directory entries add review and publication before other users can invoke
them by name. In every case, validation and resource policy stay in front of runtime
use.

The King welcomes many languages. The contract keeps them from arguing in the hallway.
