# Project Template Quickstart

Use `goblin-king project init` to create a standalone adopter project that defines
container goblins without editing Goblin King source and without requiring Python worker
imports.

This template focuses on task goblins. Project config can also define service goblins;
both use the same validation gate and container-backed model.

Think of the generated project as the portable worker backbone. It describes workers,
images, resource posture, validation, and deployment wiring without assuming the
business domain. A RAG profile should be the first profile layered onto this backbone:
indexing, retrieval, embedding, evaluation, or chat-service workers are still ordinary
project goblins with their own images, schemas, resources, and proof.

Notebook-declared Python functions, notebook-declared ASGI services, the Goblin
Directory UI, and the JupyterLab Directory picker are compatible paths when a
JupyterHub-backed deployment wants notebook authoring or deployment-local sharing.
They do not compete with this template. All paths still resolve to container-backed
goblins behind the same validation and resource-policy gates.

If your host project vendors Goblin King under a path such as `vendor/goblin-king`, see
[Using Goblin King From A Vendored Checkout](using-goblin-king-from-a-vendored-checkout.md)
before wiring the generated project into the local admin stack.

## Backbone First, Profiles Second

Use the generic proof first:

```bash
make worker-backbone-proof
```

That checks the bundled adopter project config, lists discovered goblins, and renders
the Helm chart with host-project values. It proves the reusable skeleton before any
domain-specific profile is considered.

Use the RAG profile proof as a profile-shape check:

```bash
make rag-profile-proof
```

By default this target points at `examples/worker-backbone`, including its bundled
local RAG first-use-case worker and resource-policy fixture. When a private RAG profile
exists, point the target at that profile:

```bash
make rag-profile-proof \
  RAG_PROFILE_PROJECT=/path/to/rag-profile/goblin-king-project.json \
  RAG_PROFILE_HELM_VALUES=/path/to/rag-profile/helm-values.yaml \
  RAG_PROFILE_KIND=rag.index
```

The expectation is deliberately modest: a RAG profile should fit the same project
config, image-map, resource-policy, and Helm render path as any other adopter project.
Runtime proof can then add profile-specific worker validation or end-to-end checks.

## Create The Project

```bash
goblin-king project init ./my-goblin-project --prefix myproject
cd ./my-goblin-project
```

The generated project includes:

- `goblin-king-project.json`: versioned `GoblinProject` config.
- `goblin-images.json`: empty base image map; inline goblins carry their own worker
  image settings.
- `workers/myproject.hello/`: short-running hello worker.
- `workers/myproject.artifact/`: artifact-producing worker.
- `inputs/`: sample input payloads.
- `schemas/`: optional input schemas.

Generated projects may keep repeated inline-goblin resource expectations under
`defaults.resources` in `goblin-king-project.json`. Project validation deep-merges those
defaults into each goblin's `resources`, so the template can use one default timeout,
memory cap, filesystem posture, or network mode and only override the few fields that
are different for `myproject.hello` or `myproject.artifact`.

## Validate

```bash
python -m goblin_king.cli project validate --project goblin-king-project.json
python -m goblin_king.cli project goblins list --project goblin-king-project.json

python -m goblin_king.cli workers validate \
  --project goblin-king-project.json \
  --input inputs/hello.json \
  --kind myproject.hello \
  --build \
  --require-success

python -m goblin_king.cli workers validate \
  --project goblin-king-project.json \
  --input inputs/artifact.json \
  --kind myproject.artifact \
  --build \
  --require-success

python -m goblin_king.cli workers validation-status --kind myproject.hello
python -m goblin_king.cli workers validation-status --kind myproject.artifact
```

Re-run the worker validation commands whenever an image digest changes. Goblin King will
not schedule an unvalidated project image by default.

If you add or edit `defaults.resources`, run `project validate` before worker validation.
The command reports the default block and rejects defaults or effective per-goblin
resources that exceed any ceilings in a sibling `goblin-resource-policies.json`.

When a project carries a resource policy file, inspect one resolved policy before the
first deployment:

```bash
python -m goblin_king.cli resource-policies inspect myproject.hello \
  --policies goblin-resource-policies.json
```

Run doctor when the stack, validation state, or runtime target is unclear:

```bash
python -m goblin_king.cli doctor \
  --project goblin-king-project.json \
  --kind myproject.hello \
  --runtime docker \
  --resource-policies goblin-resource-policies.json
```

For a Kubernetes-backed worker path, switch to `--runtime kubernetes` or
`--runtime both`. Add `--helm-values values.yaml` when a local Helm render should be
part of the diagnostic proof.

For the complete proof lifecycle and failure table, see
[Goblin Contract Validation](goblin-contract-validation.md).

## Kubernetes Placement

Project goblins may carry single-cluster Kubernetes placement intent. This metadata is
ignored by Docker/local execution and applied only by the Kubernetes worker Job runtime.

```json
{
  "goblins": {
    "myproject.retrieve": {
      "image": "myproject-retrieve:local",
      "placement": {
        "required": {
          "goblin-king.io/pool": "rag-workers"
        },
        "preferred": {
          "goblin-king.io/accelerator": "gpu"
        }
      }
    }
  }
}
```

`required` labels become Kubernetes `nodeSelector` entries. `preferred` labels become
node affinity preferences. Raw pod spec fields, tolerations, empty values, and
non-string selectors are rejected by project validation.

## Adopt

Mount or bake the generated `goblin-king-project.json` into the API and scheduler
services, then reload discovery from the admin Discovery panel or API. The React admin
reads goblin kinds from the API, so `myproject.hello` and `myproject.artifact` appear
without a frontend rebuild.

After discovery, submit or schedule the project goblins from CLI/API/admin:

```bash
python -m goblin_king.cli jobs submit myproject.hello \
  --project goblin-king-project.json \
  --input inputs/hello.json \
  --runtime docker

python -m goblin_king.cli jobs submit myproject.artifact \
  --project goblin-king-project.json \
  --input inputs/artifact.json \
  --runtime docker

python -m goblin_king.cli schedules add myproject.artifact \
  --project goblin-king-project.json \
  --input inputs/artifact.json \
  --cron "* * * * *" \
  --due-now

python -m goblin_king.cli runs show <run-id> --with-job
```

The generated workers implement the container contract directly: input/context files in,
result JSON and artifact metadata out. The language inside each container remains a
project choice.
