# First Hour With Goblin King In Your Project

This is the shortest path from a host project to a working goblin.

## 1. Install Goblin King

```bash
python -m pip wheel ../goblin-king -w dist
python -m pip install dist/goblin_king-*.whl
```

For local development, an editable install is fine:

```bash
python -m pip install -e ../goblin-king[dev]
```

## 2. Generate A Plugin

```bash
goblin-king project init-package ./my-goblins \
  --kind project.hello \
  --package-name project_goblins \
  --image project-hello:local
```

## 3. Validate Discovery

```bash
goblin-king project validate --project ./my-goblins/goblin-king-project.json
goblin-king project goblins list --project ./my-goblins/goblin-king-project.json
```

## 4. Build The Worker

```bash
goblin-king workers build --images ./my-goblins/goblin-images.json
```

## 5. Start The Stack

Use Docker Compose for the first run. The fixture in
`examples/adopting-project/docker-compose.host-project.yml` shows how to mount project
settings and worker images.

## 6. Reload Discovery

```bash
curl -X POST http://127.0.0.1:8080/admin-api/admin/discovery/reload \
  -H "Authorization: Bearer local-dev-token"
```

## 7. Spawn And Inspect

Open `http://127.0.0.1:8080/admin`, log in with `local-dev-token`, choose the new
goblin kind in **Goblin Lab**, submit it, then inspect **Task Board**, **Runs**,
**Events**, **Heartbeats**, and **Captured Traffic**.
