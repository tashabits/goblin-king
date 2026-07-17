# Goblin Contract Validation

Goblin King does not schedule arbitrary unvalidated container images by default. A
container-backed goblin is runnable only when its current resolved image identity has
passing proof for the declared [Goblin Container Contract](goblin-container-contract.md)
version and validator version.

Use `goblin-king workers validate` to run worker images with contract mounts and verify
that each worker writes a valid result envelope. Docker remains the default runtime.
Use `--runtime kubernetes` or the admin-authenticated Kubernetes validation API for a
generic registry worker that must establish proof inside a cluster. The short rule is:
validate first, then schedule.

## Why Validation Is Mandatory

The scheduler trusts worker containers to read mounted input/context files, write a
result envelope, report artifact metadata honestly, and exit in a way Goblin King can
record. Validation catches the common adoption failures before normal work depends on
them: missing images, broken Dockerfiles, unsupported contract versions, missing
`result.json`, malformed results, artifact metadata that points nowhere, and workers
that only succeed when they receive non-contract environment.

The gate protects both demo goblins and project-defined goblins. Python helpers are
optional; the container contract is the worker interface.

In named-volume Docker deployments, validation and normal execution share the explicit
writable root configured by `GOBLIN_KING_RUN_ROOT` or `--run-root`. The value must be an
absolute path inside the scheduler's mount of `GOBLIN_KING_DOCKER_DATA_VOLUME`. See
[Writable Docker Runtime Data](writable-docker-runtime-data.md) for hardened Compose
placement and recovery behavior.

## What Is Validated

Validation checks:

- registry or project kind exists,
- worker image mapping exists,
- image reference resolves to an immutable local image identity,
- declared contract version is supported,
- worker context and Dockerfile exist when a build is requested,
- optional image build succeeds,
- optional prebuilt image availability check succeeds,
- worker runs with mounted input/context/result/artifact paths,
- `result.json` exists,
- result envelope validates as `GoblinResult`,
- `artifact://...` metadata points to files under the mounted artifact root.

The final artifact-file check above is available to Docker validation because the
validator can inspect the shared run directory. Kubernetes validation executes the same
configured worker through a Job with `backoffLimit: 0` and an active deadline. It loads
the final forwarded result, captures bounded worker and result-forwarder logs plus the
worker exit code, and only then deletes the transient Job and input ConfigMap. When
durable artifact retention is configured, the trusted forwarder validates and copies
declared bytes before publishing metadata, so validation returns retained artifact
records. Without durable storage, an artifact-bearing result fails explicitly and
publishes no artifact records; artifact-free results retain legacy behavior.

By default, a valid failed result envelope is still contract-valid. Use
`--require-success` when failed result envelopes should fail validation.

## Proof Lifecycle

Validation proof is tied to goblin kind, resolved image identity or digest, contract
version, validator version, validation timestamp, status, failure reasons, and the
effective runtime policy summary when one is available.

The scheduler checks proof immediately before Docker or Kubernetes execution. Docker
may perform just-in-time validation. Kubernetes rejects a generic worker with no current
proof and points the operator to the explicit Kubernetes validation operation; that
operation runs the contract Job and persists pass/fail proof before normal scheduling.
This prevents a fresh Helm installation from depending on a Docker daemon, a database
seed, or a first unvalidated workload.

An operator-reviewed definition may set `metadata.validation_input` to a JSON object when
the ordinary runtime input is intentionally slow, failing, destructive, or expensive. The
scheduler uses that object only for contract validation and still executes the exact queued
input afterward. Definitions without it retain exact-input validation. Invalid or non-JSON
metadata fails the validation boundary visibly instead of falling back to runtime input.

Docker proof uses the locally inspected immutable image ID. Kubernetes proof uses the
exact scheduler gate identity `kubernetes:<configured-image-reference>`. A digest-pinned
reference therefore yields an immutable configured identity and is preferred. A mutable
tag cannot reveal tag movement to the preflight gate; revalidate after changing or
reloading a tagged image. Keeping tag-only identity behavior is an explicit compatibility
limitation of this change; the validation path does not silently reinterpret existing
worker references or claim a tag is immutable.

## Failure Mapping

| Condition | Scheduler behavior | Operator fix |
| --- | --- | --- |
| No Docker validation proof exists | Run just-in-time Docker validation before execution. Execute only if it passes. | Run `goblin-king workers validate` with the same kind and input before scheduling routine work. |
| No Kubernetes validation proof exists | Reject before normal worker execution and name the Kubernetes repair command. | Call the admin Kubernetes validation API, or run `workers validate --runtime kubernetes` against the cluster and scheduler database. |
| Current image cannot resolve to a digest | Reject execution and persist failed validation proof. | Build, pull, or correct the worker image reference, then revalidate. |
| Existing proof is for an old digest | Revalidate the current digest. Reject execution if revalidation fails. | Re-run validation after rebuilding or pulling the image. |
| Validation proof is failed | Revalidate on the current attempt. Reject execution if it still fails. | Fix the worker contract failure shown in the validation record, then revalidate. |
| Contract version is unsupported | Reject validation and scheduling. | Update the worker or Goblin King deployment so both declare a supported contract. |
| Result file is missing or malformed | Reject execution and show the worker contract error. | Ensure the worker writes valid `result.json` to the mounted result path. |
| Artifact metadata points to missing files | Reject validation. | Write artifact bytes under the mounted artifact root before referencing them. |
| Named data volume has no absolute writable run root | Fail fast before scheduling. | Set `GOBLIN_KING_RUN_ROOT=/data/runs` or pass `--run-root /data/runs` inside the shared volume mount. |
| Validation setup raises after a lease | Persist a failed Run, clear the lease, emit a terminal validation event, and continue the scheduler pass. | Correct the writable path or Docker configuration, then submit or retry the job. |
| Passing proof exists for current digest | Allow execution. | No action needed until the image digest, contract version, validator version, or policy changes. |

