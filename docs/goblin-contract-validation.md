# Goblin Contract Validation

Use `goblin-king workers validate` to run container-backed goblins with temporary
contract mounts and verify that each worker writes a valid result envelope.

Validation is a scheduling gate for container-backed goblins. Goblin King must not
execute a Docker or Kubernetes goblin unless its current resolved image identity has a
passing validation record for the declared Goblin Container Contract version. The
scheduler performs this check immediately before execution and persists validation
proof or failure details.

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
- image reference resolves to an immutable local image identity,
- declared contract version is supported,
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

Persisted validation records include:

- goblin kind,
- image reference,
- resolved image identity,
- contract version,
- validator version,
- validation timestamp,
- status and failure reasons,
- effective runtime policy summary when available.

Inspect persisted validation status:

```bash
python -m goblin_king.cli workers validation-status --db .goblin-king/goblin-king.sqlite3
```

Revalidate a worker by running `workers validate` again after rebuilding or pulling a
new image. A changed image identity requires fresh validation before that image can be
executed by the scheduler.

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
