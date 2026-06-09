# Adopting Project Example

This fixture shows how a host project can describe multiple goblin packages without
copying Goblin King internals.

It demonstrates:

- Multiple registry files in one project settings file.
- Worker image map coverage for each discovered goblin.
- A short-running goblin and a long-running service goblin.
- Static validation before Docker/Helm deployment.

Validate the fixture from the repository root:

```bash
goblin-king project validate --project examples/adopting-project/goblin-king-project.json
goblin-king project goblins list --project examples/adopting-project/goblin-king-project.json
```

Build the worker images when Docker proof is needed:

```bash
goblin-king workers build --images examples/adopting-project/goblin-images.json
```
