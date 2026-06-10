# Goblin Designer Roadmap

This roadmap describes a future React admin page called **Goblin Designer**.
It is a planning document, not an implemented feature.

The Designer should help a trusted project author create a new contract-compliant
goblin container, validate it, and promote it into the existing admin goblin
list without rebuilding the React admin. It should feel like the current admin
lab bench: same shell, auth model, visual language, tables, badges, buttons,
status messages, and proof-oriented workflow, but with a separate page focused
on design instead of operation.

Core rule:

```text
Design first. Validate next. Schedule only after validation passes.
```

## Goals

- Provide a guided designer for project goblins, not a browser-based IDE.
- Keep the Goblin Container Contract as the worker interface.
- Make validation an explicit required step for newly designed goblins.
- Show validation proof before a goblin can be promoted as runnable.
- Make validated goblins appear in the existing admin registered goblin table
  and submit dropdowns through dynamic API discovery.
- Preserve the CLI and project-config workflow as the durable source of truth.

## Additional Guardrails

- Use **Goblin Designer** as the product name for the future admin page.
- The Designer is a guided builder, not a full IDE.
- The first Designer pass uses structured templates and starter worker folders,
  not freeform browser-based AI code generation.
- The Designer must preview generated changes and require explicit user action
  before writing project config, worker folders, schedules, validation state, or
  promoted goblin definitions.
- Anything the Designer creates must be representable in the existing
  `GoblinProject` config model and usable from the existing CLI.
- Promotion means a validated goblin design becomes discoverable through the
  existing project goblin/admin surfaces and appears in the existing goblin
  list/dropdowns.
- Promotion must not bypass validation or create a separate runtime path.
- The existing CLI/project config workflow remains canonical and must not be
  replaced by the Designer.

## Designer Phase 1: Page And Navigation

Add a separate React admin page for **Goblin Designer**.

The page should:

- Reuse the existing admin shell, login behavior, token storage, API client,
  spacing, cards, tables, badges, event/error patterns, and button style.
- Stay visually distinct from the existing tester/operator page by centering
  design, validation, preview, and promotion workflows.
- Add navigation that makes it clear when the user is designing a goblin versus
  running or inspecting existing goblins.
- Load current project discovery, registry, validation status, image map, and
  resource-policy context from the API.

Proof for this phase should include screenshots of the Designer page beside the
existing admin lab page to show that the look and feel match without merging the
two workflows.

## Designer Phase 2: Project Config Builder

Add a guided builder for `GoblinProject` entries.

The builder should let a user define:

- Goblin kind and description.
- Container image, context, and Dockerfile.
- Optional input schema path.
- Artifact expectations.
- Labels and tags.
- Environment variables and safe secret references.
- Optional schedule metadata.
- Resource defaults and per-goblin overrides.

The Designer should preview the generated `GoblinProject` JSON before saving or
exporting, and it must require explicit user action before writing changes. It
should not create a second project configuration format.

## Designer Phase 3: Contract Checklist And Validation Gate

Every newly designed goblin must have an explicit validation step before it can
be scheduled or promoted as runnable.

The validation UI should show:

- Validation state: `unknown`, `validated`, `failed`, or `stale`.
- Goblin kind.
- Image reference and resolved digest when available.
- Contract version.
- Validator version.
- Validation timestamp.
- Failure reasons.
- Effective runtime policy summary.
- The exact CLI repair or revalidation command.

Missing, failed, or stale validation must block promotion into runnable admin
flows. A just-designed goblin may be saved as draft configuration, but it must
not be presented as schedulable until validation proof passes for the current
image identity, contract version, and validator version.

## Designer Phase 4: Promote Validated Goblins Into Admin List

Once a designed goblin passes validation, it must appear in the existing admin
registered goblin table and submit dropdowns without rebuilding the React admin.

Promotion should use the existing discovery and validation surfaces:

- Refresh or reload discovery after the project config/image map changes.
- Keep failed reloads from replacing the last known valid registry state.
- Show the promoted goblin with the same validation badge and source marker as
  other project-config goblins.
- Make the promoted goblin available to the tester/operator page for job
  submission, schedules, fanout, run inspection, artifacts, events, and
  heartbeats.

The success path for this phase is: design a goblin, validate it, reload
discovery, see it in the admin list, then launch it from the existing admin
tester page.

## Designer Phase 5: Sample Worker Generator

Add helper flows that generate or copy starter worker folders for common
container-packaged runtimes.

Initial templates should cover the language families already represented by the
examples, such as Python, Node, shell, Go, Java, .NET, PHP, and WASI wrapper
patterns. The generator should create contract-compliant worker folders with
Dockerfiles, minimal input handling, result-envelope output, and optional
artifact output.

The generator must not imply that Python plugins are the primary worker model.
The worker interface remains the container contract.

## Designer Phase 6: Test Fixture And Result Preview

Add a fixture builder for sample inputs and expected result shapes.

The Designer should help the user:

- Create sample input JSON.
- Preview the expected result envelope.
- Describe artifact metadata and output paths.
- Preview effective resource policy.
- Record expected failure modes.
- Save example inputs that can be reused by validation and the tester page.

This phase should make it easy to prove the goblin before it becomes part of a
larger project schedule.

## Designer Phase 7: Designer-To-Tester Handoff

Connect the Designer to the existing admin tester/operator page.

After validation passes and discovery reloads, the UI should offer a handoff to:

- Submit an on-demand job.
- Create a schedule.
- Inspect queued/running/completed jobs.
- Inspect run results and artifact metadata.
- Watch events and heartbeats.
- Run the existing Docker and Helm admin audit flows where relevant.

The handoff should carry the selected goblin kind and sample input, but the
tester page remains the place where jobs are actually submitted and inspected.

## Designer Phase 8: Closeout And Adoption Docs

Close out the Designer work with docs, screenshots, and proof updates.

Update:

- README Future Work or feature status.
- Admin guide screenshots.
- Adopter guide.
- Project config docs.
- Goblin contract validation docs.
- Proof table.
- Any roadmap file that references future admin design work.

Closeout proof should include Docker and Helm admin screenshots showing a new
designed goblin moving from draft, to validated, to visible in the existing
admin goblin list, to runnable in the tester page.

## Non-Goals

- No browser-based code editor in the first Designer pass.
- No freeform browser-based AI code generation in the first Designer pass.
- No weakening of the mandatory validation gate.
- No untrusted third-party container execution.
- No Docker socket access for goblin task containers.
- No replacement of the existing CLI/project config workflow.
- No new project configuration format.
- No React rebuild requirement for newly promoted goblin types.
- No silent modification of project config, worker folders, schedules,
  validation state, or promoted goblin definitions.

## Acceptance Criteria

- The Designer is a separate admin page with the same look and feel as the
  existing React admin.
- A newly designed goblin cannot be scheduled until validation passes.
- A validated designed goblin appears in the existing admin registered goblin
  table and submit dropdowns.
- The admin reads goblin/config/validation state dynamically from the API.
- The CLI/project config path remains documented and usable without the Designer.
- Any generated or promoted design is representable in `GoblinProject` and
  usable from the existing CLI.
