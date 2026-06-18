# Goblin Directory Contract

The Goblin Directory is an optional service for sharing approved notebook-authored
goblins. It is off by default and must be enabled explicitly in API settings or in the
deployment values that render those settings.

The first durable contract is a two-part model:

- `RepositoryEntryRecord` represents the project-scoped catalog name users search and
  call.
- `RepositoryVersionRecord` represents one submitted source bundle, runner image,
  validation proof, approval state, and publication state.

Directory names are unique within a project while active. Source or runner-image
changes must create the next draft version. Published versions are immutable; service
and notebook APIs resolve by directory entry name plus either a specific version or the
latest published version.

The review flow is:

```text
draft -> validated -> pending_review -> approved -> published
```

`rejected` and `retired` are side states. Validation is mandatory before runtime use.
Approval is a sharing gate rather than a security certification.

## API And Auth Contract

The Directory API is enabled only when the API settings include:

```json
{
  "repository": {
    "enabled": true,
    "url": "http://directory-api:8000"
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
result = client.run_directory_function("demo.hello", input={"name": "Ada"})

service = client.directory_service("demo.long-hello")
service.start(progress=True)
service.probe()
service.proxy("/hello")
service.stop()
```

Normal users only resolve entries in their project scope. Admins can specify
`project_id` to resolve a different project, and any caller can specify `version` to use
a particular published version. Draft, rejected, retired, and unpublished versions are
not invokable by normal callers.

## Notebook Directory Workflow

The JupyterHub workbook path can use the notebook helper instead of hand-written HTTP
requests. The helper reads `GOBLIN_KING_API_URL`, `GOBLIN_KING_REPOSITORY_URL`, and
`JUPYTERHUB_API_TOKEN` from the notebook server environment, or accepts explicit
constructor arguments:

```python
from goblin_king.notebooks import GoblinKingNotebookClient

client = GoblinKingNotebookClient(
    api_url="http://goblin-king-api.default.svc.cluster.local:8000",
    repository_url="http://goblin-king-directory-api.default.svc.cluster.local:8000",
    token=os.environ["JUPYTERHUB_API_TOKEN"],
)
```

Submit a short function goblin from source already defined in the workbook:

```python
def workbook_hello(payload):
    return {"message": f"Hello {payload.get('name', 'Directory')}"}

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

run = client.run_directory_function(
    "workbook.shared-hello",
    input={"name": "Consumer"},
    progress=True,
)

service = client.directory_service("workbook.shared-long-hello")
service.start(progress=True, timeout_seconds=180)
service.probe()
service.proxy("/hello")
service.stop()
```

The example workbooks in `examples/jupyterhub-goblin-king/` split that flow by role:

- `workbook-directory-submit.ipynb` for a contributor such as `bob`.
- `workbook-directory-admin.ipynb` for an admin such as `alice`.
- `workbook-directory-consume.ipynb` for a consumer such as `carol`.

If repository routes are not enabled, helper errors include the repository base URL,
`repository.enabled=true`, and `GOBLIN_KING_REPOSITORY_URL` so the workbook points at the
operator fix instead of failing as an opaque 404.

## Browser Goblin Directory UI

The optional directory UI is a separate JupyterHub service from the admin panel. It is
mounted at:

```text
/services/goblin-directory/
```

The UI uses Hub OAuth for browser login and keeps the Hub OAuth token in the service
backend. Browser requests go through relative `ui-api/...` routes, and the service
forwards only authenticated Directory/job/run requests to the Goblin King API or
Directory API as the signed-in Hub user. There is no token-paste login in this path.

The UI exposes:

- Directory: search approved published entries by name, type, tag, owner, or text.
- Submit Bundle: upload and preview a v1 zip bundle, then submit a draft.
- My Submissions: validate owned drafts and request review.
- Review Queue: admin-only approve, reject, publish, and retire actions.
- Entry Detail: version, source hash, validation proof, owner, status, and tags.
- Runtime Results: run function goblins and start/probe/proxy/stop service goblins by
  directory name.

Enable it locally with the Directory API:

```bash
make jupyterhub-stack-up \
  JUPYTERHUB_STACK_REBUILD=1 \
  GOBLIN_DIRECTORY_ENABLED=1 \
  GOBLIN_DIRECTORY_UI_ENABLED=1
```

Then port-forward the Hub proxy and open the UI:

```bash
kubectl port-forward -n default svc/proxy-public 8080:http
```

```text
http://127.0.0.1:8080/services/goblin-directory/
```

The one-command proof is:

```bash
make jupyterhub-directory-ui-proof
```

That proof uses the same local Hub users as the workbook proof: `bob` submits bundles,
`alice` approves and publishes, `carol` discovers and invokes the published entries,
and `mallory` is denied.

## JupyterLab Goblin Directory Picker

The local Hub stack can also install a real JupyterLab extension into user notebook
servers. The extension appears as a left-sidebar panel named **Goblin Directory** and
talks only to the user's own notebook server:

```text
/goblin-directory/api
```

The Python Jupyter server extension reads `JUPYTERHUB_API_TOKEN` and forwards requests
to `GOBLIN_KING_DIRECTORY_URL` as the signed-in Hub user. If that URL is not set, it
falls back to the existing API URL behavior. The browser never receives or asks for a
token.

The picker lists published entries visible to the Hub user. Each option shows display
name, Directory name, type, project, version, and tags. Function entries expose a JSON
input box and `Run Function`. Service entries expose `Start Service`, `Probe`, `Proxy`,
and `Stop`, with proof JSON shown directly in the panel.

Enable the picker locally by rebuilding the Hub stack with the Directory UI enabled:

```bash
make jupyterhub-stack-up \
  JUPYTERHUB_STACK_REBUILD=1 \
  GOBLIN_DIRECTORY_ENABLED=1 \
  GOBLIN_DIRECTORY_UI_ENABLED=1
```

