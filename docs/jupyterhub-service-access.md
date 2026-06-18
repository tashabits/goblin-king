# JupyterHub Service Access

Goblin King can optionally use a JupyterHub in the same cluster as an auth provider for
registered long-running services, notebook-declared Python function goblins, and
notebook-declared ASGI services. This is additive: local bootstrap/API tokens and OIDC
continue to work, and JupyterHub validation only runs when `jupyterhub.enabled` is true.

Use this when JupyterHub already owns user identity and Goblin King owns service
workload registration, probe proof, and project-scoped access control.

## Runtime Shape

- JupyterHub authenticates users and issues user API tokens.
- JupyterHub registers Goblin King as an external service and gives it a service token.
- Goblin King validates incoming Hub user tokens against the Hub API.
- Goblin King maps Hub users/groups to local roles and project scopes.
- Users can declare a Python function in a workbook, validate it, and run it as a
  project-scoped goblin.
- Users can declare an ASGI service in a workbook, validate it, start a managed
  Docker container or Kubernetes Deployment/Service/ConfigMap, probe it, proxy to it,
  and stop it without writing a Dockerfile.
- Users access registered services through Goblin King's service proxy:

```text
/services/long-running/<service-id>/proxy/<path>
```

Service containers do not need to implement JupyterHub auth. Goblin King gates access
before proxying the request to the registered service base URL.

Notebook-defined goblins use the generic Python function runner image configured by
`notebook_function_image` / `config.notebookFunctionImage`. The workbook sends function
source to Goblin King, Goblin King stores a hashed bundle under the requested kind, and
the scheduler wraps user input with that bundle before invoking the runner image. The
normal worker validation gate still applies before execution.

Notebook-defined services use the ASGI runner image configured by
`notebook_service_image` / `config.notebookServiceImage`. The workbook sends ASGI source,
the target app symbol, inline pip requirements, port, and probe path. Goblin King starts
an isolated runner, installs those requirements inside that runner, imports the app,
probes it, and registers the managed runtime through the normal long-running service
gateway.

## Configure JupyterHub

In zero-to-jupyterhub, add Goblin King as an externally managed service and grant the
service enough scope to identify users and read group membership. If you enable the
browser-facing Goblin Directory app, register it as a second service with its own
OAuth client token and grant users `access:services!service=goblin-directory`. Use a
Kubernetes Secret for the shared service tokens.

```yaml
hub:
  extraEnv:
    GOBLIN_KING_JUPYTERHUB_SERVICE_TOKEN:
      valueFrom:
        secretKeyRef:
          name: goblin-king-jupyterhub-auth
          key: service-token
    GOBLIN_KING_WORKBOOK_USER_TOKEN:
      valueFrom:
        secretKeyRef:
          name: goblin-king-jupyterhub-auth
          key: workbook-user-token
    GOBLIN_KING_DIRECTORY_UI_SERVICE_TOKEN:
      valueFrom:
        secretKeyRef:
          name: goblin-king-jupyterhub-auth
          key: directory-ui-token
  extraConfig:
    00-goblin-king-service: |
      import os

      service_token = os.environ["GOBLIN_KING_JUPYTERHUB_SERVICE_TOKEN"]
      directory_ui_token = os.environ["GOBLIN_KING_DIRECTORY_UI_SERVICE_TOKEN"]
      workbook_user_token = os.environ["GOBLIN_KING_WORKBOOK_USER_TOKEN"]
      c.JupyterHub.api_tokens = {
          workbook_user_token: "alice",
      }
      c.JupyterHub.services = [
          {
              "name": "goblin-king",
              "url": "http://goblin-king-admin.default.svc.cluster.local:8080",
              "api_token": service_token,
          },
          {
              "name": "goblin-directory",
              "url": "http://goblin-king-directory-ui.default.svc.cluster.local:8080",
              "api_token": directory_ui_token,
              "display": True,
              "oauth_no_confirm": True,
              "oauth_redirect_uri": "/services/goblin-directory/oauth_callback",
          }
      ]
      c.JupyterHub.load_groups = {
          "goblin-users": ["alice", "bob", "carol"],
          "goblin-admins": ["alice"],
      }
      c.JupyterHub.load_roles = [
          {
              "name": "goblin-king-service-auth",
              "services": ["goblin-king"],
              "scopes": [
                  "read:users",
                  "read:users:name",
                  "read:users:groups",
              ],
          },
          {
              "name": "goblin-directory-service-auth",
              "services": ["goblin-directory"],
              "scopes": [
                  "read:users",
                  "read:users:name",
                  "read:users:groups",
              ],
          },
          {
              "name": "goblin-king-user-access",
              "groups": ["goblin-users", "goblin-admins"],
              "scopes": [
                  "access:services!service=goblin-king",
                  "access:services!service=goblin-directory",
              ],
          },
      ]
```

