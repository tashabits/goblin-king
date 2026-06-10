# Goblin Scaffolding Roadmap

This roadmap describes future CLI scaffolding and shared starter templates for
Goblin Designer. It is a planning document, not an implemented feature.

Core rule:

```text
Scaffolding creates a starter. Validation decides whether it can run.
```

## Goals

- Plan starter folders for contract-compliant Docker goblins.
- Share one future template engine between CLI scaffolding and Goblin Designer.
- Keep generated goblins representable in `GoblinProject`.
- Make validation the handoff from generated files to schedulable work.
- Support multiple container-packaged runtimes without making Python required.

## Additional Guardrails

- Generated goblins must still validate before scheduling.
- No silent project config writes; generated changes must be previewed and
  explicitly accepted.
- No browser-based IDE in the first scaffolding pass.
- No freeform browser-based AI code generation in the first pass.
- Goblins remain contract-compliant OCI/Docker containers.
- Project config remains the primary adopter interface.

## Scaffold Phase 1: Template Manifest Format

Plan a future template manifest that describes the files a starter creates, the
runtime it targets, required inputs, result-envelope behavior, artifact behavior,
resource-policy suggestions, and validation expectations.

The manifest should be small enough to review and should not become a second
project configuration format.

## Scaffold Phase 2: CLI Goblin Generator

Plan a future CLI generator that can create a starter worker folder, Dockerfile,
sample input, result-envelope code, optional artifact output, and matching
project config snippet.

Starter languages may include Python, Node.js, Go, Rust, Java, .NET, Ruby, PHP,
shell, and container-wrapped WASI.

## Scaffold Phase 3: Shared Template Registry

Plan a future local template registry shared by CLI scaffolding and Goblin
Designer. Both surfaces should render the same starter files from the same
template definitions.

The registry should distinguish built-in templates from project-local templates
without requiring new runtime behavior.

## Scaffold Phase 4: Project Config Preview And Explicit Write

Plan a preview step that shows generated `GoblinProject` changes before writing
them. The user must explicitly accept config changes, worker folder creation,
and any schedule metadata.

No scaffolded goblin should be silently promoted into the runnable admin list.

## Scaffold Phase 5: Validation Handoff

Plan a validation handoff that runs or displays the required validation command
after scaffolding. The result should include validation status, image identity,
contract version, validator version, timestamp, and repair guidance.

Only passing validation should make the scaffolded goblin schedulable.

## Scaffold Phase 6: Closeout And Docs

Close out scaffolding work by updating README, Goblin Designer roadmap, adopter
guide, writing-goblins docs, screenshots, and proof table.

Closeout proof should show CLI and Designer scaffolding using the same template
engine and producing equivalent project config snippets.

## Non-Goals

- No browser-based IDE.
- No freeform AI code generation in the first pass.
- No validation bypass.
- No new runtime path for scaffolded goblins.
- No untrusted third-party container execution.
- No federation, remote runners, geographic placement, or distributed
  experiment orchestration.

## Acceptance Criteria

- Future scaffolding creates contract-compliant Docker goblin starter folders.
- CLI scaffolding and Goblin Designer share a planned template engine.
- Generated config is previewed before any write.
- Generated goblins must validate before scheduling.
- The roadmap keeps Python optional and the container contract primary.
