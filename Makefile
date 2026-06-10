PYTHON ?= python
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

.PHONY: help install test lint local-ci build-workers build-cross-language-workers run-cross-language-proof validate-cross-language-workers build-behavior-workers run-behavior-proof validate-behavior-workers admin-build redis-up redis-down deploy run-once schedule simulate events-smoke api api-smoke admin-up long-hello-up long-hello-down admin-smoke project-validate project-build-workers project-discovery-reload project-admin-proof release-wheel release-check helm-template helm-admin-smoke kind-smoke clean docker-clean

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
	@echo "  schedule       Add a due example.echo schedule"
	@echo "  run-once       Run one Docker scheduler pass"
	@echo "  simulate       Deploy, schedule, run once, and list jobs"
	@echo "  events-smoke   Print recent events and heartbeats after simulation"
	@echo "  api            Run the FastAPI control plane"
	@echo "  api-smoke      Exercise local API health, auth, and queued jobs"
	@echo "  long-hello-up  Start the long-running hello service with Compose"
	@echo "  admin-up       Start the React admin, API, Redis, and long service"
	@echo "  admin-smoke    Exercise Docker React admin API proof flow"
	@echo "  project-validate Validate host-project discovery settings"
	@echo "  project-build-workers Build host-project worker images"
	@echo "  project-discovery-reload Reload host-project discovery through admin API"
	@echo "  project-admin-proof Prove host-project goblins are visible through admin API"
	@echo "  release-wheel  Build the internal wheel into DIST"
	@echo "  release-check  Run local release/adoption proof commands"
	@echo "  helm-template  Render the optional Helm chart"
	@echo "  helm-admin-smoke Exercise Helm React admin through goblin-king.local"
	@echo "  kind-smoke     Render Helm and report whether kind is available"
	@echo "  clean          Remove local Goblin King runtime state"
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
	docker compose --profile api --profile admin up -d redis api admin long-hello

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

project-validate:
	$(PYTHON) -m goblin_king.cli project validate --project $(PROJECT)

project-build-workers:
	$(PYTHON) -m goblin_king.cli workers build --images $(PROJECT_IMAGES)

project-discovery-reload:
	$(PYTHON) -c "import urllib.request; base='$(ADMIN_BASE)'; token='$(ADMIN_TOKEN)'; req=urllib.request.Request(base+'/admin-api/admin/discovery/reload', headers={'Authorization':'Bearer '+token}, method='POST'); print(urllib.request.urlopen(req).read().decode())"

project-admin-proof:
	$(PYTHON) -c "import urllib.request; base='$(ADMIN_BASE)'; token='$(ADMIN_TOKEN)'; req=urllib.request.Request(base+'/admin-api/goblins', headers={'Authorization':'Bearer '+token}); print(urllib.request.urlopen(req).read().decode())"

release-wheel:
	$(PYTHON) -m pip wheel . -w $(DIST)

release-check: local-ci project-validate helm-template
	cd admin-ui && npm test -- --run
	cd admin-ui && npm run build

helm-template:
	helm template goblin-king charts/goblin-king

helm-admin-smoke:
	$(PYTHON) -c "import urllib.request; base='http://goblin-king.local'; token='local-dev-token'; print('admin_status=' + str(urllib.request.urlopen(base+'/admin', timeout=10).status)); req=urllib.request.Request(base+'/admin-api/goblins', headers={'Authorization':'Bearer '+token}); print(urllib.request.urlopen(req, timeout=10).read().decode())"

kind-smoke: helm-template
	$(PYTHON) -c "import shutil; print('kind_available=' + str(shutil.which('kind') is not None))"

clean:
	$(PYTHON) -c "import shutil; shutil.rmtree('.goblin-king', ignore_errors=True)"

docker-clean:
	docker compose down --volumes --remove-orphans
