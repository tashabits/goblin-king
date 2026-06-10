# Project-Adoptable Goblin King Roadmap

This roadmap extends Goblin King from the current container-contract and
cross-language demo state into a project-adoptable alpha. It is future-work
planning, not a user guide.

All proof remains local. GitHub Actions are not required and are not sufficient
for these phases; every PR must include exact local command output.

## Goal

An adopting project should be able to install Goblin King, define its own
contract-compliant goblin container images through project configuration,
validate those containers, schedule them, inspect results/artifacts/logs, and
rely on a clearly documented alpha contract.

Core rule:

A goblin is always a contract-compliant OCI/Docker container. Goblin King
schedules containers, not language runtimes. The language, framework, binary,
script, or WASI module inside the container is an implementation detail.

## Target Adoption Flow

The exact commands may adjust to match the existing CLI style, but the intended
journey is:

```bash
goblin-king project init
goblin-king goblin add invoice-renderer --image my-project/invoice-renderer:local
goblin-king goblin validate invoice-renderer
goblin-king run invoice-renderer --input examples/invoice.json
goblin-king runs show <run-id>
goblin-king artifacts list <run-id>
```

The path should let a user:

1. Initialize Goblin King support in an external project.
2. Define goblins without editing Goblin King source code.
3. Build or reference goblin container images.
4. Validate goblins against the container contract.
5. Schedule goblins.
6. Inspect run status, results, logs, and artifacts.
7. Understand what is stable, alpha, and internal.

Phase 34 completed runtime resource-policy enforcement. The project-adoptable alpha work
starts from that enforced policy baseline.

## Phase 35: Project-Adoptable Goblin Configuration

- Branch: `phase-35-project-goblin-config`.
- PR title: `Phase 35 project-adoptable goblin configuration`.
- Status: implemented.

Added a project-level configuration format that lets consuming
projects define their own goblins outside Goblin King internals. Use the
project's existing config format if one already exists; do not create a second
competing format.

The config should support fields like:

```yaml
apiVersion: goblin-king/v1alpha1
kind: GoblinProject
goblins:
  invoice-renderer:
    image: my-project/invoice-renderer:local
    description: Renders invoices as PDFs
    inputSchema: schemas/invoice-renderer.input.schema.json
    resources:
      timeoutSeconds: 120
      memory:
        limit: 512Mi
    artifacts:
      enabled: true
```

Implemented capabilities:

- Define goblin kind/name.
- Define container image.
- Define optional description.
- Define optional input schema path.
- Define optional resource policy.
- Define optional artifact behavior.
- Define optional labels/tags.
- Define optional environment variables with safe secret handling rules.
- Define optional schedule metadata where appropriate.
- Resolve local relative paths from the adopting project root.
- Validate config with clear error messages.
- Keep Goblin King source/internals untouched when adding project goblins.
- Merge inline project goblins with existing JSON registries and entry points.
- Merge inline worker image definitions with existing image maps.
- Mark inline goblins as `project-config` in API/admin discovery.

Proof:

- `examples/adopting-project/goblin-king-project.json` includes
  `project.inline.hello`.
- Tests cover valid/invalid project config, secret reference safety, API source
  reporting, registry discovery, and worker image map merging.
- Project-defined goblins use the container-only placeholder module and require Docker
  or Kubernetes runtime, not Python worker imports.
- Full local CI, project validation/list smoke proof, Docker admin audit, Helm admin
  audit, and screenshots are required in the PR body. Phase 35 screenshots live at
  `docs/screenshots/phase-35-docker-admin.png` and
  `docs/screenshots/phase-35-helm-admin.png`.

## Phase 36: Bring-Your-Own-Goblin Validation

- Branch: `phase-36-bring-your-own-goblin-validation`.
- PR title: `Phase 36 bring-your-own-goblin validation`.
- Status: implemented.

Added a validation workflow that lets an adopting project verify a goblin
container image before relying on it. The validator answers: "Does this image
behave like a Goblin King goblin?"

Commands:

```bash
python -m goblin_king.cli workers validate \
  --project examples/adopting-project/goblin-king-project.json \
  --input examples/input.json \
  --kind project.inline.hello \
  --build \
  --require-success

python -m goblin_king.cli workers validate-image \
  --image my-project/invoice-renderer:local \
  --kind my.project.invoice-renderer \
  --input examples/input.json \
  --require-success
```

Validation checks project config validity, image availability/buildability,
container startup, required contract env vars, readable input/context files,
valid result JSON, result status, writable artifact directory, stdout/stderr
failure detail, clear exit-code reporting, timeout reporting, and actionable errors for
unsupported or missing behavior.

