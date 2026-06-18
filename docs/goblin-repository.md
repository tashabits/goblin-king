# Goblin Repository Contract

The Goblin Repository is an optional service for sharing approved notebook-authored
goblins. It is off by default and must be enabled explicitly in API settings or in the
deployment values that render those settings.

The first durable contract is a two-part model:

- `RepositoryEntryRecord` represents the project-scoped catalog name users search and
  call.
- `RepositoryVersionRecord` represents one submitted source bundle, runner image,
  validation proof, approval state, and publication state.

Repository names are unique within a project while active. Source or runner-image
changes must create the next draft version. Published versions are immutable; service
and notebook APIs resolve by repository entry name plus either a specific version or the
latest published version.

The review flow is:

```text
draft -> validated -> pending_review -> approved -> published
```

`rejected` and `retired` are side states. Validation is mandatory before runtime use.
Approval is a sharing gate rather than a security certification.

## API And Auth Contract

The repository API is enabled only when the API settings include:

```json
{
  "repository": {
    "enabled": true,
    "url": "http://repository:8000"
  }
}
```

When enabled, repository routes require bearer auth. The default access model is:

- Admins can list all projects, filter review queues, approve, publish, reject, and
  retire entries.
- Members and viewers can list/search published entries in their own project scope.
- Entry owners can inspect and advance their own draft through validation and review
  request.
- Non-admin callers cannot request another `project_id`, inspect another user's draft
  or review entry, or approve or publish versions.
- JupyterHub and OIDC principals use the same role and project mapping as the rest of
  the API.

Default list/search behavior should return only active published entries for the
caller's project. The search endpoint should support bounded pagination plus exact
filters for `project_id`, `status`, and `type`, and a text query `q` against entry name,
display name, description, and tags:

```bash
curl -H "Authorization: Bearer <project-token>" \
  "http://127.0.0.1:8000/repository/entries?q=demo&type=notebook_function"
```

Admin review queue example:

```bash
curl -H "Authorization: Bearer <admin-token>" \
  "http://127.0.0.1:8000/repository/entries?status=pending_review&project_id=default"
```

Submit and publish are separate steps. A contributor submits source, validates it,
and requests review:

```bash
curl -X POST \
  -H "Authorization: Bearer <project-token>" \
  -H "Content-Type: application/json" \
  -d '{"name":"demo.hello","type":"notebook_function","source":"def run(payload):\n    return {\"message\": payload.get(\"name\", \"hello\")}\n"}' \
  http://127.0.0.1:8000/repository/entries
```

```bash
curl -X POST \
  -H "Authorization: Bearer <project-token>" \
  -H "Content-Type: application/json" \
  -d '{"input":{"name":"repository"},"require_success":true}' \
  http://127.0.0.1:8000/repository/entries/<entry-id>/validate
```

```bash
curl -X POST \
  -H "Authorization: Bearer <project-token>" \
  -H "Content-Type: application/json" \
  -d '{"note":"ready for review"}' \
  http://127.0.0.1:8000/repository/entries/<entry-id>/request-review
```

Publish is admin-only:

```bash
curl -X POST \
  -H "Authorization: Bearer <admin-token>" \
  -H "Content-Type: application/json" \
  -d '{}' \
  http://127.0.0.1:8000/repository/entries/<entry-id>/publish
```

## Invoke Published Goblins By Name

After approval and publication, callers do not need to copy notebook source. They call
the project-scoped repository name, which resolves to the latest published immutable
version unless a specific `version` is supplied.

Run a published function goblin:

```bash
curl -X POST \
  -H "Authorization: Bearer <project-token>" \
  -H "Content-Type: application/json" \
  -d '{"input":{"name":"Ada"}}' \
  http://127.0.0.1:8000/repository/functions/demo.hello/run
```

Start, probe, proxy, and stop a published ASGI service goblin:

```bash
curl -X POST \
  -H "Authorization: Bearer <project-token>" \
  -H "Content-Type: application/json" \
  -d '{}' \
  http://127.0.0.1:8000/repository/services/demo.long-hello/start
```

```bash
curl -X POST \
  -H "Authorization: Bearer <project-token>" \
  -H "Content-Type: application/json" \
  -d '{}' \
  http://127.0.0.1:8000/repository/services/demo.long-hello/probe
```

```bash
curl -H "Authorization: Bearer <project-token>" \
  http://127.0.0.1:8000/repository/services/demo.long-hello/proxy/hello
```

```bash
curl -X POST \
  -H "Authorization: Bearer <project-token>" \
  -H "Content-Type: application/json" \
  -d '{}' \
  http://127.0.0.1:8000/repository/services/demo.long-hello/stop
```

The notebook helper exposes the same invocation path:

```python
result = client.run_repository_function("demo.hello", {"name": "Ada"})

service = client.repository_service("demo.long-hello")
service.start(progress=True)
service.probe()
service.proxy("/hello")
service.stop()
```

Normal users only resolve entries in their project scope. Admins can specify
`project_id` to resolve a different project, and any caller can specify `version` to use
a particular published version. Draft, rejected, retired, and unpublished versions are
not invokable by normal callers.

## Notebook Repository Workflow

The JupyterHub workbook path can use the notebook helper instead of hand-written HTTP
requests. The helper reads `GOBLIN_KING_API_URL`, `GOBLIN_KING_REPOSITORY_URL`, and
`JUPYTERHUB_API_TOKEN` from the notebook server environment, or accepts explicit
constructor arguments:

