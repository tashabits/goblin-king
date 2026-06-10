# Project-Adoptable Goblin King Roadmap

This roadmap extends Goblin King from the current container-contract and
cross-language demo state into a project-adoptable alpha. It is a roadmap and
proof ledger, not a user guide.

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

The supported alpha journey is:

```shell
goblin-king project init ./my-goblin-project --prefix myproject
cd ./my-goblin-project
goblin-king project validate --project goblin-king-project.json
goblin-king workers validate --project goblin-king-project.json --kind myproject.hello --input inputs/hello.json --build --require-success
goblin-king jobs submit myproject.hello --project goblin-king-project.json --input inputs/hello.json --runtime docker
goblin-king scheduler run-once --project goblin-king-project.json --runtime docker
goblin-king runs show <run-id> --with-job
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
started from that enforced policy baseline and is now closed out through Phase 42.

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
- Status: implemented.

Added a small, boring, copy-paste-friendly project template and quickstart that
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

The template demonstrates a minimal goblin, an artifact-producing goblin,
project config, local input files, optional input schema, image build command,
validation command, run command, and result/artifact inspection command.

Proof:

- `goblin-king project init` creates a standalone adopter project.
- Template files and quickstart docs are added.
- Template goblin images build locally through `workers validate --build`.
- Template hello and artifact goblins validate successfully.
- Result and artifact validation commands work.
- Full local CI, Docker/Helm admin audits, and screenshots are required in the PR body.
  Phase 37 screenshots live at `docs/screenshots/phase-37-docker-admin.png` and
  `docs/screenshots/phase-37-helm-admin.png`.

## Phase 38: External Project Scheduling And Run Inspection

- Branch: `phase-38-external-project-scheduling`.
- PR title: `Phase 38 external project scheduling and run inspection`.
- Status: implemented.

Made sure project-defined goblins can be scheduled and inspected cleanly through
CLI, API, and admin surfaces.

Goals:

- Schedule a goblin defined only in project config.
- Pass input JSON from a file and inline input JSON if already supported.
- Record job/run metadata, effective goblin definition, and effective resource
  policy.
- Show run status, result JSON, log references, artifacts, and whether the
  goblin came from built-in examples or project config.

Commands:

```bash
python -m goblin_king.cli jobs submit project.inline.hello \
  --project examples/adopting-project/goblin-king-project.json \
  --input examples/input.json \
  --runtime docker

python -m goblin_king.cli schedules add project.inline.hello \
  --project examples/adopting-project/goblin-king-project.json \
  --input examples/input.json \
  --cron "* * * * *" \
  --due-now

python -m goblin_king.cli scheduler run-once \
  --project examples/adopting-project/goblin-king-project.json