That build includes `examples/jupyterhub-goblin-king/singleuser/Dockerfile`, which
installs the Python user-server extension and the JupyterLab frontend package
`jupyterlab-goblin-directory/`.

The one-command proof is:

```bash
make jupyterhub-directory-picker-proof
```

It brings up Hub, Goblin King, the Directory API, the Directory browser app, and the
custom single-user image. The proof has `bob` submit function and service entries,
`alice` approve and publish them, `carol` invoke both through the user-server picker
proxy, and `mallory` fail to discover entries through the same path.

## Upload Bundle v1

The browser UI accepts a `.zip` bundle. The required root manifest is
`goblin-directory.json`, and v1 executes exactly one Python entrypoint source file.
Extra files are shown in preview for reviewer context but are not executed.

Function bundle example:

```json
{
  "schema_version": 1,
  "name": "demo.hello",
  "type": "notebook_function",
  "entrypoint": "hello.py",
  "display_name": "Demo Hello",
  "description": "Shared hello-world function",
  "tags": ["demo", "hello"],
  "function_name": "run",
  "timeout_seconds": 30,
  "max_retries": 0
}
```

Service bundle example:

```json
{
  "schema_version": 1,
  "name": "demo.long-hello",
  "type": "notebook_service",
  "entrypoint": "service.py",
  "display_name": "Demo Long Hello",
  "description": "Shared ASGI hello-world service",
  "tags": ["demo", "service"],
  "app_name": "app",
  "requirements": ["fastapi>=0.115,<1"],
  "requirements_file": "requirements.txt",
  "port": 8080,
  "probe_path": "/hello"
}
```

The UI backend rejects unsafe bundles before submitting to the Directory API:

- path traversal or absolute paths
- missing manifest or missing entrypoint
- invalid names, tags, schema versions, or goblin types
- oversized bundles, source files, requirements files, or file counts
- binary or non-UTF-8 entrypoint files
- unsafe or missing requirements file paths

## Docker Compose Enablement

For local Compose, add the repository block to the API settings file used by the `api`
service. The default stack reads `goblin-king-api.json`, so a local proof can use a
small override file or edit that JSON directly:

```json
{
  "repository": {
    "enabled": true,
    "url": "http://directory-api:8000"
  }
}
```

The Compose stack also includes a dedicated optional Directory service. Start it with
Redis and the API/admin stack through Make when you want a separate local service endpoint:

```bash
GOBLIN_DIRECTORY_ENABLED=true make admin-up
```

Use a project-scoped token for normal search calls and the bootstrap/admin token only
for review and publication actions. The Directory service uses the same SQLite database
and artifact volume as the rest of the local API, so `docker compose down --volumes`
removes Directory rows along with jobs, runs, and auth records.

## Helm Enablement

In Kubernetes, enable the Directory API through chart values so the rendered API
ConfigMap contains the same JSON settings:

```yaml
repository:
  enabled: true
  url: http://goblin-king-directory-api:8000
```

Render before applying:

```bash
helm template goblin-king charts/goblin-king \
  --set repository.enabled=true \
  --set repository.url=http://goblin-king-directory-api:8000
```

For a live upgrade, use the same values file you use for API/auth settings:

```bash
helm upgrade --install goblin-king charts/goblin-king -f values.yaml
```

Keep repository endpoints behind the same authenticated ingress policy as the API.
Review and publication actions must remain admin-only.

## JupyterHub Stack Enablement

JupyterHub-backed users can search the Directory when Hub auth is enabled and their Hub
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
  url: http://goblin-king-directory-api.default.svc.cluster.local:8000
  podLabels:
    hub.jupyter.org/network-access-hub: "true"
directoryUi:
  enabled: true
  podLabels:
    hub.jupyter.org/network-access-hub: "true"
  serviceTokenSecret:
    name: goblin-king-jupyterhub-auth
    key: directory-ui-token
  apiUrl: http://goblin-king-api.default.svc.cluster.local:8000
  repositoryUrl: http://goblin-king-directory-api.default.svc.cluster.local:8000
  hubApiUrl: http://hub.default.svc.cluster.local:8081/hub/api
  hubBaseUrl: /hub/
```

With that mapping:

- Users in `goblin-users` can search published entries for `default`.
- Users in `goblin-admins` can review and publish entries.
- Users outside allowed Hub groups receive the normal JupyterHub auth denial before any
  Directory lookup happens.

When zero-to-jupyterhub network policies are enabled, the Directory API and Directory
UI pods need `hub.jupyter.org/network-access-hub: "true"` so they can validate Hub user
tokens and complete OAuth token exchange against the Hub API.

The local stack proof should enable the Directory API in
`examples/jupyterhub-goblin-king/goblin-king.values.yaml` and then run:

```bash
make jupyterhub-stack-up JUPYTERHUB_STACK_REBUILD=1
```

After the stack is up, call the Directory API with a Hub user token from a notebook or
from the Hub service route. Do not use the Hub service token for user search flows; it is
only for the API to validate Hub user tokens.

For the full local proof, use the one-command target:

```bash
make jupyterhub-directory-proof
```

It brings up JupyterHub, Goblin King, and the optional Directory API; uses Hub user
tokens for `bob`, `alice`, `carol`, and an unauthorized `mallory`; proves submit,
validate, review, publish, discover, run, start, probe, proxy, stop; and tears the stack
down with a resource audit.

For the Hub browser UI proof, use:

```bash
make jupyterhub-directory-ui-proof
```

It brings up the directory UI service at `/services/goblin-directory/`, completes Hub
OAuth for the proof users, uploads v1 bundles through the UI backend, publishes them,
invokes the published goblins by name, and tears the stack down.
