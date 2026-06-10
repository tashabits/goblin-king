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

Validation checks:

- registry kind exists,
- worker image mapping exists,
- worker context and Dockerfile exist,
- optional image build succeeds,
- worker runs with mounted input/context/result/artifact paths,
- `result.json` exists,
- result envelope validates as `GoblinResult`,
- `artifact://...` metadata points to files under the mounted artifact root.

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