The `goblin-king` service URL should point to the admin service when users enter
through the Hub service route. The `goblin-directory` service URL should point to the
directory UI service, not the repository API; its backend exchanges Hub OAuth and calls
the repository API as the logged-in user.

If zero-to-jupyterhub network policies are enabled, every Goblin King pod that calls the
Hub API must be allowed by the Hub ingress policy. The bundled local values add the
standard label to the API, repository, and directory UI pods:

```yaml
api:
  podLabels:
    hub.jupyter.org/network-access-hub: "true"
repository:
  podLabels:
    hub.jupyter.org/network-access-hub: "true"
directoryUi:
  podLabels:
    hub.jupyter.org/network-access-hub: "true"
```

The `api_tokens` entry above is for the bundled local proof. In normal workbook use,
JupyterHub provides `JUPYTERHUB_API_TOKEN` inside the user's notebook server.

Official references:

- JupyterHub REST API tokens and services:
  <https://jupyterhub.readthedocs.io/en/stable/howto/rest.html>
- JupyterHub service authentication:
  <https://jupyterhub.readthedocs.io/en/stable/reference/api/services.auth.html>
- JupyterHub services:
  <https://jupyterhub.readthedocs.io/en/stable/reference/services.html>
- JupyterHub scopes:
  <https://jupyterhub.readthedocs.io/en/stable/rbac/scopes.html>

## Configure Goblin King

Configure the Hub API URL, the Hub service route details, and group mapping. Do not put
the service token in the ConfigMap; mount it through `service_token_env`.

```json
{
  "notebook_function_image": "registry.example/goblin-king-notebook-python-function:latest",
  "notebook_service_image": "registry.example/goblin-king-notebook-asgi-service:latest",
  "notebook_service_runtime": "auto",
  "jupyterhub": {
    "enabled": true,
    "api_url": "http://hub.default.svc.cluster.local:8081/hub/api",
    "hub_url": "http://proxy-public.default.svc.cluster.local",
    "service_name": "goblin-king",
    "service_prefix": "/services/goblin-king/",
    "public_url": "http://goblin-king.default.example",
    "service_token_env": "GOBLIN_KING_JUPYTERHUB_SERVICE_TOKEN",
    "allowed_groups": ["goblin-users", "goblin-admins"],
    "admin_groups": ["goblin-admins"],
    "project_groups": {
      "goblin-users": "default"
    },
    "default_project_id": "default",
    "cache_ttl_seconds": 60
  }
}
```

The Helm chart exposes the same settings under `config.jupyterhub.*` and reads the
service token from a Secret:

```yaml
config:
  notebookFunctionImage: registry.example/goblin-king-notebook-python-function:latest
  notebookServiceImage: registry.example/goblin-king-notebook-asgi-service:latest
  notebookServiceRuntime: auto
  jupyterhub:
    enabled: true
    apiUrl: http://hub.default.svc.cluster.local:8081/hub/api
    hubUrl: http://proxy-public.default.svc.cluster.local
    serviceName: goblin-king
    servicePrefix: /services/goblin-king/
    serviceTokenSecret:
      name: goblin-king-jupyterhub-auth
      key: service-token
    allowedGroups:
      - goblin-users
    adminGroups:
      - goblin-admins
    projectGroups:
      goblin-users: default
```

