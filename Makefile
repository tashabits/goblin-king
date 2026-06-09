PYTHON ?= python
DB ?= .goblin-king/goblin-king.sqlite3
REGISTRY ?= examples/goblins.json
IMAGES ?= goblin-images.json
INPUT ?= examples/input.json
REDIS_URL ?= redis://localhost:6379/0

.PHONY: help install test lint local-ci build-workers redis-up redis-down deploy run-once schedule simulate clean docker-clean

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

clean:
	$(PYTHON) -c "import shutil; shutil.rmtree('.goblin-king', ignore_errors=True)"

docker-clean:
	docker compose down --volumes --remove-orphans
