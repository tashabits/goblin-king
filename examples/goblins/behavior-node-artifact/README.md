# Node Artifact Behavior Goblin

Writes a text artifact under `GOBLIN_ARTIFACT_ROOT`, returns artifact metadata with a
standards-derived local `file:` URI, and logs the artifact path to stdout. The local URI
works with both Docker artifact downloads and Kubernetes durable retention; the worker
does not assemble or encode the URI by hand.

```powershell
docker build -t goblin-example-behavior-node-artifact:local .
```
