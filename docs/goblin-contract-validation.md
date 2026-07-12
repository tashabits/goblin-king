# Goblin Contract Validation

Goblin King does not schedule arbitrary unvalidated container images by default. A
container-backed goblin is runnable only when its current resolved image identity has
passing proof for the declared [Goblin Container Contract](goblin-container-contract.md)
version and validator version.

Use `goblin-king workers validate` to run worker images with temporary contract mounts
and verify that each worker writes a valid result envelope. The short rule is:
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

By default, a valid failed result envelope is still contract-valid. Use
`--require-success` when failed result envelopes should fail validation.

## Proof Lifecycle

Validation proof is tied to goblin kind, resolved image identity or digest, contract
version, validator version, validation timestamp, status, failure reasons, and the
effective runtime policy summary when one is available.

The scheduler checks proof immediately before Docker or Kubernetes execution. If no
current passing proof exists, the scheduler may perform just-in-time validation, persist
the result, and continue only when validation passes. If proof cannot be created, the
job is rejected before container execution and the failed run, event, and audit record
include repair guidance.

Re-run validation whenever you rebuild, retag, or pull an image. A changed image digest
invalidates previous proof for scheduling. A tag such as `my-worker:local` is only a
name; the proof is for the resolved image identity behind that name.

## Failure Mapping

| Condition | Scheduler behavior | Operator fix |
| --- | --- | --- |
| No validation proof exists | Run just-in-time validation before execution. Execute only if it passes. | Run `goblin-king workers validate` with the same kind and input before scheduling routine work. |
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