Proof:

- `workers validate --project` loads project settings and discovered worker mappings.
- `workers validate-image` validates one prebuilt image without requiring a registry file.
- Tests cover missing prebuilt images, missing worker result JSON, invalid result JSON,
  and project-settings validation dispatch.
- Docker proof, Helm proof, full local CI, and screenshots are required in the PR body.
  Phase 36 screenshots live at `docs/screenshots/phase-36-docker-admin.png` and
  `docs/screenshots/phase-36-helm-admin.png`.

## Phase 37: Project Template And Golden Path Quickstart

- Branch: `phase-37-project-template-quickstart`.
- PR title: `Phase 37 project template and quickstart`.
- Status: planned.

Add a small, boring, copy-paste-friendly project template and quickstart that
show how a real adopting project should structure goblins.

Suggested template:

```text
goblin-project/
  goblin-king.project.yaml
  goblins/
    hello/
      Dockerfile
      main.py
      README.md
    report-writer/
      Dockerfile
      main.js
      README.md
  inputs/
    hello.json
    report.json
  schemas/
    hello.input.schema.json
    report.input.schema.json
  README.md
```

The template should demonstrate a minimal goblin, an artifact-producing goblin,
project config, local input files, optional input schema, image build command,
validation command, run command, and result/artifact inspection command.

Proof:

- Template files and quickstart docs are added.
- Template goblin image builds locally.
- Template goblin validates and runs successfully.
- Result and artifact inspection commands work.
- Full local CI passes.

## Phase 38: External Project Scheduling And Run Inspection

- Branch: `phase-38-external-project-scheduling`.
- PR title: `Phase 38 external project scheduling and run inspection`.
- Status: planned.

Make sure project-defined goblins can be scheduled and inspected cleanly through
CLI, API, and admin surfaces.

Goals:

- Schedule a goblin defined only in project config.
- Pass input JSON from a file and inline input JSON if already supported.
- Record job/run metadata, effective goblin definition, and effective resource
  policy.
- Show run status, result JSON, log references, artifacts, and whether the
  goblin came from built-in examples or project config.

Suggested commands, adjusted to existing style:

```bash
goblin-king run invoice-renderer --input examples/invoice.json
goblin-king runs list
goblin-king runs show <run-id>
goblin-king runs result <run-id>
goblin-king artifacts list <run-id>
goblin-king artifacts download <run-id> invoice.pdf
```

Proof:

- A project-defined goblin runs successfully through Docker.
- Run details show kind and project source.
- Result JSON and artifacts are inspectable.
- Failed goblin runs are understandable.
- API and admin surfaces work where already supported.
- Full local CI passes.

## Phase 39: Stable v1alpha1 Contract And Public API Boundaries

- Branch: `phase-39-v1alpha1-contract-public-boundaries`.
- PR title: `Phase 39 v1alpha1 contract and public boundaries`.
- Status: planned.

Mark the Goblin Container Contract and project config format with explicit
stability labels:

```text
Goblin Container Contract: v1alpha1
Project Goblin Config: v1alpha1
```

Define stable-enough fields, alpha fields, internal/private details,
deprecated fields if any, compatibility expectations, migration expectations,
and version declaration/negotiation where appropriate.

Docs must clearly state that Python imports are not the stable worker interface;
the container contract is the worker interface.

Proof:

- Contract and project config docs are versioned.
- Example project uses versioned config.
- Invalid/unsupported config versions fail clearly.
- Public/semi-public/internal module docs are updated.
- Full local CI passes.

## Phase 40: Adopter Documentation Pass

- Branch: `phase-40-adopter-documentation-pass`.
- PR title: `Phase 40 adopter documentation pass`.
- Status: planned.

Write and reorganize documentation for people who want to use Goblin King inside
their own project.

The docs should answer:

- What is Goblin King?
- What is a goblin?
- Why must goblins be containers?
- How do I define my own goblin?
- How do I write a Dockerfile for a goblin?
- How do I validate a goblin?
- How do I schedule a goblin?
- How do I inspect results?
- How do I handle artifacts and failures?
- How do I apply resource limits?
- What is stable enough to build against?
- What should I not depend on?

Suggested docs, updating equivalent existing files instead of duplicating:

```text
docs/adopting-goblin-king.md
docs/define-your-own-goblins.md
docs/goblin-container-contract.md
docs/project-goblin-config.md
docs/goblin-dockerfiles.md
docs/goblin-validation.md
docs/running-goblins.md
docs/goblin-results-and-artifacts.md
docs/public-api-boundaries.md
```