The default zero-to-jupyterhub network policy only admits pods labeled
`hub.jupyter.org/network-access-hub: "true"` to the Hub API. Add that label to each
Goblin King pod that validates Hub tokens or exchanges Hub OAuth codes:

```yaml
api:
  podLabels:
    hub.jupyter.org/network-access-hub: "true"
repository:
  podLabels:
    hub.jupyter.org/network-access-hub: "true"
directoryUi:
  podLabels:
    hub.jupyter.org/network-access-hub: "true"
```

Notebook servers also need egress to the Goblin King API service so workbook users can
declare, validate, and run goblins from inside JupyterHub. The included
`zero-to-jupyterhub.values.yaml` adds a single-user network policy egress rule for
Goblin King API pods on port `8000`.

## Local Kubernetes Default Hub

For local Kubernetes proof, Goblin King includes a default editable stack config at
`examples/jupyterhub-goblin-king/local-stack.mk`. It points to:

- `examples/jupyterhub-goblin-king/zero-to-jupyterhub.values.yaml`
- `examples/jupyterhub-goblin-king/goblin-king.values.yaml`
- `examples/jupyterhub-goblin-king/workbook-launch.ipynb`
- `examples/jupyterhub-goblin-king/workbook-launch-branch.ipynb`

The default zero-to-jupyterhub values use JupyterHub's dummy authenticator, register
Goblin King admin and Goblin Directory browser services, create a starter
`goblin-users` group containing `alice`, `bob`, and `carol`, make `alice` a
`goblin-admins` member, leave `mallory` unauthorized for negative proof, and set
`GOBLIN_KING_API_URL` plus `GOBLIN_KING_NOTEBOOK_PACKAGE` in notebook
servers. The local make target injects a branch-aware
`GOBLIN_KING_NOTEBOOK_PACKAGE` such as
`git+https://github.com/tashabits/goblin-king.git@develop`; override
`JUPYTERHUB_NOTEBOOK_PACKAGE` when you want notebook servers to install a
different branch or package artifact. Directory workbooks always force-reinstall
that configured package with `--no-deps` and clear cached imports before loading
the helper, so uploaded notebooks match the running stack instead of a stale user
site package.

Install the default Hub and Goblin King together:

```bash
make jupyterhub-stack-up
```

When you are testing local branch changes, force a fresh image build and deploy those
exact tags:

```bash
make jupyterhub-stack-up JUPYTERHUB_STACK_REBUILD=1
```

That rebuild path creates unique local tags for the API, admin UI, notebook function
runner, notebook ASGI runner, and example long service, loads them into kind or Docker
Desktop Kubernetes when needed, and appends the matching Helm image settings. Set
`JUPYTERHUB_STACK_BUILD_NO_CACHE=0` if you want Docker to reuse cached layers.

Run the end-to-end workbook proof with one command:

```bash
make jupyterhub-workbook-proof
```

That proof target:

- installs the same editable local Hub plus Goblin King stack
- uses the local-only Hub user token for `alice`, not the bootstrap admin token
- declares a short Python function goblin from proof-local workbook-style source
- validates that function through the configured notebook runner image
- runs the custom kind and waits for the result
- declares a FastAPI ASGI service from proof-local workbook-style source
- validates the service through the configured ASGI runner image
- starts the managed Kubernetes Deployment/Service/ConfigMap, probes it, proxies it,
  stops it, and confirms cleanup
- tears down the Hub plus Goblin King stack in a cleanup step

Run the end-to-end repository proof with one command:

```bash
make jupyterhub-repository-proof
```

That proof target rebuilds local images, installs JupyterHub plus Goblin King with the
optional repository service enabled, validates Hub tokens for `bob`, `alice`, and
`carol`, confirms `mallory` is denied, and runs the repository workflow:

- `bob` submits a notebook-defined function and ASGI service, validates both, and
  requests review
- `alice` approves and publishes both entries
- `carol` searches published entries, runs the function by repository name, starts the
  service by repository name, probes/proxies it, and stops it
