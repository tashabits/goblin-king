# Admin Runtime Audit

Use this audit before opening every roadmap PR from `roadmap-proof-preflight` onward.
It proves that Docker Compose and Helm can both launch the current registered goblin
set from the React admin surface.

This is local proof only. GitHub Actions are not required and are not sufficient.

## What The Audit Proves

- Docker admin at `http://127.0.0.1:8080/admin` can list and spawn goblins.
- Helm admin at `http://goblin-king.local/admin` can list and spawn goblins.
- Every registered goblin kind either completes successfully or produces its documented
  controlled failure.
- Artifact, progress, heartbeat, and long-running service paths are visible in admin.
- The PR body has job IDs, run IDs, screenshots, and notes for every kind.

## Prepare Docker

```bash
python -m goblin_king.cli workers build --images demo-images.json
docker compose --profile api --profile admin --profile scheduler up -d --build redis api admin scheduler long-hello
```

Open `http://127.0.0.1:8080/admin` and sign in with `local-dev-token`.

## Prepare Helm

Build and load the same local images into your Kubernetes node, then deploy the chart.
For Docker Desktop Kubernetes, the local image import method can vary by setup; the
current manual path is:

```bash
docker build -t goblin-king:local .
docker build -t goblin-king-admin-ui:local admin-ui
docker build -t goblin-king-example-long-hello:local workers/example.long-hello
python -m goblin_king.cli workers build --images demo-images.json
helm upgrade --install goblin-king charts/goblin-king --wait --timeout 5m
```

Open `http://goblin-king.local/admin` and sign in with `local-dev-token`.

## Browser Proof Steps

Run these steps in both Docker and Helm admin consoles:

1. Open **Goblin Lab**.
2. Screenshot the goblin dropdown showing the registered kinds.
3. Submit every goblin kind from the dropdown with a broad sample input:

   ```json
   {
     "message": "admin runtime audit",
     "name": "Audit",
     "target": "Audit",
     "value": "Audit"
   }
   ```

4. For `example.long-hello`, use **Services** to register, probe twice, and confirm the
   timestamp changes.
5. Use **Task Board** and **Runs** to confirm terminal status and result detail.
6. Use **Events** and **Heartbeats** to capture event and heartbeat proof.
7. For artifact goblins, capture artifact metadata/download links in **Runs & Artifacts**.
8. Treat `example.controlled-failure` and `example.behavior-shell-failure` as expected
   failures only when the error is clear and documented in the result.
9. Treat every other failed run as a blocker; fix it and rerun the full audit.

## Table Helper

After browser-spawning the goblins, use the helper to collect a PR-ready markdown table
from each admin API.

Docker:

```bash
python scripts/admin_runtime_audit.py \
  --base-url http://127.0.0.1:8080 \
  --token local-dev-token \
  --long-service-url http://long-hello:8080
```

Helm:

```bash
python scripts/admin_runtime_audit.py \
  --base-url http://goblin-king.local \
  --token local-dev-token \
  --long-service-url http://goblin-king-long-hello
```

The helper submits every listed kind through the admin API and prints:

```text
kind | status | result | job/run | notes
```

Include the helper output plus screenshots in the PR body. The screenshots prove the
browser/operator path; the table gives durable IDs for review.