```python
from goblin_king.notebooks import GoblinKingNotebookClient

client = GoblinKingNotebookClient(
    api_url="http://goblin-king-api.default.svc.cluster.local:8000",
    repository_url="http://goblin-king-repository.default.svc.cluster.local:8000",
    token=os.environ["JUPYTERHUB_API_TOKEN"],
)
```

Submit a short function goblin from source already defined in the workbook:

```python
def workbook_hello(payload):
    return {"message": f"Hello {payload.get('name', 'Repository')}"}

submission = client.submit_repository_function(
    workbook_hello,
    name="workbook.shared-hello",
    display_name="Workbook Shared Hello",
    description="Short hello-world function submitted from a notebook",
    tags=["workbook", "hello"],
    timeout_seconds=30,
)

submission.validate({"name": "Validation"}, progress=True)
submission.request_review("ready for admin review", progress=True)
```

Submit an ASGI service from workbook source the same way:

```python
service_source = """
from fastapi import FastAPI

app = FastAPI()

@app.get("/hello")
def hello():
    return {"message": "Hello World"}
""".strip()

service_submission = client.submit_repository_service(
    source=service_source,
    name="workbook.shared-long-hello",
    app_name="app",
    requirements=["fastapi>=0.115,<1"],
    probe_path="/hello",
    tags=["workbook", "service"],
)

service_submission.validate(progress=True, timeout_seconds=180)
service_submission.request_review("service ready for admin review", progress=True)
```

An admin token can approve and publish the same entries:

```python
pending = client.list_repository_entries(status="pending_review", limit=100)
entry_id = pending["items"][0]["entry"]["id"]

client.approve_repository_entry(entry_id, note="approved", progress=True)
client.publish_repository_entry(entry_id, progress=True)
```

Another authorized project user can discover and invoke by name without copying source:

```python
published = client.search_repository_entries("workbook", status="published")

run = client.run_repository_function(
    "workbook.shared-hello",
    {"name": "Consumer"},
    progress=True,
)

service = client.repository_service("workbook.shared-long-hello")
service.start(progress=True, timeout_seconds=180)
service.probe()
service.proxy("/hello")
service.stop()
```

The example workbooks in `examples/jupyterhub-goblin-king/` split that flow by role:

- `workbook-repository-submit.ipynb` for a contributor such as `bob`.
- `workbook-repository-admin.ipynb` for an admin such as `alice`.
- `workbook-repository-consume.ipynb` for a consumer such as `carol`.

If repository routes are not enabled, helper errors include the repository base URL,
`repository.enabled=true`, and `GOBLIN_KING_REPOSITORY_URL` so the workbook points at the
operator fix instead of failing as an opaque 404.

## Docker Compose Enablement

For local Compose, add the repository block to the API settings file used by the `api`
service. The default stack reads `goblin-king-api.json`, so a local proof can use a
small override file or edit that JSON directly:

```json
{
  "repository": {
    "enabled": true,
    "url": "http://repository:8000"
  }
}
```

The Compose stack also includes a dedicated optional `repository` profile. Start it with
Redis and the API/admin stack when you want a separate local service endpoint:

```bash
GOBLIN_REPOSITORY_ENABLED=true \
docker compose --profile api --profile admin --profile repository up -d --build redis api admin repository
```

Use a project-scoped token for normal search calls and the bootstrap/admin token only
for review and publication actions. The repository uses the same SQLite database and
artifact volume as the rest of the local API, so `docker compose down --volumes` removes
repository rows along with jobs, runs, and auth records.

## Helm Enablement

In Kubernetes, enable the repository through chart values so the rendered API
ConfigMap contains the same JSON settings:

```yaml
repository:
  enabled: true
  url: http://goblin-king-repository:8000
```

Render before applying:

```bash
helm template goblin-king charts/goblin-king \
  --set repository.enabled=true \
  --set repository.url=http://goblin-king-repository:8000
```

For a live upgrade, use the same values file you use for API/auth settings:

```bash
helm upgrade --install goblin-king charts/goblin-king -f values.yaml
```

Keep repository endpoints behind the same authenticated ingress policy as the API.
Review and publication actions must remain admin-only.

## JupyterHub Stack Enablement

JupyterHub-backed users can search the repository when Hub auth is enabled and their Hub
groups map to a project. Add the repository settings beside the existing Hub settings in
the stack values:

```yaml
config:
  jupyterhub:
    enabled: true
    projectGroups:
      goblin-users: default
      goblin-admins: default
    adminGroups:
      - goblin-admins
repository:
  enabled: true
  url: http://goblin-king-repository.default.svc.cluster.local:8000
```

With that mapping:

- Users in `goblin-users` can search published entries for `default`.
- Users in `goblin-admins` can review and publish entries.
- Users outside allowed Hub groups receive the normal JupyterHub auth denial before any
  repository lookup happens.

The local stack proof should enable the repository in
`examples/jupyterhub-goblin-king/goblin-king.values.yaml` and then run:

```bash
make jupyterhub-stack-up JUPYTERHUB_STACK_REBUILD=1
```

After the stack is up, call the repository API with a Hub user token from a notebook or
from the Hub service route. Do not use the Hub service token for user search flows; it is
only for the API to validate Hub user tokens.