- teardown confirms no Goblin King stack resources, JupyterHub resources,
  notebook-service Kubernetes resources, or notebook-service Docker containers remain

Run the end-to-end directory browser-service proof with one command:

```bash
make jupyterhub-directory-ui-proof
```

That proof target installs Hub, Goblin King, the optional repository service, and the
separate directory UI service. It opens the UI through the Hub proxy route
`/services/goblin-directory/`, completes Hub OAuth for `bob`, `alice`, and `carol`,
confirms `mallory` is denied, has `bob` upload v1 zip bundles for a function and ASGI
service, has `alice` approve and publish them, has `carol` discover and invoke both by
directory name, then tears down the stack and audits Kubernetes and Docker resources.

That target:

- creates a `goblin-king-jupyterhub-auth` Secret
- installs zero-to-jupyterhub with the default service config
- installs Goblin King with `config.jupyterhub.enabled=true`
- points Goblin King at the in-cluster Hub API
- mounts the Hub service token into the Goblin King API pod
- configures the notebook Python runner image through `config.notebookFunctionImage`
- configures the notebook ASGI service runner image through `config.notebookServiceImage`

Edit `examples/jupyterhub-goblin-king/local-stack.mk` when you want to change the
namespace, releases, token Secret, or values files. Edit the two values files for Hub
and Goblin King behavior. The target is deliberately just Make plus Helm so local
clusters, kind, k3d, minikube, and real Kubernetes clusters can use the same shape.

To bring the browser UI up for manual testing, enable both optional services:

```bash
make jupyterhub-stack-up \
  JUPYTERHUB_STACK_REBUILD=1 \
  GOBLIN_REPOSITORY_ENABLED=1 \
  GOBLIN_DIRECTORY_UI_ENABLED=1
```

Then port-forward the Hub proxy and open both service routes:

```bash
kubectl port-forward -n default svc/proxy-public 8080:http
```

- `http://127.0.0.1:8080/services/goblin-king/` opens the existing admin UI.
- `http://127.0.0.1:8080/services/goblin-directory/` opens the directory UI with
  Hub OAuth.

To install or remove only the local Hub:

```bash
make jupyterhub-up
make jupyterhub-down
```

`make jupyterhub-down` also deletes Hub single-user server pods for the configured
release, so a local proof does not leave a user's notebook server behind after Helm
uninstall.

The default service and workbook user tokens are intentionally local-only. Override them
for any shared cluster:

```bash
make jupyterhub-stack-up \
  JUPYTERHUB_SERVICE_TOKEN="$(openssl rand -hex 32)" \
  JUPYTERHUB_WORKBOOK_USER_TOKEN="$(openssl rand -hex 32)"
```

The lower-level flag still works if you do not want the stack config:

```bash
make helm-up HELM_WITH_JUPYTERHUB=1
```

## User Flows

Browser flow:

1. User logs in to JupyterHub.
2. User opens the Goblin King service route, such as `/services/goblin-king/`, and
   sees the admin UI token login through the Hub proxy.
3. User opens `examples/jupyterhub-goblin-king/workbook-launch.ipynb` in a notebook.
   Before the branch merges, use `workbook-launch-branch.ipynb` instead.
4. The workbook reads `JUPYTERHUB_API_TOKEN` and `GOBLIN_KING_API_URL`.
5. Goblin King validates the token with the Hub and applies group/project mapping.
6. The workbook declares a Python function goblin, validates it, runs it, declares an
   ASGI service from source, validates/starts/probes/proxies it, then stops it.

Directory workbook flow:

1. Enable the optional repository service for the stack:

   ```bash
   make jupyterhub-stack-up GOBLIN_REPOSITORY_ENABLED=1
   ```

2. A contributor such as `bob` opens
   `examples/jupyterhub-goblin-king/workbook-directory-submit.ipynb`.
3. The submitter workbook defines a short function and an ASGI service in notebook
   source, submits both with `client.submit_repository_function()` and
   `client.submit_repository_service()`, validates both, and requests review.
