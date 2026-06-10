# Goblin Contract Validation

Use `goblin-king workers validate` to run container-backed goblins with temporary
contract mounts and verify that each worker writes a valid result envelope.

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

Validation checks:

- registry kind exists,
- worker image mapping exists,
- worker context and Dockerfile exist,
- optional image build succeeds,
- optional prebuilt image availability check succeeds,
- worker runs with mounted input/context/result/artifact paths,
- `result.json` exists,
- result envelope validates as `GoblinResult`,
- `artifact://...` metadata points to files under the mounted artifact root.

Validation failures are meant to be actionable for project authors. Missing image,
missing result file, invalid result JSON, nonzero exit, timeout, and artifact metadata
errors are reported as failed validation rows.

By default, a valid failed result envelope is still contract-valid. Use
`--require-success` when failed result envelopes should fail validation.

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
