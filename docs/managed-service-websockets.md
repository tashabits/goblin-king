# Managed-Service WebSocket Proxy

Goblin King can relay WebSocket traffic to a registered, ready long-running service
without giving the caller the service address, container runtime, or scheduler API.
This route is additive: the existing HTTP proxy paths and response documents are
unchanged.

Use this capability for an operator-approved service that genuinely needs a duplex,
long-lived channel, such as a collaboration broker. Ordinary API calls, task results,
logs, and artifacts should continue to use their existing HTTP or run-event paths.

## Route model

The API distinguishes HTTP requests and WebSocket upgrades at the same proxy paths:

| Service source | WebSocket path |
| --- | --- |
| Registered long-running service | `/services/long-running/<service-id>/proxy/<path>` |
| Published repository service | `/repository/services/<name>/proxy/<path>` |

The repository route accepts the existing `project_id` and `version` selectors. Those
selectors, and the WebSocket `token`, remain control-plane inputs and are removed before
the upstream handshake.

A connection is eligible only when all of these checks pass:

1. The bearer or query token authenticates through the configured local, OIDC, or
   JupyterHub provider.
2. The principal can access the registered service project.
3. The service ID or published repository entry resolves to an existing registered
   record. The WebSocket path cannot supply an arbitrary upstream URL.
4. The record is `running` and contains a persisted 2xx readiness probe result.
5. The route is not draining after a healthy replacement.

For non-browser clients, prefer `Authorization: Bearer <token>`. The browser WebSocket
constructor cannot add an Authorization header, so it may use `?token=<token>`. Query
tokens can appear in API access logs even though Goblin King removes them before the
upstream connection; keep log access restricted and use short-lived scoped tokens.

## Register, probe, and connect

Register and probe an operator-approved service exactly as for HTTP proxying:

```bash
curl -X POST http://127.0.0.1:8000/services/long-running \
  -H "Authorization: Bearer local-dev-token" \
  -H "Content-Type: application/json" \
  -d '{"kind":"example.long-hello","base_url":"http://broker:8080","probe_path":"/health"}'

curl -X POST \
  http://127.0.0.1:8000/services/long-running/<service-id>/probe \
  -H "Authorization: Bearer local-dev-token"
```

Then connect from a browser:

```javascript
const socket = new WebSocket(
  "ws://127.0.0.1:8000/services/long-running/<service-id>/proxy/socket" +
    "?token=<scoped-token>&room=demo",
  ["l2l.v1"],
);

socket.addEventListener("message", event => console.log(event.data));
socket.addEventListener("close", event => {
  console.log(event.code, event.reason);
});
socket.addEventListener("open", () => socket.send("hello"));
```

The upstream receives `/socket?room=demo`. It does not receive the API token,
Authorization header, Cookie, proxy authorization, API-key headers, or WebSocket
handshake headers supplied by the client. An ordinary application header and Origin
may be forwarded. The upstream selects the final subprotocol from the validated client
offer.

## Readiness, promotion, and last-known-good behavior

Starting a published repository service is a staged replacement:

1. Goblin King starts a uniquely named candidate runtime without stopping the active
   runtime.
2. The runtime adapter completes its startup check.
3. Goblin King independently probes the candidate's configured readiness path.
4. Only a 2xx result updates the active-service pointer.
5. New connections resolve to the promoted service.
6. Existing WebSocket connections keep their direct connection to the old service.
7. The old runtime stops after its last local connection closes or the configured drain
   timeout expires. A timed-out connection closes with code `1012`.

If startup or the pre-promotion probe fails, Goblin King stops the candidate, marks its
service record failed, and leaves the previous active record and runtime untouched.
Default repository routing can therefore fall back to the newest ready published
version. An explicitly requested failed version does not silently change versions.

Directly registered long-running services use the same authentication, project,
readiness, and relay checks. Automated rolling promotion applies to repository service
starts because Goblin King owns those runtime transitions.

## Bounded transport policy

Every setting is operator-controlled, positive, and capped by validation:

| JSON key | Helm key | Default | Maximum |
| --- | --- | ---: | ---: |
| `max_message_bytes` | `maxMessageBytes` | 1 MiB | 64 MiB |
| `max_queue_messages` | `maxQueueMessages` | 16 | 1,024 |
| `write_limit_bytes` | `writeLimitBytes` | 32 KiB | 16 MiB |
| `open_timeout_seconds` | `openTimeoutSeconds` | 10 s | 300 s |
| `idle_timeout_seconds` | `idleTimeoutSeconds` | 300 s | 86,400 s |
| `close_timeout_seconds` | `closeTimeoutSeconds` | 10 s | 300 s |
| `drain_timeout_seconds` | `drainTimeoutSeconds` | 30 s | 3,600 s |

The relay uses the configured upstream message ceiling, receive queue, and write limit.
It awaits each send before reading another message in that direction, allowing socket
and library pressure to propagate instead of creating an unbounded application queue.
The same message ceiling is enforced before client messages are sent upstream. Text and
binary messages remain distinct.

Configure Docker/API defaults in `goblin-king-api.json`:

```json
{
  "service_websocket_proxy": {
    "max_message_bytes": 1048576,
    "max_queue_messages": 16,
    "write_limit_bytes": 32768,
    "open_timeout_seconds": 10,
    "idle_timeout_seconds": 300,
    "close_timeout_seconds": 10,
    "drain_timeout_seconds": 30
  }
}
```

Configure Kubernetes with the equivalent Helm values:

```yaml
config:
  serviceWebSocket:
    maxMessageBytes: 1048576
    maxQueueMessages: 16
    writeLimitBytes: 32768
    openTimeoutSeconds: 10
    idleTimeoutSeconds: 300
    closeTimeoutSeconds: 10
    drainTimeoutSeconds: 30
```

## Close and failure behavior

| Close code | Meaning at this route |
| ---: | --- |
| `1000` / `1001` | Normal client/upstream close or configured idle close. |
| `1008` | Missing/invalid auth, project denial, invalid selector, or invalid route. |
| `1009` | A message exceeded the configured byte ceiling. |
| `1012` | The route was replaced or its bounded drain expired. |
| `1013` | Rate limit, readiness gate, or upstream availability prevented service. |
| `3xxx` / `4xxx` | Valid application close codes are propagated from upstream. |

Each terminal relay writes an audit row and emits `service.websocket_proxy` with a safe
outcome, close code, reason, frame counts, and byte counts. Repository replacement also
emits `service.websocket_drain_completed`. Secrets and message bodies are not recorded.

## Deployment boundary

The relay is implemented by the Goblin King API in both Docker Compose and Kubernetes.
It needs network access to the registered service, but neither the caller nor the
adopting application receives the Docker socket, Kubernetes client, scheduler
credentials, or an arbitrary-connect operation. Kubernetes uses the same route and
settings rendered into the API ConfigMap.

Connection-aware drain state is local to one API process. The chart's default
`api.replicas: 1` therefore provides the proven strict drain behavior. Operators using
multiple API replicas must keep the bounded drain timeout and accept that a process
cannot count connections owned by another replica; a distributed connection registry
is not part of this change.

## Reproduce the proof

The Docker Compose proof uses an isolated API, Redis, and echo service and mounts no
host runtime socket into the API:

```bash
docker compose -p gk157-proof \
  -f scripts/proofs/docker-compose.managed-service-websocket.yml \
  up -d --build --wait

python scripts/proofs/managed_service_websocket_proof.py \
  --api-url http://127.0.0.1:18057 \
  --upstream-url http://upstream:8080

docker compose -p gk157-proof \
  -f scripts/proofs/docker-compose.managed-service-websocket.yml \
  down -v
```

The same client can target a port-forwarded Helm API and an in-cluster echo service.
The exact observed Compose and disposable-kind evidence is recorded in
[the issue 157 proof](proofs/issue-157-managed-service-websockets.md).

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Upgrade receives 403 | Supply a valid bearer/query token and confirm provider configuration. |
| Close code 1008 | Check project scope, service ID/name, version selector, and registered URL. |
| Close code 1013 before accept | Probe the service and inspect its stored status/result. |
| Close code 1009 | Raise the message limit only after reviewing memory and peer behavior. |
| New connections work but old one closes during deploy | Increase the bounded drain timeout or make the client reconnect on `1012`. |
| Upstream never sees the API token | Expected: control-plane credentials are intentionally stripped. |
| Compose works but Kubernetes does not | Verify API-pod DNS/egress to the registered Service and compare rendered limits. |