4. An admin such as `alice` opens
   `examples/jupyterhub-goblin-king/workbook-directory-admin.ipynb`, lists
   `pending_review` entries, approves them, and publishes them.
5. A consumer such as `carol` opens
   `examples/jupyterhub-goblin-king/workbook-directory-consume.ipynb`, searches
   published entries, runs the approved function by repository name, starts the
   approved ASGI service by repository name, probes/proxies it, and stops it.

When repository routes are not enabled or the notebook server points at the wrong
endpoint, the notebook helper error mentions `repository.enabled=true` and
`GOBLIN_KING_REPOSITORY_URL`.

Directory browser flow:

1. Enable both optional services:

   ```bash
   make jupyterhub-stack-up \
     GOBLIN_REPOSITORY_ENABLED=1 \
     GOBLIN_DIRECTORY_UI_ENABLED=1
   ```

2. The user logs in to JupyterHub and opens `/services/goblin-directory/`.
3. The directory UI completes Hub OAuth; no token is pasted into the browser.
4. A contributor uploads a `.zip` bundle with `goblin-directory.json` and one Python
   entrypoint source file.
5. The UI previews the manifest, source, requirements, and extra files. Extra files are
   visible for review but are not executed in upload bundle schema v1.
6. The owner submits the draft, validates it, and requests review.
7. An admin approves and publishes from the Review Queue.
8. Authorized users search the Directory and run function goblins or start/probe/proxy
   and stop service goblins by repository name.

Notebook function flow:

```python
from goblin_king.notebooks import GoblinKingNotebookClient

client = GoblinKingNotebookClient()

def summarize(payload):
    values = payload["values"]
    return {
        "count": len(values),
        "total": sum(values),
    }

goblin = client.declare(
    summarize,
    kind="notebook.summarize",
    display_name="Notebook Summarize",
    timeout_seconds=30,
)

validation = goblin.validate({"values": [1, 2, 3]})
run = goblin.run({"values": [5, 8, 13]}, progress=True)
run["run"]["result"]
```

`declare` stores the source bundle and records the runnable kind. `validate` runs the
bundle through the configured Python runner image and saves validation proof. `run`
submits the custom kind through the normal jobs API and waits for the run result.

If your function needs imports, put them inside the function or pass explicit `source=`
when declaring the goblin so the bundle is self-contained.

Notebook ASGI service flow:

```python
from goblin_king.notebooks import GoblinKingNotebookClient

client = GoblinKingNotebookClient()

SERVICE_SOURCE = """
from fastapi import FastAPI

app = FastAPI()

@app.get("/hello")
def hello():
    return {"message": "Hello World"}
""".strip()

service = client.declare_service(
    source=SERVICE_SOURCE,
    kind="notebook.long-hello",
    app_name="app",
    requirements=["fastapi>=0.115,<1"],
    probe_path="/hello",
    project_id="default",
)

service.validate()
start = service.start(progress=True)
probe = service.probe()
proxied = service.proxy("/hello")
stopped = service.stop()
```

`declare_service` stores the ASGI bundle and requirements under the requested kind.
`validate` starts an isolated runner and probes it. `start` creates managed runtime
resources and registers a service for gated proxy access. `stop` removes the managed
runtime resources and marks the service stopped.

API flow:

```bash
curl -H "Authorization: Bearer <jupyterhub-user-token>" \
  http://goblin-king.default.example/services/long-running/<service-id>/proxy/healthz
```

Registered-service flow:

```bash
curl -X POST http://goblin-king.default.example/services/long-running \
  -H "Authorization: Bearer <admin-or-project-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "kind": "example.long-hello",
    "base_url": "http://goblin-king-long-hello",
    "probe_path": "/hello",
    "project_id": "default"
  }'
```

## Troubleshooting

