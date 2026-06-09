PYTHON ?= python
DB ?= .goblin-king/goblin-king.sqlite3
REGISTRY ?= examples/goblins.json
IMAGES ?= goblin-images.json
INPUT ?= examples/input.json
REDIS_URL ?= redis://localhost:6379/0
LONG_HELLO_URL ?= http://long-hello:8080

.PHONY: help install test lint local-ci build-workers admin-build redis-up redis-down deploy run-once schedule simulate events-smoke api api-smoke admin-up long-hello-up long-hello-down admin-smoke helm-template helm-admin-smoke kind-smoke clean docker-clean

help:
	@echo "Targets:"
	@echo "  install        Install the project in editable mode"
	@echo "  test           Run pytest locally"
	@echo "  lint           Run ruff locally"
	@echo "  local-ci       Run local CI checks"
	@echo "  build-workers  Build configured Docker worker images"
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
