PYTHON ?= python3
DB ?= .goblin-king/goblin-king.sqlite3
REGISTRY ?= demo-goblins.json
IMAGES ?= demo-images.json
INPUT ?= examples/input.json
CROSS_LANGUAGE_REGISTRY ?= examples/cross-language-goblins.json
CROSS_LANGUAGE_IMAGES ?= examples/cross-language-images.json
CROSS_LANGUAGE_INPUT ?= examples/cross-language-input.json
BEHAVIOR_REGISTRY ?= examples/behavior-goblins.json
BEHAVIOR_IMAGES ?= examples/behavior-images.json
BEHAVIOR_INPUT ?= examples/behavior-input.json
REDIS_URL ?= redis://localhost:6379/0
LONG_HELLO_URL ?= http://long-hello:8080
HOST_PROJECT ?= examples/adopting-project
PROJECT ?= $(HOST_PROJECT)/goblin-king-project.json
PROJECT_IMAGES ?= $(HOST_PROJECT)/goblin-images.json
ADMIN_BASE ?= http://127.0.0.1:8080
ADMIN_TOKEN ?= local-dev-token
DIST ?= dist
HELM_RELEASE ?= goblin-king
HELM_CHART ?= charts/goblin-king
HELM_NAMESPACE ?= default
HELM_TIMEOUT ?= 5m
HELM_ARGS ?=
HELM_EXTRA_ARGS ?=
HELM_PVC ?= $(HELM_RELEASE)-data
HELM_WITH_JUPYTERHUB ?= 0
GOBLIN_REPOSITORY_ENABLED ?= 0
GOBLIN_REPOSITORY_UI_ENABLED ?= 0
GOBLIN_REPOSITORY_LOCAL_URL ?= http://127.0.0.1:8010
JUPYTERHUB_REPOSITORY_URL ?= http://$(HELM_RELEASE)-repository.$(HELM_NAMESPACE).svc.cluster.local:8000
JUPYTERHUB_REPOSITORY_UI_URL ?= http://$(HELM_RELEASE)-repository-ui.$(HELM_NAMESPACE).svc.cluster.local:8080
JUPYTERHUB_REPOSITORY_UI_SERVICE_TOKEN_KEY ?= repository-ui-token
JUPYTERHUB_REPOSITORY_UI_SERVICE_TOKEN ?= local-goblin-king-repository-ui-token
HELM_REPOSITORY_ARGS ?= --set repository.enabled=true --set repository.url=$(JUPYTERHUB_REPOSITORY_URL)
HELM_REPOSITORY_UI_ARGS ?= --set repositoryUi.enabled=true --set repositoryUi.image.tag=$(JUPYTERHUB_STACK_IMAGE_TAG) --set repositoryUi.image.pullPolicy=Never --set repositoryUi.serviceTokenSecret.name=$(JUPYTERHUB_SERVICE_TOKEN_SECRET) --set repositoryUi.serviceTokenSecret.key=$(JUPYTERHUB_REPOSITORY_UI_SERVICE_TOKEN_KEY) --set repositoryUi.apiUrl=http://$(HELM_RELEASE)-api.$(HELM_NAMESPACE).svc.cluster.local:8000 --set repositoryUi.repositoryUrl=$(JUPYTERHUB_REPOSITORY_URL) --set repositoryUi.hubApiUrl=http://hub.$(HELM_NAMESPACE).svc.cluster.local:8081/hub/api --set repositoryUi.hubBaseUrl=/hub/
JUPYTERHUB_REPOSITORY_ARGS ?= --set singleuser.extraEnv.GOBLIN_KING_REPOSITORY_URL=$(JUPYTERHUB_REPOSITORY_URL)
JUPYTERHUB_REPOSITORY_UI_ARGS ?= --set hub.extraEnv.GOBLIN_KING_REPOSITORY_UI_SERVICE_TOKEN.valueFrom.secretKeyRef.name=$(JUPYTERHUB_SERVICE_TOKEN_SECRET) --set hub.extraEnv.GOBLIN_KING_REPOSITORY_UI_SERVICE_TOKEN.valueFrom.secretKeyRef.key=$(JUPYTERHUB_REPOSITORY_UI_SERVICE_TOKEN_KEY)
JUPYTERHUB_EXTRA_ARGS ?=
JUPYTERHUB_STACK_CONFIG ?= examples/jupyterhub-goblin-king/local-stack.mk
JUPYTERHUB_RELEASE ?= jupyterhub
JUPYTERHUB_CHART ?= jupyterhub/jupyterhub
JUPYTERHUB_VALUES ?= examples/jupyterhub-goblin-king/zero-to-jupyterhub.values.yaml
JUPYTERHUB_SERVICE_TOKEN_SECRET ?= goblin-king-jupyterhub-auth
JUPYTERHUB_SERVICE_TOKEN_KEY ?= service-token
JUPYTERHUB_SERVICE_TOKEN ?= local-goblin-king-hub-token
JUPYTERHUB_NOTEBOOK_PACKAGE_BRANCH ?= $(shell git branch --show-current)
JUPYTERHUB_NOTEBOOK_PACKAGE ?= $(if $(JUPYTERHUB_NOTEBOOK_PACKAGE_BRANCH),git+https://github.com/tashabits/goblin-king.git@$(JUPYTERHUB_NOTEBOOK_PACKAGE_BRANCH),git+https://github.com/tashabits/goblin-king.git)
JUPYTERHUB_NOTEBOOK_PACKAGE_ARGS ?= --set-string singleuser.extraEnv.GOBLIN_KING_NOTEBOOK_PACKAGE=$(JUPYTERHUB_NOTEBOOK_PACKAGE)
JUPYTERHUB_WORKBOOK_USER_TOKEN_KEY ?= workbook-user-token
JUPYTERHUB_WORKBOOK_USER_TOKEN ?= local-goblin-king-workbook-token
JUPYTERHUB_WORKBOOK_ALICE_TOKEN ?= local-goblin-king-alice-token
JUPYTERHUB_WORKBOOK_BOB_TOKEN ?= local-goblin-king-bob-token
JUPYTERHUB_WORKBOOK_CAROL_TOKEN ?= local-goblin-king-carol-token
JUPYTERHUB_WORKBOOK_MALLORY_TOKEN ?= local-goblin-king-mallory-token
JUPYTERHUB_WORKBOOK_PROOF ?= examples/jupyterhub-goblin-king/workbook_proof.py
JUPYTERHUB_FULL_STACK_PROOF ?= examples/jupyterhub-goblin-king/full_stack_workbook_proof.py
JUPYTERHUB_REPOSITORY_FULL_STACK_PROOF ?= examples/jupyterhub-goblin-king/full_stack_repository_proof.py
JUPYTERHUB_REPOSITORY_UI_PROOF ?= examples/jupyterhub-goblin-king/repository_ui_proof.py
JUPYTERHUB_STACK_IMAGE_PREPARE ?= examples/jupyterhub-goblin-king/prepare_stack_images.py
JUPYTERHUB_STACK_REBUILD ?= 0
JUPYTERHUB_STACK_BUILD_NO_CACHE ?= 1
JUPYTERHUB_STACK_IMAGE_TAG ?=
ifeq ($(JUPYTERHUB_STACK_IMAGE_TAG),)
JUPYTERHUB_STACK_IMAGE_TAG := jupyterhub-stack-$(shell $(PYTHON) -c "import time; print(int(time.time()))")
endif
JUPYTERHUB_KIND_CLUSTER ?= kind
NOTEBOOK_SERVICE_DOCKER_PROOF ?= examples/jupyterhub-goblin-king/docker_notebook_service_proof.py
JUPYTERHUB_WORKBOOK_API_URL ?= http://127.0.0.1:18000
JUPYTERHUB_WORKBOOK_KIND ?= notebook.workbook-short-hello
HELM_JUPYTERHUB_ARGS ?= --set config.jupyterhub.enabled=true --set config.jupyterhub.apiUrl=http://hub.$(HELM_NAMESPACE).svc.cluster.local:8081/hub/api --set config.jupyterhub.hubUrl=http://proxy-public.$(HELM_NAMESPACE).svc.cluster.local --set config.jupyterhub.serviceTokenSecret.name=$(JUPYTERHUB_SERVICE_TOKEN_SECRET) --set config.jupyterhub.serviceTokenSecret.key=$(JUPYTERHUB_SERVICE_TOKEN_KEY) --set config.jupyterhub.allowedGroups[0]=goblin-users --set config.jupyterhub.projectGroups.goblin-users=default
JUPYTERHUB_STACK_REBUILD_HELM_ARGS ?= --set image.tag=$(JUPYTERHUB_STACK_IMAGE_TAG) --set image.pullPolicy=Never --set admin.image.tag=$(JUPYTERHUB_STACK_IMAGE_TAG) --set admin.image.pullPolicy=Never --set workers.exampleLongHello.image=goblin-king-example-long-hello:$(JUPYTERHUB_STACK_IMAGE_TAG) --set workers.exampleLongHello.pullPolicy=Never --set config.notebookFunctionImage=goblin-king-notebook-python-function:$(JUPYTERHUB_STACK_IMAGE_TAG) --set config.notebookServiceImage=goblin-king-notebook-asgi-service:$(JUPYTERHUB_STACK_IMAGE_TAG)
COMPOSE_REPOSITORY_PROFILE = $(if $(filter 1 true yes,$(GOBLIN_REPOSITORY_ENABLED)),--profile repository,)
COMPOSE_REPOSITORY_SERVICE = $(if $(filter 1 true yes,$(GOBLIN_REPOSITORY_ENABLED)),repository,)
NOTEBOOK_REPOSITORY_PROOF_ARGS = $(if $(filter 1 true yes,$(GOBLIN_REPOSITORY_ENABLED)),--repository-url $(GOBLIN_REPOSITORY_LOCAL_URL),)
HELM_OPTIONAL_REPOSITORY_ARGS = $(if $(filter 1 true yes,$(GOBLIN_REPOSITORY_ENABLED)),$(HELM_REPOSITORY_ARGS),)
JUPYTERHUB_OPTIONAL_REPOSITORY_ARGS = $(if $(filter 1 true yes,$(GOBLIN_REPOSITORY_ENABLED)),$(JUPYTERHUB_REPOSITORY_ARGS),)
HELM_OPTIONAL_REPOSITORY_UI_ARGS = $(if $(filter 1 true yes,$(GOBLIN_REPOSITORY_UI_ENABLED)),$(HELM_REPOSITORY_UI_ARGS),)
JUPYTERHUB_OPTIONAL_REPOSITORY_UI_ARGS = $(if $(filter 1 true yes,$(GOBLIN_REPOSITORY_UI_ENABLED)),$(JUPYTERHUB_REPOSITORY_UI_ARGS),)

.PHONY: help install test lint local-ci build-workers build-cross-language-workers run-cross-language-proof validate-cross-language-workers build-behavior-workers run-behavior-proof validate-behavior-workers admin-build redis-up redis-down deploy docker-up docker-wipe docker-restart-clean notebook-service-docker-proof jupyterhub-stack-up jupyterhub-stack-down jupyterhub-up jupyterhub-down jupyterhub-workbook-proof jupyterhub-repository-proof jupyterhub-repository-ui-proof helm-up helm-wipe helm-restart-clean stack-wipe stack-restart-clean run-once schedule simulate events-smoke api api-smoke admin-up long-hello-up long-hello-down admin-smoke admin-runtime-audit doctor demo demo-down project-validate project-build-workers project-discovery-reload project-admin-proof adopter-smoke release-wheel release-check helm-template helm-admin-smoke kind-smoke clean clean-all docker-clean

help:
	@echo "Targets:"
	@echo "  install        Install the project in editable mode"
	@echo "  test           Run pytest locally"
	@echo "  lint           Run ruff locally"
	@echo "  local-ci       Run local CI checks"
	@echo "  build-workers  Build configured demo Docker worker images"
	@echo "  build-cross-language-workers Build cross-language example worker images"
	@echo "  run-cross-language-proof Run every cross-language example through Docker runtime"
	@echo "  validate-cross-language-workers Validate cross-language workers against contract"
	@echo "  build-behavior-workers Build behavior example worker images"
	@echo "  run-behavior-proof Run behavior examples through Docker runtime"
	@echo "  validate-behavior-workers Validate behavior workers against contract"
	@echo "  admin-build    Build the React admin image"
	@echo "  redis-up       Start Redis with Docker Compose"
	@echo "  redis-down     Stop Redis"
	@echo "  deploy         Build workers and start Redis"
	@echo "  docker-up      Build and start the full Docker Compose stack"
	@echo "  docker-wipe    Stop Docker Compose and delete its volumes/data"
	@echo "  docker-restart-clean Wipe Docker data, then rebuild and start Compose"
	@echo "  notebook-service-docker-proof Prove notebook ASGI service lifecycle with Docker"
	@echo "  helm-up        Install/upgrade the Helm stack and wait for readiness"
	@echo "  jupyterhub-stack-up Install local Kubernetes stack with JupyterHub auth"
	@echo "                 Set JUPYTERHUB_STACK_REBUILD=1 to build fresh local images first"
	@echo "                 Set GOBLIN_REPOSITORY_ENABLED=1 to add the optional repository service"
	@echo "                 Set GOBLIN_REPOSITORY_UI_ENABLED=1 to add the Hub repository UI service"
	@echo "  jupyterhub-stack-down Remove local Kubernetes stack with JupyterHub auth"
	@echo "  jupyterhub-up  Install default zero-to-jupyterhub for local auth proof"
	@echo "  jupyterhub-down Uninstall the default local JupyterHub release"
	@echo "  jupyterhub-workbook-proof Run full Hub workbook declare/validate/run/service proof"
	@echo "  jupyterhub-repository-proof Run full Hub repository submit/review/consume proof"
	@echo "  jupyterhub-repository-ui-proof Run full Hub repository browser-service proof"
	@echo "  helm-wipe      Uninstall Helm release and delete its PVC/data"
	@echo "  helm-restart-clean Wipe Helm data, then install/upgrade and wait"
	@echo "  stack-wipe     Wipe both Docker Compose and Helm data"
	@echo "  stack-restart-clean Wipe and restart both Docker Compose and Helm"
	@echo "  schedule       Add a due example.echo schedule"
	@echo "  run-once       Run one Docker scheduler pass"
	@echo "  simulate       Deploy, schedule, run once, and list jobs"
	@echo "  events-smoke   Print recent events and heartbeats after simulation"
	@echo "  api            Run the FastAPI control plane"
	@echo "  api-smoke      Exercise local API health, auth, and queued jobs"
	@echo "  long-hello-up  Start the long-running hello service with Compose"
	@echo "  admin-up       Start the React admin, API, Redis, and long service"
	@echo "  admin-smoke    Exercise Docker React admin API proof flow"
	@echo "  admin-runtime-audit Collect Docker admin runtime audit table"
	@echo "  doctor         Diagnose local demo/adopter prerequisites"
	@echo "  demo           Start local demo stack and prove a validated admin-visible run"
	@echo "  demo-down      Stop the local demo stack"
	@echo "  project-validate Validate host-project discovery settings"
	@echo "  project-build-workers Build host-project worker images"
	@echo "  project-discovery-reload Reload host-project discovery through admin API"
	@echo "  project-admin-proof Prove host-project goblins are visible through admin API"
	@echo "  adopter-smoke  Generate, validate, schedule, run, inspect, and clean adopter goblins"
	@echo "  release-wheel  Build the internal wheel into DIST"
	@echo "  release-check  Run local release/adoption proof commands"
	@echo "  helm-template  Render the optional Helm chart"
	@echo "  helm-admin-smoke Exercise Helm React admin through goblin-king.local"
	@echo "  kind-smoke     Render Helm and report whether kind is available"
	@echo "  clean          Remove local Goblin King runtime state"
	@echo "  clean-all      Remove ignored untracked files/directories with git clean -fdX"
	@echo "  docker-clean   Stop Compose services and remove volumes"

install:
	$(PYTHON) -m pip install -e .

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check .

local-ci: test lint

build-workers:
	$(PYTHON) -m goblin_king.cli workers build --images $(IMAGES)

build-cross-language-workers:
	$(PYTHON) -m goblin_king.cli workers build --images $(CROSS_LANGUAGE_IMAGES)

run-cross-language-proof: build-cross-language-workers redis-up
	$(PYTHON) -c "import json, subprocess, tempfile; from pathlib import Path; kinds=['example.hello-dotnet','example.hello-go','example.hello-java','example.hello-node','example.hello-php','example.hello-python','example.hello-ruby','example.hello-rust','example.hello-shell','example.wasi-c-hello','example.wasi-rust-hello']; db=Path(tempfile.mkdtemp())/'cross-language.sqlite3';\nfor kind in kinds:\n    completed=subprocess.run(['$(PYTHON)','-m','goblin_king.cli','jobs','submit',kind,'--input','$(CROSS_LANGUAGE_INPUT)','--registry','$(CROSS_LANGUAGE_REGISTRY)','--images','$(CROSS_LANGUAGE_IMAGES)','--db',str(db),'--redis-url','$(REDIS_URL)'], check=True, capture_output=True, text=True); print(completed.stdout.strip().splitlines()[-1])"

validate-cross-language-workers: redis-up
	$(PYTHON) -m goblin_king.cli workers validate --registry $(CROSS_LANGUAGE_REGISTRY) --images $(CROSS_LANGUAGE_IMAGES) --input $(CROSS_LANGUAGE_INPUT) --build --require-success --redis-url $(REDIS_URL)

build-behavior-workers:
	$(PYTHON) -m goblin_king.cli workers build --images $(BEHAVIOR_IMAGES)

run-behavior-proof: build-behavior-workers redis-up
	$(PYTHON) -m goblin_king.cli jobs submit example.behavior-node-artifact --input $(BEHAVIOR_INPUT) --registry $(BEHAVIOR_REGISTRY) --images $(BEHAVIOR_IMAGES) --redis-url $(REDIS_URL)
	$(PYTHON) -m goblin_king.cli jobs submit example.behavior-python-progress --input $(BEHAVIOR_INPUT) --registry $(BEHAVIOR_REGISTRY) --images $(BEHAVIOR_IMAGES) --redis-url $(REDIS_URL)
	$(PYTHON) -m goblin_king.cli jobs submit example.behavior-python-slow-cancellable --input $(BEHAVIOR_INPUT) --registry $(BEHAVIOR_REGISTRY) --images $(BEHAVIOR_IMAGES) --redis-url $(REDIS_URL)
	$(PYTHON) -m goblin_king.cli jobs submit example.behavior-go-transform --input $(BEHAVIOR_INPUT) --registry $(BEHAVIOR_REGISTRY) --images $(BEHAVIOR_IMAGES) --redis-url $(REDIS_URL)
	-$(PYTHON) -m goblin_king.cli jobs submit example.behavior-shell-failure --input $(BEHAVIOR_INPUT) --registry $(BEHAVIOR_REGISTRY) --images $(BEHAVIOR_IMAGES) --redis-url $(REDIS_URL)
	$(PYTHON) -m goblin_king.cli jobs submit example.behavior-wasi-c-context --input $(BEHAVIOR_INPUT) --registry $(BEHAVIOR_REGISTRY) --images $(BEHAVIOR_IMAGES) --redis-url $(REDIS_URL)

validate-behavior-workers: redis-up
	$(PYTHON) -m goblin_king.cli workers validate --registry $(BEHAVIOR_REGISTRY) --images $(BEHAVIOR_IMAGES) --input $(BEHAVIOR_INPUT) --build --redis-url $(REDIS_URL)

admin-build:
	docker build -t goblin-king-admin-ui:local admin-ui

redis-up:
	docker compose up -d redis

redis-down:
	docker compose stop redis

deploy: build-workers redis-up

admin-up:
	docker compose --profile api --profile admin $(COMPOSE_REPOSITORY_PROFILE) up -d redis api admin long-hello $(COMPOSE_REPOSITORY_SERVICE)

docker-up: admin-build build-workers
	docker compose --profile api --profile admin --profile scheduler $(COMPOSE_REPOSITORY_PROFILE) up -d --build redis api admin long-hello scheduler $(COMPOSE_REPOSITORY_SERVICE)

docker-wipe:
	docker compose down --volumes --remove-orphans

docker-restart-clean: docker-wipe docker-up

notebook-service-docker-proof:
	docker build -t goblin-king-notebook-asgi-service:local workers/notebook.asgi-service
	docker compose --profile api $(COMPOSE_REPOSITORY_PROFILE) up -d --build redis api $(COMPOSE_REPOSITORY_SERVICE)
	$(PYTHON) $(NOTEBOOK_SERVICE_DOCKER_PROOF) --api-url http://127.0.0.1:8000 --token local-dev-token $(NOTEBOOK_REPOSITORY_PROOF_ARGS)
	docker compose rm -sf api redis $(COMPOSE_REPOSITORY_SERVICE)
	docker compose down --volumes --remove-orphans

jupyterhub-stack-up:
ifeq ($(filter 1 true yes,$(JUPYTERHUB_STACK_REBUILD)),)
	$(MAKE) -f Makefile -f $(JUPYTERHUB_STACK_CONFIG) helm-up HELM_WITH_JUPYTERHUB=1 JUPYTERHUB_STACK_IMAGE_TAG=$(JUPYTERHUB_STACK_IMAGE_TAG)
else
	$(PYTHON) $(JUPYTERHUB_STACK_IMAGE_PREPARE) --tag $(JUPYTERHUB_STACK_IMAGE_TAG) --kind-cluster $(JUPYTERHUB_KIND_CLUSTER) $(if $(filter 1 true yes,$(JUPYTERHUB_STACK_BUILD_NO_CACHE)),--no-cache,)
	$(MAKE) -f Makefile -f $(JUPYTERHUB_STACK_CONFIG) helm-up HELM_WITH_JUPYTERHUB=1 JUPYTERHUB_STACK_IMAGE_TAG=$(JUPYTERHUB_STACK_IMAGE_TAG) HELM_EXTRA_ARGS="$(JUPYTERHUB_STACK_REBUILD_HELM_ARGS)"
endif

jupyterhub-stack-down:
	$(MAKE) -f Makefile -f $(JUPYTERHUB_STACK_CONFIG) helm-wipe jupyterhub-down

jupyterhub-up:
	kubectl create namespace $(HELM_NAMESPACE) --dry-run=client -o yaml | kubectl apply -f -
	kubectl create secret generic $(JUPYTERHUB_SERVICE_TOKEN_SECRET) --namespace $(HELM_NAMESPACE) --from-literal=$(JUPYTERHUB_SERVICE_TOKEN_KEY)=$(JUPYTERHUB_SERVICE_TOKEN) --from-literal=$(JUPYTERHUB_REPOSITORY_UI_SERVICE_TOKEN_KEY)=$(JUPYTERHUB_REPOSITORY_UI_SERVICE_TOKEN) --from-literal=$(JUPYTERHUB_WORKBOOK_USER_TOKEN_KEY)=$(JUPYTERHUB_WORKBOOK_USER_TOKEN) --from-literal=alice-token=$(JUPYTERHUB_WORKBOOK_ALICE_TOKEN) --from-literal=bob-token=$(JUPYTERHUB_WORKBOOK_BOB_TOKEN) --from-literal=carol-token=$(JUPYTERHUB_WORKBOOK_CAROL_TOKEN) --from-literal=mallory-token=$(JUPYTERHUB_WORKBOOK_MALLORY_TOKEN) --dry-run=client -o yaml | kubectl apply -f -
	helm repo add jupyterhub https://hub.jupyter.org/helm-chart/
	helm repo update jupyterhub
	helm upgrade --install $(JUPYTERHUB_RELEASE) $(JUPYTERHUB_CHART) --namespace $(HELM_NAMESPACE) --create-namespace --wait --timeout $(HELM_TIMEOUT) -f $(JUPYTERHUB_VALUES) $(JUPYTERHUB_NOTEBOOK_PACKAGE_ARGS) $(JUPYTERHUB_EXTRA_ARGS) $(JUPYTERHUB_OPTIONAL_REPOSITORY_ARGS) $(JUPYTERHUB_OPTIONAL_REPOSITORY_UI_ARGS)

jupyterhub-down:
	-kubectl delete pod --namespace $(HELM_NAMESPACE) -l app.kubernetes.io/instance=$(JUPYTERHUB_RELEASE),app.kubernetes.io/component=singleuser-server --ignore-not-found
	-helm uninstall $(JUPYTERHUB_RELEASE) --namespace $(HELM_NAMESPACE) --ignore-not-found
	-kubectl delete pvc hub-db-dir --namespace $(HELM_NAMESPACE) --ignore-not-found
	-kubectl delete secret $(JUPYTERHUB_SERVICE_TOKEN_SECRET) --namespace $(HELM_NAMESPACE) --ignore-not-found

jupyterhub-workbook-proof:
	$(PYTHON) $(JUPYTERHUB_FULL_STACK_PROOF) --stack-config $(JUPYTERHUB_STACK_CONFIG) --namespace $(HELM_NAMESPACE) --release $(HELM_RELEASE) --token $(JUPYTERHUB_WORKBOOK_USER_TOKEN) --kind $(JUPYTERHUB_WORKBOOK_KIND) --workbook-proof $(JUPYTERHUB_WORKBOOK_PROOF) $(if $(filter 1 true yes,$(GOBLIN_REPOSITORY_ENABLED)),--repository-url $(JUPYTERHUB_REPOSITORY_URL),)

jupyterhub-repository-proof:
	$(PYTHON) $(JUPYTERHUB_REPOSITORY_FULL_STACK_PROOF) --stack-config $(JUPYTERHUB_STACK_CONFIG) --namespace $(HELM_NAMESPACE) --release $(HELM_RELEASE) --jupyterhub-release $(JUPYTERHUB_RELEASE) --alice-token $(JUPYTERHUB_WORKBOOK_ALICE_TOKEN) --bob-token $(JUPYTERHUB_WORKBOOK_BOB_TOKEN) --carol-token $(JUPYTERHUB_WORKBOOK_CAROL_TOKEN) --mallory-token $(JUPYTERHUB_WORKBOOK_MALLORY_TOKEN)

jupyterhub-repository-ui-proof:
	$(PYTHON) $(JUPYTERHUB_REPOSITORY_UI_PROOF) --stack-config $(JUPYTERHUB_STACK_CONFIG) --namespace $(HELM_NAMESPACE) --release $(HELM_RELEASE) --jupyterhub-release $(JUPYTERHUB_RELEASE) --alice-token $(JUPYTERHUB_WORKBOOK_ALICE_TOKEN) --bob-token $(JUPYTERHUB_WORKBOOK_BOB_TOKEN) --carol-token $(JUPYTERHUB_WORKBOOK_CAROL_TOKEN) --mallory-token $(JUPYTERHUB_WORKBOOK_MALLORY_TOKEN)

helm-up: $(if $(filter 1 true yes,$(HELM_WITH_JUPYTERHUB)),jupyterhub-up)
	helm upgrade --install $(HELM_RELEASE) $(HELM_CHART) --namespace $(HELM_NAMESPACE) --create-namespace --wait --timeout $(HELM_TIMEOUT) $(HELM_ARGS) $(HELM_EXTRA_ARGS) $(HELM_OPTIONAL_REPOSITORY_ARGS) $(HELM_OPTIONAL_REPOSITORY_UI_ARGS) $(if $(filter 1 true yes,$(HELM_WITH_JUPYTERHUB)),$(HELM_JUPYTERHUB_ARGS),)

helm-wipe:
	-helm uninstall $(HELM_RELEASE) --namespace $(HELM_NAMESPACE) --ignore-not-found
	-kubectl delete pvc $(HELM_PVC) --namespace $(HELM_NAMESPACE) --ignore-not-found

helm-restart-clean: helm-wipe helm-up

stack-wipe: docker-wipe helm-wipe

stack-restart-clean: docker-restart-clean helm-restart-clean

schedule:
	$(PYTHON) -m goblin_king.cli schedules add example.echo --cron "* * * * *" --input $(INPUT) --registry $(REGISTRY) --db $(DB) --due-now

run-once:
	$(PYTHON) -m goblin_king.cli scheduler run-once --registry $(REGISTRY) --images $(IMAGES) --db $(DB) --redis-url $(REDIS_URL)

simulate: deploy schedule run-once
	$(PYTHON) -m goblin_king.cli jobs list --db $(DB)

events-smoke: simulate
	$(PYTHON) -m goblin_king.cli events list --db $(DB) --limit 20
	$(PYTHON) -m goblin_king.cli heartbeats list --db $(DB)

api:
	$(PYTHON) -m goblin_king.cli api run --settings goblin-king-api.json

api-smoke:
	$(PYTHON) -c "import json, urllib.error, urllib.request; base='http://127.0.0.1:8000'; auth={'Authorization':'Bearer local-dev-token'}; print(urllib.request.urlopen(base + '/health').read().decode()); req=urllib.request.Request(base + '/goblins', headers=auth); print(urllib.request.urlopen(req).read().decode()); body=json.dumps({'kind':'example.echo','input':{'message':'hello api'}}).encode(); req=urllib.request.Request(base + '/jobs', data=body, headers={'Content-Type':'application/json'}, method='POST');\ntry:\n    urllib.request.urlopen(req)\nexcept urllib.error.HTTPError as e:\n    print('unauthenticated_status=' + str(e.code));\nreq=urllib.request.Request(base + '/jobs', data=body, headers={'Content-Type':'application/json','Authorization':'Bearer local-dev-token'}, method='POST'); print(urllib.request.urlopen(req).read().decode()); req=urllib.request.Request(base + '/jobs?limit=5', headers=auth); print(urllib.request.urlopen(req).read().decode()); req=urllib.request.Request(base + '/openapi.json'); data=json.loads(urllib.request.urlopen(req).read()); print('openapi_has_bearer=' + str('HTTPBearer' in data['components']['securitySchemes']))"

long-hello-up:
	docker compose --profile admin up -d long-hello

long-hello-down:
	docker compose stop long-hello

admin-smoke:
	$(PYTHON) -c "import json, time, urllib.request; base='http://127.0.0.1:8080'; token='local-dev-token'; service_url='http://long-hello:8080'; headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'}; print('admin_status=' + str(urllib.request.urlopen(base+'/admin').status)); req=urllib.request.Request(base+'/admin-api/goblins', headers={'Authorization':'Bearer '+token}); print('goblins=' + urllib.request.urlopen(req).read().decode()); body=json.dumps({'kind':'example.hello','input':{'name':'World'}}).encode(); req=urllib.request.Request(base+'/admin-api/jobs', data=body, headers=headers, method='POST'); job=json.loads(urllib.request.urlopen(req).read()); print('hello_job=' + job['id']); cancel=urllib.request.Request(base+'/admin-api/jobs/'+job['id']+'/cancel', headers={'Authorization':'Bearer '+token}, method='POST'); print('cancel_status=' + json.loads(urllib.request.urlopen(cancel).read())['status']); body=json.dumps({'kind':'example.long-hello','base_url':service_url}).encode(); req=urllib.request.Request(base+'/admin-api/services/long-running', data=body, headers=headers, method='POST'); service=json.loads(urllib.request.urlopen(req).read()); print('service=' + service['id']); req=urllib.request.Request(base+'/admin-api/services/long-running/'+service['id']+'/probe', headers={'Authorization':'Bearer '+token}, method='POST'); first=json.loads(urllib.request.urlopen(req).read()); time.sleep(1); second=json.loads(urllib.request.urlopen(req).read()); print(first['response']['json']['message']); print('timestamp_changed=' + str(first['response']['json']['timestamp'] != second['response']['json']['timestamp'])); stop=urllib.request.Request(base+'/admin-api/services/long-running/'+service['id']+'/stop', headers={'Authorization':'Bearer '+token}, method='POST'); print('service_stop=' + json.loads(urllib.request.urlopen(stop).read())['status']); req=urllib.request.Request(base+'/admin-api/events?limit=10', headers={'Authorization':'Bearer '+token}); print(urllib.request.urlopen(req).read().decode())"

admin-runtime-audit:
	$(PYTHON) scripts/admin_runtime_audit.py --base-url $(ADMIN_BASE) --token $(ADMIN_TOKEN) --long-service-url $(LONG_HELLO_URL)

doctor:
	$(PYTHON) -m goblin_king.cli doctor

demo:
	$(PYTHON) -m goblin_king.cli demo up

demo-down:
	$(PYTHON) -m goblin_king.cli demo down

project-validate:
	$(PYTHON) -m goblin_king.cli project validate --project $(PROJECT)

project-build-workers:
	$(PYTHON) -m goblin_king.cli workers build --images $(PROJECT_IMAGES)

project-discovery-reload:
	$(PYTHON) -c "import urllib.request; base='$(ADMIN_BASE)'; token='$(ADMIN_TOKEN)'; req=urllib.request.Request(base+'/admin-api/admin/discovery/reload', headers={'Authorization':'Bearer '+token}, method='POST'); print(urllib.request.urlopen(req).read().decode())"

project-admin-proof:
	$(PYTHON) -c "import urllib.request; base='$(ADMIN_BASE)'; token='$(ADMIN_TOKEN)'; req=urllib.request.Request(base+'/admin-api/goblins', headers={'Authorization':'Bearer '+token}); print(urllib.request.urlopen(req).read().decode())"

adopter-smoke: redis-up
	$(PYTHON) -m goblin_king.cli smoke adopter-project --redis-url $(REDIS_URL)

release-wheel:
	$(PYTHON) -m pip wheel . -w $(DIST)

release-check: local-ci project-validate helm-template
	cd admin-ui && npm test -- --run
	cd admin-ui && npm run build

helm-template:
	helm template $(HELM_RELEASE) $(HELM_CHART) --namespace $(HELM_NAMESPACE) $(HELM_ARGS) $(HELM_EXTRA_ARGS)

helm-admin-smoke:
	$(PYTHON) -c "import urllib.request; base='http://goblin-king.local'; token='local-dev-token'; print('admin_status=' + str(urllib.request.urlopen(base+'/admin', timeout=10).status)); req=urllib.request.Request(base+'/admin-api/goblins', headers={'Authorization':'Bearer '+token}); print(urllib.request.urlopen(req, timeout=10).read().decode())"

kind-smoke: helm-template
	$(PYTHON) -c "import shutil; print('kind_available=' + str(shutil.which('kind') is not None))"

clean:
	$(PYTHON) -c "import shutil; shutil.rmtree('.goblin-king', ignore_errors=True)"

clean-all:
	git clean -fdX

docker-clean:
	docker compose down --volumes --remove-orphans
