# Language-Agnostic Goblin Closeout

This closeout records the state after the container-first worker phases.

## Completed

- The canonical worker contract is `docs/goblin-container-contract.md`.
- Goblins are documented as OCI/Docker containers, not Python functions.
- Python helpers remain optional for definitions, local tests, and in-process debugging.
- Hello examples exist for Go, Rust, Node.js, Java, .NET/C#, Ruby, PHP, shell, Python,
  C/WASI, and Rust/WASI.
- Behavior examples cover artifacts, progress/logging, transforms, controlled failure,
  cancellation-friendly loops, and WASI context reads.
- `examples/cross-language-goblins.json` and `examples/cross-language-images.json`
  wire hello/WASI examples into Docker runtime proof.
- `examples/behavior-goblins.json` and `examples/behavior-images.json` wire behavior
  examples into Docker runtime proof.
- `goblin-king workers validate` provides local image/contract validation.
- The React admin labels active goblins as container workers with image mappings.

## Proof Commands

```bash
python -m pytest
python -m ruff check .
cd admin-ui && npm test -- --run
cd admin-ui && npm run build
make validate-cross-language-workers
make validate-behavior-workers
```

## Documentation Map

- [Goblin Container Contract](goblin-container-contract.md)
- [What Is A Goblin?](what-is-a-goblin.md)
- [Writing Goblins](writing-goblins.md)
- [Goblin Dockerfiles](goblin-dockerfiles.md)
- [Language-Agnostic Workers](language-agnostic-workers.md)
- [Goblin Examples Index](examples-index.md)
- [Choose Your Runtime](choose-your-runtime.md)
- [Goblin Contract Validation](goblin-contract-validation.md)
- [Goblin Resource Policies](goblin-resource-policies.md)
- [Security Model](security-model.md)

## Deferred

- Native Kubernetes WASI scheduling remains out of scope.
- Official SDKs for every language remain out of scope.
- Cloud-specific object storage examples remain out of scope.
- Runtime-level enforcement for CPU, memory, process, network, log, and artifact policy
  ceilings remains future work. The policy model and deployment mappings now live in
  `docs/goblin-resource-policies.md`.

The King now has a court full of languages. The law is still one contract.
