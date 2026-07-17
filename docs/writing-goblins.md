# Writing Goblins

Start with the [Goblin Container Contract](goblin-container-contract.md). This guide is
the practical authoring path for one short-running goblin.

## Directory Layout

```text
workers/my.goblin/
  Dockerfile
  worker.py       # or main.go, index.js, run.sh, Program.cs, etc.
  README.md
```

Add the worker image to `goblin-images.json`:

```json
{
  "workers": {
    "my.goblin": {
      "context": "workers/my.goblin",
      "dockerfile": "Dockerfile",
      "image": "my-goblin:local"
    }
  }
}
```

Add the goblin kind to a registry file or package entry point:

```json
{
  "goblins": [
    {
      "kind": "my.goblin",
      "display_name": "My Goblin",
      "module": "my_project.definitions",
      "entrypoint": "run",
      "timeout_seconds": 60,
      "max_retries": 0
    }
  ]
}
```

Registry metadata identifies the goblin. The worker image executes it.

## Read Input And Context

Read the mounted file paths from environment variables:

```text
GOBLIN_INPUT_PATH=/goblin/input.json
GOBLIN_CONTEXT_PATH=/goblin/context.json
GOBLIN_RESULT_PATH=/goblin/result.json
GOBLIN_ARTIFACT_ROOT=/goblin/artifacts
```

Input example:

```json
{
  "name": "World"
}
```

Context example:

```json
{
  "run_id": "run-123",
  "artifact_root": "/goblin/artifacts",
  "metadata": {
    "job_id": "job-123",
    "kind": "my.goblin"
  }
}
```

## Write Result JSON

Write the result envelope to `GOBLIN_RESULT_PATH`:

```json
{
  "status": "success",
  "data": {
    "message": "Hello World"
  },
  "artifacts": [],
  "metrics": {
    "items_processed": 1
  },
  "handoff": [],
  "error": null
}
```

On failure, write:

```json
{
  "status": "failed",
  "data": {},
  "artifacts": [],
  "metrics": {},
  "handoff": [],
  "error": "explain what failed"
}
```

Exit `0` for success and nonzero for failure.

## Write Artifacts

Write files under `GOBLIN_ARTIFACT_ROOT` and return relative metadata:

```json
{
  "name": "summary.txt",
  "uri": "summary.txt",
  "media_type": "text/plain"
}
```

Do not return absolute paths or path traversal. If the King cannot prove an artifact is
inside the artifact root, it will not serve it.

A local `file:` URI beneath the same root is also portable across Docker and Kubernetes.
Use the language runtime's file-URL helper so path separators and special characters are
encoded correctly. In Node:

```javascript
import { pathToFileURL } from "node:url";

const artifactUri = pathToFileURL(artifactPath).href;
```

The `artifact://<name>` convenience locator is supported for Docker downloads but is not
accepted by Kubernetes durable retention.

## Logs, Events, And Progress

Use stdout/stderr for concise diagnostic logs. Put durable data in result JSON,
artifacts, events, metrics, or handoff payloads.

For user-visible progress or text that must arrive before the container exits, use the additive
bounded [live run-event contract](live-run-events.md). Docker and Kubernetes provide the same
run-local publisher environment; existing workers may ignore it.

Longer-running workers should publish heartbeat envelopes using the heartbeat Redis
variables. Short workers can publish `running` and `completed` heartbeats or skip
heartbeats when the run is too brief.

## Test Locally

Build the image:

```bash
docker build -t my-goblin:local workers/my.goblin
```

Run with temporary contract files:

```bash
mkdir -p .goblin-king/dev-run/artifacts
printf '{"name":"World"}' > .goblin-king/dev-run/input.json
printf '{"run_id":"dev-run","artifact_root":".goblin-king/dev-run/artifacts","metadata":{}}' \
  > .goblin-king/dev-run/context.json

docker run --rm \
  -e GOBLIN_RUN_ID=dev-run \
  -e GOBLIN_KIND=my.goblin \
  -e GOBLIN_INPUT_PATH=/goblin/input.json \
  -e GOBLIN_CONTEXT_PATH=/goblin/context.json \
  -e GOBLIN_RESULT_PATH=/goblin/result.json \
  -e GOBLIN_ARTIFACT_ROOT=/goblin/artifacts \
  -v "$PWD/.goblin-king/dev-run:/goblin" \
  my-goblin:local

cat .goblin-king/dev-run/result.json
```

The goblin does not need to import Goblin King Python code to pass this test.