## CLI Examples

```bash
python -m goblin_king.cli workers validate \
  --registry examples/cross-language-goblins.json \
  --images examples/cross-language-images.json \
  --input examples/cross-language-input.json \
  --build \
  --require-success
```

Project settings can be used directly, which is the preferred adopter workflow once a
project has a `GoblinProject` file:

```bash
python -m goblin_king.cli workers validate \
  --project examples/adopting-project/goblin-king-project.json \
  --input examples/input.json \
  --kind project.inline.hello \
  --build \
  --require-success
```

For a one-off prebuilt image, use `validate-image` before adding it to a project:

```bash
python -m goblin_king.cli workers validate-image \
  --image my-project/my-goblin:local \
  --kind my.project.goblin \
  --input examples/input.json \
  --require-success
```

Inspect persisted validation status:

```bash
python -m goblin_king.cli workers validation-status --db .goblin-king/goblin-king.sqlite3
```

The behavior examples include an intentional failure goblin, so validate them without
`--require-success`:

```bash
python -m goblin_king.cli workers validate \
  --registry examples/behavior-goblins.json \
  --images examples/behavior-images.json \
  --input examples/behavior-input.json \
  --build
```

The King accepts honest failure envelopes. He only gets annoyed when a worker
vanishes without paperwork.

## Kubernetes CLI And API

The CLI is useful for deployment jobs or operators whose kubeconfig can reach the
cluster and whose `--db` points at the scheduler's state:

```bash
goblin-king workers validate \
  --runtime kubernetes \
  --registry demo-goblins.json \
  --images demo-images.json \
  --input examples/input.json \
  --kind example.hello \
  --timeout-seconds 120 \
  --require-success \
  --result-forwarder-image registry.example/control@sha256:<digest> \
  --worker-image-pull-policy IfNotPresent \
  --result-forwarder-image-pull-policy IfNotPresent \
  --workload-image-pull-secret primary-registry \
  --db /data/goblin-king.sqlite3 \
  --json
```

`--build` and `--run-root` are rejected with this runtime because Kubernetes consumes a
preloaded or registry-accessible image. The JSON result includes `image_digest` (the
schema-compatible scheduler identity field), `result_status`, `exit_code`, `artifacts`,
`checks`, and `logs`.

For a Helm installation, prefer the admin operation so proof is stored in the mounted
chart database:

```bash
curl -X POST http://127.0.0.1:8000/admin/workers/validate-kubernetes \
  -H "Authorization: Bearer local-dev-token" \
  -H "Content-Type: application/json" \
  -d '{"kinds":["example.hello"],"input":{"message":"proof"},"require_success":true,"timeout_seconds":120}'
```

Omit `kinds` to validate every active registry definition. Unknown kinds and missing
worker mappings return failed validation entries. Every returned entry is persisted,
and the audit log stores only kind/pass/fail summaries rather than worker logs. See the
[fresh-chart proof](kubernetes-generic-worker-validation-proof.md) for the complete
sequence.

### Shared Kubernetes Runtime Configuration

Generic validation does not construct an independent validation-only runtime. The API
passes its typed `kubernetes_runtime` settings into the same factory used by scheduler
execution and notebook validation. The CLI builds that same typed settings object from
the established forwarder-image, worker/forwarder pull-policy, and repeatable pull-secret
options. The factory retains the runtime's shared namespace discovery, artifact-retention
settings, and bounded Pod diagnostics. A runtime settings file may also select
`restricted-v1`; its effective profile and per-kind ServiceAccount decision become part
of the same validation identity used by the scheduler. This prevents validation from
passing with a local/default forwarder or legacy Pod contract while normal scheduling
uses another image, policy, Secret set, namespace, retention boundary, security contract,
or diagnostic boundary. Legacy constructor calls and Docker validation remain unchanged.

When artifact retention is enabled, the exact validation identity also includes the normalized PVC
claim, volume subdirectory, API URI root, and forwarder mount path for both security profiles. This
makes proof stale after a storage-boundary change. Validation returns the forwarder's verified
artifact metadata, then deletes the validation-only hashed Run directory because no durable Run owns
those bytes; cleanup failure is a failed validation. The old legacy identity stays unchanged only
when retention is disabled.

## Project-Defined Goblins

Project config goblins follow the same gate. A project can define a goblin through
`GoblinProject` without importing Goblin King Python worker code, but its image must
still validate against the container contract before scheduler execution.

Admin, CLI, and API surfaces show validation status so adopters can tell whether a
goblin is validated, failed, stale, or unknown. The admin can show stale configured
image references; digest-level staleness is detected during scheduler validation when
the runtime resolves the current image identity.

## Future Runner Boundary

Future remote, federated, or external runners must honor the same rule: a goblin image
is schedulable only when its resolved image identity has passing proof for the declared
contract and validator version. This document describes that boundary only; it does not
add federation or remote-runner behavior.