python -m goblin_king.cli runs show <run-id> --with-job
```

Proof:

- A project-defined goblin runs successfully through Docker.
- Job metadata records `goblin_source` and the effective `goblin_definition`.
- `runs show --with-job` displays run data plus source job metadata.
- Project-defined schedules can be created and materialized by the scheduler.
- API-created project goblin jobs preserve source metadata.
- Docker admin audit passed for every registered demo goblin. Representative
  proof: `example.hello` completed as job
  `ece9d635-5827-4b01-a834-b11d826ec99a` / run
  `c05c538d-97a0-440b-b036-f59211f291af`, `example.artifact`
  completed as job `730b3845-e4d1-459b-ad1e-46bfd563dcc5` / run
  `b51ab9a6-a0ea-4db1-88eb-3739ac5d1a0c`, controlled failures failed
  readably as expected, and `example.long-hello` returned changing timestamps
  for service `ca0b12ea-061d-48aa-99d2-b84f8756752f`.
- Helm admin audit passed for every registered demo goblin. Representative
  proof: `example.hello` completed as job
  `c45fc9a6-4828-4c7b-a427-2ffa2a9d4aa5` / run
  `8cc170cb-311e-4821-b738-4a9830518d59`, `example.artifact`
  completed as job `52db4185-a0fe-40bd-ba6e-34a4339b290b` / run
  `e606cba3-f35d-4505-8cf7-01e40d302fff`, controlled failures failed
  readably as expected, and `example.long-hello` returned changing timestamps
  for service `46e94aac-e1e4-4d5a-af92-3c67de18520c`.
- Phase 38 screenshots live at `docs/screenshots/phase-38-docker-admin.png`
  and `docs/screenshots/phase-38-helm-admin.png`.

## Phase 39: Stable v1alpha1 Contract And Public API Boundaries

- Branch: `phase-39-v1alpha1-contract-public-boundaries`.
- PR title: `Phase 39 v1alpha1 contract and public boundaries`.
- Status: implemented.

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

- Public root exports include version constants for the container contract, project
  config, registry schema, worker image map, result envelope, heartbeat envelope, and
  API settings schema.
- Docker and Kubernetes runtimes pass `GOBLIN_CONTRACT_VERSION=goblin-king/v1alpha1`
  to worker containers.
- Contract, project config, public API, compatibility, and README docs now state
  `goblin-king/v1alpha1` explicitly.
- The compatibility matrix uses named `v1alpha1` versions instead of bare `1` labels.
- Example project config and generated project templates use versioned config.
- Invalid/unsupported project config versions fail clearly.
- Docker admin audit passed for every registered demo goblin. Representative
  proof: `example.hello` completed as job
  `dfc025ba-e749-4d8d-a233-cb49219560c4` / run
  `b3f09992-630b-4e3c-a9e7-efe250c43b84`, `example.artifact`
  completed as job `312ce65f-3630-4bed-b31d-3f7f7c466479` / run
  `2fb671eb-88d4-47b8-a1aa-c46b12b7115b`, controlled failures failed
  readably as expected, and `example.long-hello` returned changing timestamps
  for service `c03ab7c3-7509-4c0a-b3fc-dcdb1e717768`.
- Helm admin audit passed for every registered demo goblin. Representative
  proof: `example.hello` completed as job
  `2fab2cf7-39a3-4eba-af42-7f079a9f5c1c` / run
  `38279770-5ad4-4a87-8206-03d992d05387`, `example.artifact`
  completed as job `af7b3a69-366e-4040-9ebc-86fe9dded031` / run
  `1d689edb-0bf4-47e8-a82b-f1b183727815`, controlled failures failed
  readably as expected, and `example.long-hello` returned changing timestamps
  for service `69181ce4-8f6a-4d54-ab4d-b66d9f00c93e`.
- Phase 39 screenshots live at `docs/screenshots/phase-39-docker-admin.png`
  and `docs/screenshots/phase-39-helm-admin.png`.

## Phase 40: Adopter Documentation Pass

- Branch: `phase-40-adopter-documentation-pass`.
- PR title: `Phase 40 adopter documentation pass`.
- Status: implemented.

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

- README now includes `Using Goblin King In Your Project` with the shortest template,
  validation, submit, schedule, and inspection path.
- `docs/adopter-guide.md` provides a complete adopter path with copy-paste commands,
  expected outputs, Docker, Helm, admin, result, artifact, failure, and public-boundary
  guidance.
- `docs/ADOPTING_PROJECTS.md` now leads with project config and container goblins;
  Python package metadata is documented as optional.
- Docs consistently say goblins are contract-compliant containers.
- Documentation tests verify README heading anchors and the adopter guide link.
- Docker admin audit passed for every registered demo goblin. Representative
  proof: `example.hello` completed as job
  `cb69982b-9b73-4fa1-87f6-86a183686eb5` / run
  `cd2d9fef-9c95-4f92-b40c-b296723eaf8e`, `example.artifact`
  completed as job `aa0b107c-b046-437e-9f70-3e99499f4b4a` / run
  `2ce3cf0e-467d-4d1d-aee2-b1aaa5720ecc`, controlled failures failed
  readably as expected, and `example.long-hello` returned changing timestamps
  for service `6a489ccd-546a-4ed7-a04f-ed148ff1386e`.
- Helm admin audit passed for every registered demo goblin. Representative
  proof: `example.hello` completed as job
  `98a139b4-a694-45ba-bbf6-1555ef567c65` / run
  `41e64861-6e60-40bd-ab48-46b87bff7d0d`, `example.artifact`
  completed as job `0b6c78d7-40c6-4980-a8a3-deec6ceb766d` / run
  `1e2c34e9-9ec4-4b9d-aa3a-0e75002bfae0`, controlled failures failed
  readably as expected, and `example.long-hello` returned changing timestamps
  for service `f39bab2a-5bac-47df-86e1-0e64f425a4cd`.
- Phase 40 screenshots live at `docs/screenshots/phase-40-docker-admin.png`
  and `docs/screenshots/phase-40-helm-admin.png`.

## Phase 41: Adopter Smoke Suite

- Branch: `phase-41-adopter-smoke-suite`.
- PR title: `Phase 41 adopter smoke suite`.
- Status: implemented.

Adds a local smoke suite that proves the adopter path works end to end. It
generates a temporary consuming project, defines project goblins, validates
their container contracts, schedules them through Goblin King, inspects success,
artifact, and controlled-failure results, and cleans the generated project up.

Commands:

```shell
goblin-king smoke adopter-project
make adopter-smoke
```

Implemented proof:

- `goblin-king smoke adopter-project --prefix phase41` passed and removed its
  temporary generated project. It validated `phase41.hello`,
  `phase41.artifact`, and `phase41.failure`, scheduled them, recorded successful
  runs for hello and artifact goblins, recorded an expected failed run for the
  controlled failure goblin, and proved artifact output.
- `make adopter-smoke` passed through the same flow for `smoke.hello`,
  `smoke.artifact`, and `smoke.failure`.
- Clean-board Docker admin audit removed historical rows before the proof run:
  19 artifacts, 10 handoffs, 229 runs, 232 jobs, 2 fanouts, 1462 events,
  70 worker heartbeats, and 16 long services.
- Docker admin audit passed for every registered goblin type. Representative
  proof: `example.hello` completed as job
  `fd4b1513-fa7b-442e-bfd7-3b35b37faba8` / run
  `549295c3-01b6-47a3-aefd-c233b7740b84`, `example.artifact`
  completed as job `b57b37e4-ab19-4364-84bc-19555a7aa838` / run
  `bb83e02d-7839-4f05-a691-264071776aee`,
  `example.behavior-shell-failure` and `example.controlled-failure` failed
  readably as expected, and `example.long-hello` probed successfully as service
  `8eff7f7c-fe02-4874-ae16-7480a05c0867`.
- Docker WASI proof: `example.wasi-c-hello` completed as job
  `3b0b34e3-1ffa-4569-8086-cef912bce9e4` / run
  `4d09a58c-39e1-493b-8faa-ba5539e5956a`, and
  `example.wasi-rust-hello` completed as job
  `4150d443-ce36-405b-8461-88d9dbafae57` / run
  `ee1d32ca-6f67-41c6-830b-b1b1f870fbf7`.
- Clean-board Helm admin audit removed historical rows before the proof run:
  26 artifacts, 11 handoffs, 288 runs, 288 jobs, 1788 events,
  74 worker heartbeats, and 12 long services.
- Helm admin audit passed for every registered goblin type. Representative
  proof: `example.hello` completed as job
  `0dd2157f-03c6-4abb-9c02-d108ef202924` / run
  `88527bce-97fc-4cbe-8713-4f134f165208`, `example.artifact`
  completed as job `19af4271-9977-4c1f-a226-445d6a304448` / run
  `0b3ceaec-4764-4769-a49c-c73badc340f6`,
  `example.behavior-shell-failure` and `example.controlled-failure` failed
  readably as expected, and `example.long-hello` probed successfully as service
  `85405642-c2e7-4c87-89d1-ac9142f94e53`.
- Helm WASI proof: `example.wasi-c-hello` completed as job
  `49226ae1-c7c7-44b1-9f2d-78f27d75f91c` / run
  `ca90b9ae-7278-4e81-a233-8d9ccb0bba99`, and
  `example.wasi-rust-hello` completed as job
  `33d495ba-26e6-40c7-abc2-6003328d4fb1` / run
  `5e087610-47d2-42fa-b2dd-9510996b1109`.
- Browser admin-console proof launched WASI goblins through the Goblin Lab UI:
  `example.wasi-c-hello` produced `Hello World from C WASI`, and
  `example.wasi-rust-hello` produced `Hello World from Rust WASI`.
- Phase 41 screenshots live at
  `docs/screenshots/phase-41-docker-admin.png`,
  `docs/screenshots/phase-41-helm-admin.png`, and
  `docs/screenshots/phase-41-admin-wasi-ui.png`.
- Full local CI passed. Local proof remains the required quality gate; GitHub
  Actions are not required and are not sufficient.

## Phase 42: Project-Adoptable Alpha Closeout

- Branch: `phase-42-project-adoptable-alpha-closeout`.
- PR title: `Phase 42 project-adoptable alpha closeout`.
- Status: implemented.

Performed a closeout pass to ensure Goblin King is ready for early adopter
projects.

Completed goals:

- Re-audited the adopter path from a clean checkout.
- Re-audited docs for Python-specific assumptions.
- Re-audited examples for contract compliance.
- Re-audited project config docs and validation errors.
- Ensured quickstart and smoke commands still work.
- Ensured public/semi-public/internal boundaries are clear.
- Updated the README with an explicit readiness status statement.

Status language added to the README:

```text
Status: Project-adoptable alpha.

