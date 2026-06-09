PYTHON ?= python
DB ?= .goblin-king/goblin-king.sqlite3
REGISTRY ?= examples/goblins.json
IMAGES ?= goblin-images.json
INPUT ?= examples/input.json
REDIS_URL ?= redis://localhost:6379/0

.PHONY: help install test lint local-ci build-workers redis-up redis-down deploy run-once schedule simulate events-smoke api api-smoke clean docker-clean

help:
	@echo "Targets:"
	@echo "  install        Install the project in editable mode"
	@echo "  test           Run pytest locally"
	@echo "  lint           Run ruff locally"
	@echo "  local-ci       Run local CI checks"
	@echo "  build-workers  Build configured Docker worker images"
	@echo "  redis-up       Start Redis with Docker Compose"
	@echo "  redis-down     Stop Redis"
	@echo "  deploy         Build workers and start Redis"
	@echo "  schedule       Add a due example.echo schedule"
	@echo "  run-once       Run one Docker scheduler pass"
	@echo "  simulate       Deploy, schedule, run once, and list jobs"
	@echo "  events-smoke   Print recent events and heartbeats after simulation"
	@echo "  api            Run the FastAPI control plane"
	@echo "  api-smoke      Exercise local API health, auth, and queued jobs"
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

redis-up:
	docker compose up -d redis

redis-down:
	docker compose stop redis

deploy: build-workers redis-up

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
	$(PYTHON) -c "import json, urllib.error, urllib.request; base='http://127.0.0.1:8000'; print(urllib.request.urlopen(base + '/health').read().decode()); print(urllib.request.urlopen(base + '/goblins').read().decode()); body=json.dumps({'kind':'example.echo','input':{'message':'hello api'}}).encode(); req=urllib.request.Request(base + '/jobs', data=body, headers={'Content-Type':'application/json'}, method='POST');\ntry:\n    urllib.request.urlopen(req)\nexcept urllib.error.HTTPError as e:\n    print('unauthenticated_status=' + str(e.code));\nreq=urllib.request.Request(base + '/jobs', data=body, headers={'Content-Type':'application/json','Authorization':'Bearer local-dev-token'}, method='POST'); print(urllib.request.urlopen(req).read().decode()); print(urllib.request.urlopen(base + '/jobs').read().decode())"

clean:
	$(PYTHON) -c "import shutil; shutil.rmtree('.goblin-king', ignore_errors=True)"

docker-clean:
	docker compose down --volumes --remove-orphans