| Symptom | Likely cause | Repair |
| --- | --- | --- |
| `missing or invalid JupyterHub bearer token` | User token is expired, wrong Hub, or hidden by another auth layer. | Create a fresh Hub token and send it as `Authorization: Bearer ...`. |
| `JupyterHub user is not authorized` | User is not in `allowed_users` or `allowed_groups`. | Add the user/group to Goblin King config and reload the API. |
| `JupyterHub service token is required` | Secret was not mounted into the API pod. | Check `config.jupyterhub.serviceTokenSecret` and the API pod env. |
| Directory UI redirects to Hub and then returns `invalid OAuth state` | Browser cookies were blocked or the service prefix changed while logging in. | Reload `/services/goblin-directory/`, keep cookies enabled for the Hub origin, and verify `directoryUi.servicePrefix`. |
| Directory UI returns `directory UI service token is required` | The UI service token Secret was not mounted into the directory UI pod. | Check `directoryUi.serviceTokenSecret` and the `directory-ui-token` key in `goblin-king-jupyterhub-auth`. |
| `/services/goblin-directory/` is missing from the Hub services menu | The Hub service was not registered or the user lacks the service access scope. | Add the `goblin-directory` service and grant `access:services!service=goblin-directory` to the Hub group. |
| Bundle preview says `bundle path is not safe` | The zip contains an absolute path or `..` traversal segment. | Recreate the zip with relative paths rooted at the bundle directory. |
| Bundle preview says `entrypoint must be UTF-8 text` | The configured entrypoint is binary or encoded differently. | Use one UTF-8 Python source file for upload bundle schema v1. |
| `JupyterHub is unavailable for token validation` | Hub API URL or cluster DNS is wrong. | Confirm `config.jupyterhub.apiUrl` from inside the API pod. |
| Project access denied | Hub group did not map to the service project. | Add the group to `projectGroups` or set `defaultProjectId`. |
| `ModuleNotFoundError: goblin_king` in a notebook | The single-user image does not include the helper package and cannot install it. | Set `GOBLIN_KING_NOTEBOOK_PACKAGE`, preinstall the package in the notebook image, or use the workbook install cell. |
| `GoblinKingNotebookClient.__init__()` rejects `repository_url` | The notebook installed an older helper package than the running stack expects. | Rebuild the stack with `JUPYTERHUB_NOTEBOOK_PACKAGE=git+https://github.com/tashabits/goblin-king.git@develop`, or upload the latest repository workbook and rerun its first cell. |
| Notebook install/import cell stays at `[*]` for a long time | The notebook is installing from GitHub in the running kernel. | Use `workbook-launch-branch.ipynb`, which installs quietly with `--no-deps`; restart the kernel once after a failed install/import attempt. |
| `kubectl port-forward` reports `lost connection to pod` while browsing JupyterHub | The local tunnel to the Hub proxy was reset or the proxy pod restarted. | Start the port-forward again with `kubectl port-forward -n default svc/proxy-public 8080:http`, then refresh JupyterLab. |
| Notebook validation says runner image is unavailable | `config.notebookFunctionImage` is not present on the scheduler node or registry. | Build/push/load `goblin-king-notebook-python-function:local` or point `config.notebookFunctionImage` to a pullable image. |
| Notebook service start says runner image is unavailable | `config.notebookServiceImage` is not present on the node or registry. | Build/push/load `goblin-king-notebook-asgi-service:local` or point `config.notebookServiceImage` to a pullable image. |
| Notebook service validation fails during pip install | Inline requirements are invalid or unavailable from the runner network. | Fix the `requirements=[...]` list, use versions available to the runner, and rerun `service.validate()`. |
| Notebook service validation says the app symbol is missing or not callable | `app_name` does not match an ASGI callable in the source. | Export the app object and set `app_name`, for example `app = FastAPI()` with `app_name="app"`. |
| Notebook service probe fails | The app started but `probe_path` did not return a 2xx response. | Fix the route or set `probe_path` to a healthy endpoint, then rerun validate/start. |
| Notebook service resources remain after an interrupted workbook | The stop cell did not run or the API pod was interrupted mid-cleanup. | Run `client.stop_service("<kind>")`, or delete Kubernetes resources labeled `goblin-king.io/notebook-service=true`. |
| Service proxy returns 502 | Service base URL is unreachable from the API pod. | Probe the service URL from the API pod and fix Service/DNS/port config. |