Goblin King is suitable for local development, internal prototypes, and trusted
early-adopter projects that want to define and schedule their own
contract-compliant container goblins.

It is not yet recommended for untrusted multi-tenant workloads or production
environments without additional hardening.
```

Proof:

- Clean checkout proof passed from a temporary clone of `main`:
  `clean42.hello` completed as job
  `bc8485f6-29a0-40c5-ab0a-6f049b22d48a` / run
  `ef47b345-0d26-4113-bf4a-595cb2379185`, `clean42.artifact`
  completed as job `b7b025d3-18c6-4d48-aa3e-04e6ee391b25` / run
  `138f271c-7f66-4b11-ae53-a2c8468a8299` with one artifact, and
  `clean42.failure` failed readably as job
  `7661b030-332b-4712-b25e-d1b2c708bee4` / run
  `5d4de845-9eed-4e71-b0f4-147197f65690`.
- Branch-local adopter smoke proof passed:
  `phase42.hello` completed as job
  `109f4736-ad2d-4091-a04c-8e25ae3023be` / run
  `d17d9684-ea16-42a1-bac2-29532d79679f`, `phase42.artifact`
  completed as job `5e54b59c-786c-4bf4-b1a8-73d632217f61` / run
  `41fd09fc-a3db-46e6-a2af-3c93efbe92ea` with one artifact, and
  `phase42.failure` failed readably as job
  `af8941c9-adf4-4470-bcb0-c034144c2d7e` / run
  `fa849641-2817-4e4b-8520-0d5a983cad0c`.
- Clean-board Docker admin audit removed 2 artifacts, 1 handoff, 30 runs,
  30 jobs, 185 events, 8 worker heartbeats, and 1 long service before proof.
- Docker admin audit passed for every registered goblin type. WASI proof:
  `example.behavior-wasi-c-context` completed as run
  `3764862a-8112-4e7a-a7e2-94bb2953b389`,
  `example.wasi-c-hello` completed as run
  `986890f2-cc93-46de-92fd-fee4e4828790`, and
  `example.wasi-rust-hello` completed as run
  `86d3f4ee-b825-48e0-abe8-12ef429995a7`.
- Clean-board Helm admin audit removed 2 artifacts, 1 handoff, 23 runs,
  23 jobs, 143 events, 6 worker heartbeats, and 1 long service before proof.
- Helm admin audit passed for every registered goblin type. WASI proof:
  `example.behavior-wasi-c-context` completed as run
  `fa779788-288f-4864-abfe-c8579f5a59ed`,
  `example.wasi-c-hello` completed as run
  `4a628656-32e6-48e5-a8fe-bd3705d23a30`, and
  `example.wasi-rust-hello` completed as run
  `9294a7d1-2324-47c0-86e6-00b9c787ffd4`.
- Final screenshots live at `docs/screenshots/phase-42-docker-admin.png` and
  `docs/screenshots/phase-42-helm-admin.png`.
- Full local CI passed. GitHub Actions remain neither required nor sufficient.

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