Add a README section named `Using Goblin King In Your Project` that links to
the adoption guide and shows the shortest possible working path.

Proof:

- Docs provide a complete adopter path with copy-paste commands and expected
  outputs.
- Docs avoid implying goblins are Python plugins.
- Docs consistently say goblins are contract-compliant containers.
- Docs links are valid.
- Full local CI passes.

## Phase 41: Adopter Smoke Suite

- Branch: `phase-41-adopter-smoke-suite`.
- PR title: `Phase 41 adopter smoke suite`.
- Status: planned.

Add a local smoke suite that proves the adopter path works end to end. It should
simulate a consuming project defining its own goblins and using Goblin King to
validate, schedule, and inspect them.

The smoke suite should cover project config loading, image build/reference,
goblin validation, scheduling a project-defined goblin, successful result,
artifact output, failure output, run inspection, and basic cleanup.

Possible command:

```bash
goblin-king smoke adopter-project
```

Docker Compose or local Docker remains the default proof path. Kubernetes proof
can be optional.

Proof:

- Smoke suite and docs are added.
- Smoke suite runs successfully locally.
- Docker proof is included.
- Non-Docker portions may be suitable for future CI, but local proof remains the
  required quality gate.
- Full local CI passes.

## Phase 42: Project-Adoptable Alpha Closeout

- Branch: `phase-42-project-adoptable-alpha-closeout`.
- PR title: `Phase 42 project-adoptable alpha closeout`.
- Status: planned.

Perform a closeout pass to ensure Goblin King is ready for early adopter
projects.

Goals:

- Re-audit the adopter path from a clean checkout.
- Re-audit docs for Python-specific assumptions.
- Re-audit examples for contract compliance.
- Re-audit project config docs and validation errors.
- Ensure quickstart commands still work.
- Ensure public/semi-public/internal boundaries are clear.
- Ensure the README accurately describes readiness.
- Add a clear status statement.

Suggested status language:

```text
Status: Project-adoptable alpha.

Goblin King is suitable for local development, internal prototypes, and trusted
early-adopter projects that want to define and schedule their own
contract-compliant container goblins.

It is not yet recommended for untrusted multi-tenant workloads or production
environments without additional hardening.
```

Proof:

- Full local CI passes.
- Adopter smoke suite proof is included.
- Docker proof for project-defined goblin validation and execution is included.
- Final docs index links are valid.
- README status is updated.
- Before/after summary confirms a consuming project can define and schedule its
  own goblins without modifying Goblin King internals.

## Required Proof For Every PR

Each PR body must include:

- Objective checklist with concrete evidence.
- Exact local CI output:
  - `python -m pytest`
  - `python -m ruff check .`
  - `cd admin-ui && npm test -- --run` when admin/frontend changes are included.
  - `cd admin-ui && npm run build` when admin/frontend changes are included.
- Docker proof when validation, runtime behavior, examples, or adopter smoke
  paths are changed.
- Helm render proof when Helm/Kubernetes behavior is changed.
- Live Kubernetes smoke when local Kubernetes is available.
- Screenshots or command output for admin UI behavior when UI changes are
  included.
- Deferred items and known limitations.

No GitHub Actions proof is required or sufficient.

## Global Constraints

- All goblins must be OCI/Docker container images.
- All goblins must follow the Goblin Container Contract.
- Goblin King schedules containers, not language runtimes.
- The language/runtime inside the container is an implementation detail.
- Do not make Python a required worker runtime.
- Python helpers or SDKs may exist only as optional conveniences.
- Consuming projects must be able to define goblins without editing Goblin King
  source code.
- Project configuration should become the primary adoption path for user-defined
  goblins.
- Do not create a second competing config format if one already exists.
- Do not introduce cloud-specific assumptions.
- Docker Compose/local Docker remains the default adopter proof path.
- Helm remains optional and cloud-neutral.
- Short-lived self-terminating task containers remain the primary worker model.
- Validation should test the container contract, not language implementation
  details.
- Security docs must be honest: project-adoptable alpha does not mean safe for
  untrusted multi-tenant workloads.
- Backwards compatibility should be preserved by safe defaults or migration
  guidance.

## Deferred After Phase 42

- Public PyPI release hardening.
- Full production hardening for untrusted multi-tenant execution.
- Secret allow-lists, provider-specific admission controls, object-storage quota
  enforcement, and deeper resource-policy engines.
- Deep conformance certification.
- Image signing/scanning.
- Cloud-provider-specific deployment recipes.
- Object storage backends.
- Native WASI scheduling, if ever desired later.
- Untrusted third-party goblin execution.
