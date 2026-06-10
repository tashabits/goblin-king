# Project Template Quickstart

Use `goblin-king project init` to create a standalone adopter project that defines
container goblins without editing Goblin King source and without requiring Python worker
imports.

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

For the complete proof lifecycle and failure table, see
[Goblin Contract Validation](goblin-contract-validation.md).

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
