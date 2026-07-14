# Issue 157 Managed-Service WebSocket Proof

## Scope

This proof covers the additive WebSocket route for registered long-running services and
the shared relay used by published repository services. It records real Docker Compose
and disposable-kind results; focused tests cover repository promotion, failed-candidate
fallback, and old-connection drain.

- Date: 2026-07-14 00:23 -07:00
- Implementation commit tested: `1a8827ec8ede`
- Docker Engine: `29.4.0`
- Docker Compose: `5.1.1`
- Helm: `v4.1.3`
- kind: `v0.32.0`
- Kubernetes node: `kindest/node:v1.36.1`
- Proof image ID:
  `sha256:95dd98f9a6a486908fb57ef2094ac41f24f1c0644fa8d34fa4732b8e92911a72`

## Docker Compose

Commands:

```powershell
$env:GOBLIN_KING_WEBSOCKET_PROOF_PORT='18057'
docker compose -p gk157-proof `
  -f scripts/proofs/docker-compose.managed-service-websocket.yml `
  up -d --build --wait
python scripts/proofs/managed_service_websocket_proof.py `
  --api-url http://127.0.0.1:18057 `
  --upstream-url http://upstream:8080
docker inspect gk157-proof-api-1 --format '{{json .HostConfig.Binds}}'
```

Observed client result:

```json
{"binary_echo_hex":"000102","credentials_stripped":true,"probe_status":"running","service_id":"1d63fab4-6de7-4fd7-a21d-e8fd405ccfbd","subprotocol":"l2l.v1","text_echo":"hello websocket","unauthorized_rejected":true}
```

Observed container state:

```text
gk157-proof-api-1        Up (healthy)   0.0.0.0:18057->8000/tcp
gk157-proof-redis-1      Up (healthy)
gk157-proof-upstream-1   Up (healthy)
```

`docker inspect` printed `null` for API bind mounts. API logs recorded an unauthenticated
upgrade as `403`, then accepted the token-authenticated connection. Upstream logs showed
`WebSocket /socket?room=blue`; the `token` selector did not cross the proxy. The proof
stack was removed with `docker compose ... down -v`.

## Kubernetes

An isolated `gk157-proof` kind cluster was created after verifying the official kind
release checksum. The proof image was loaded into the node. The Helm release used:

```yaml
admin:
  enabled: false
image:
  pullPolicy: Never
  repository: goblin-king-managed-websocket-proof
  tag: local
persistence:
  enabled: false
scheduler:
  enabled: false
workers:
  exampleLongHello:
    enabled: false
```

A proof-only in-cluster echo Deployment mounted the same source fixture through a
ConfigMap. After both rollouts, observed resources were:

```text
pod/gk157-api-79c9d457bd-sgjm2       1/1   Running
pod/gk157-redis-5d9c4d6dbb-kz5h4     1/1   Running
pod/gk157-ws-echo-6579f84db8-rdfz6   1/1   Running
service/gk157-api                     ClusterIP   8000/TCP
service/gk157-redis                   ClusterIP   6379/TCP
service/gk157-ws-echo                 ClusterIP   8080/TCP
```

The same proof client targeted a port-forwarded API and
`http://gk157-ws-echo:8080`. Observed result:

```json
{"binary_echo_hex":"000102","credentials_stripped":true,"probe_status":"running","service_id":"5267ae93-4607-48dc-80e8-ac5edfe659cb","subprotocol":"l2l.v1","text_echo":"hello websocket","unauthorized_rejected":true}
```

The rendered API pod used `goblin-king-managed-websocket-proof:local`; inspection found
no `hostPath` or `docker.sock`. API logs again recorded the rejected unauthenticated
upgrade, successful probe, and accepted authenticated upgrade. The kind cluster was
deleted after evidence collection.

## Automated coverage

Focused tests prove:

- bearer and browser-compatible query-token authentication;
- project-boundary denial;
- readiness rejection;
- text, binary, query, header, Origin, and subprotocol behavior;
- client and upstream message ceilings;
- bounded library queues and awaited backpressure;
- idle timeout and close propagation;
- graceful and forced drain;
- healthy repository promotion with an old connection still active;
- failed replacement retaining the old active service and runtime;
- Helm setting render and absence of host runtime authority;
- compatibility of existing API, notebook, and HTTP service paths.

Final local verification passed:

```text
python -m pytest -q
431 passed, 5 skipped in 81.96s

python -m ruff check .
All checks passed!

helm lint charts/goblin-king
1 chart(s) linted, 0 chart(s) failed

helm template gk157 charts/goblin-king <proof values>
helm_render=passed
```

## Known limitation

Connection counts used for early drain completion are process-local. Strict
connection-aware retirement is proven with the chart's default single API replica.
The configured timeout remains the bounded retirement mechanism when an operator runs
multiple API processes, but distributed connection accounting is outside this issue.
