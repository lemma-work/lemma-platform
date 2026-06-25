# bash, not zsh: CI runners (Ubuntu) ship bash but not zsh, and the recipes use
# no zsh-only syntax. zsh here made `make test-unit` fail with Error 127 in CI.
SHELL := /bin/bash

TEAMS_MANIFEST_DIR ?= manifests/teams
OUTPUT ?= $(TEAMS_MANIFEST_DIR)/lemma-teams.zip
# Parallelism for the fast e2e suite (pytest-xdist). Each worker spins up its
# OWN isolated testcontainers (Postgres/Redis/SuperTokens) on ephemeral ports
# and per-worker /tmp roots, so workers never share state. Default 2 is the
# safe, verified-green value (each worker loads a local embedding model + its
# own container trio, so workers are RAM-hungry — Kreuzberg gets OOM-killed if
# the host runs out of memory). Bump on roomy machines: E2E_WORKERS=4 (~2x) or
# E2E_WORKERS=auto (one per core); E2E_WORKERS=0 disables (in-process serial).
# Only the fast suite is parallelized; the runtime suite (real streaq worker +
# docker workspace runtime) shares a global container namespace and stays serial.
E2E_WORKERS ?= 2
E2E_PARALLEL := -n $(E2E_WORKERS) --dist loadscope

# Load test settings (override on the command line)
LOAD_TEST_COMPOSE := load_tests/docker-compose.load-test.yml
LOAD_TEST_ENV     := load_tests/.env.load_test
MAX_SUBSCRIBERS   ?= 100
WRITER_VUS        ?= 2

.PHONY: help test test-unit test-e2e test-e2e-real test-e2e-fast test-e2e-runtime test-module coverage coverage-unit coverage-e2e test-collect lint migrate docker-build docker-build-cloud docker-build-arm64 docker-build-amd64 agentbox-build teams-manifest-zip load-test-build load-test-up load-test-migrate load-test-setup load-test-run load-test-sse load-test-monitor load-test-down load-test

help:
	@echo "Lemma backend:"
	@echo "  make test              # run backend tests"
	@echo "  make test-unit         # run non-e2e tests"
	@echo "  make test-e2e          # run all e2e tests"
	@echo "  make test-e2e-fast     # fast API e2e (no slow/worker/workspace/provider/local_cli),"
	@echo "                         #   parallel via pytest-xdist (E2E_WORKERS=$(E2E_WORKERS); auto|0 to override)"
	@echo "  make test-e2e-runtime  # run slow worker/workspace/local/provider e2e tests (serial)"
	@echo "  make test-module MODULE=pod"
	@echo "  make coverage-unit     # unit coverage"
	@echo "  make coverage-e2e      # e2e coverage"
	@echo "  make lint              # run ruff checks"
	@echo "  make migrate           # apply database migrations"
	@echo "  make docker-build-cloud  # build the lemma-cloud image"
	@echo "  (full dev workflow: use 'make dev STACK=cloud|oss' from the repo root)"
	@echo "  make teams-manifest-zip OUTPUT=path"
	@echo "                         # package Teams manifest zip"
	@echo "  make docker-build      # build backend image from monorepo root"
	@echo "  make agentbox-build # build local AgentBox runtime images"
	@echo ""
	@echo "Load testing (WebSocket capacity on a simulated 2 CPU / 2 GB server):"
	@echo "  make load-test         # full end-to-end: build → up → setup → run → stats"
	@echo "  make load-test-build   # build lemma-backend:load-test Docker image"
	@echo "  make load-test-up      # start infra + API + worker with resource limits"
	@echo "  make load-test-migrate # run alembic migrations inside the API container"
	@echo "  make load-test-setup   # provision test user / pod / table, write .env.load_test"
	@echo "  make load-test-run     # run k6 WebSocket load test (reads .env.load_test)"
	@echo "  make load-test-sse     # run k6 SSE streaming load test"
	@echo "  make load-test-monitor # monitor DB connections (pg_stat_activity poller)"
	@echo "  make load-test-down    # tear down containers and clean up"
	@echo "  Override: MAX_SUBSCRIBERS=200 WRITER_VUS=4 make load-test"
	@echo ""
	@echo "Run app-level commands such as setup/start/logs from the repository root."

test:
	uv run pytest

test-unit:
	uv run pytest -m "not e2e"

# Default e2e gate: fast + deterministic. The agent LLM is a mocked
# FunctionModel and workspace tools hit the in-process fake AgentBox, so there
# is NO real model call and NO Docker workspace. Parallelized via xdist. This is
# what CI runs (no API keys, no Docker image needed). real_llm/real_sandbox
# tests auto-skip here.
test-e2e:
	uv run pytest $(E2E_PARALLEL) -m e2e -o timeout=120

# Real e2e: real model from env (needs LEMMA_OPENAI_API_KEY) + real Docker
# AgentBox. Runs the real_llm/real_sandbox tests too. For local/manual/nightly
# verification of the actual model + sandbox. Serial (Docker workspace shares a
# global container namespace).
test-e2e-real:
	E2E_REAL=1 uv run pytest -m e2e

test-e2e-fast:
	uv run pytest $(E2E_PARALLEL) -m "e2e and not slow and not worker and not workspace and not provider and not local_cli"

# Real runtime suite only (real model + Docker), serial. Subset of test-e2e-real.
test-e2e-runtime:
	E2E_REAL=1 uv run pytest -m "e2e and (slow or worker or workspace or provider or local_cli)"

test-module:
	@test -n "$(MODULE)" || (echo "MODULE is required, e.g. make test-module MODULE=pod" && exit 1)
	uv run pytest app/modules/$(MODULE)

coverage: coverage-unit coverage-e2e

coverage-unit:
	uv run pytest -m "not e2e" --cov=app --cov-report=term-missing --cov-report=xml:coverage-unit.xml

coverage-e2e:
	uv run pytest -m e2e --cov=app --cov-report=term-missing --cov-report=xml:coverage-e2e.xml

coverage-module:
	@test -n "$(MODULE)" || (echo "MODULE is required, e.g. make coverage-module MODULE=agent" && exit 1)
	uv run pytest app/modules/$(MODULE) \
		--cov=app/modules/$(MODULE) \
		--cov-report=term-missing \
		--cov-fail-under=90

test-collect:
	uv run pytest --collect-only -q

lint:
	uv run ruff check .

migrate:
	uv run alembic upgrade head

# NOTE: the full dev workflow (infra + app + frontend, per stack, properly
# port-isolated) lives in the repo-root Makefile: `make dev STACK=cloud|oss`.
# Don't add a `dev` target here — it shadowed the root one and bound the wrong
# ports.

docker-build:
	cd .. && docker build -f lemma-backend/Dockerfile -t lemma-backend:local .

docker-build-cloud:
	cd .. && docker build -f lemma-cloud/Dockerfile -t lemma-cloud:local .

docker-build-arm64:
	cd .. && docker buildx build --platform linux/arm64 -f lemma-backend/Dockerfile -t lemma-backend:local .

docker-build-amd64:
	cd .. && docker buildx build --platform linux/amd64 -f lemma-backend/Dockerfile -t lemma-backend:local .

agentbox-build:
	$(MAKE) -C ../agentbox build

teams-manifest-zip:
	python3 scripts/package_teams_manifest.py \
		--manifest-dir "$(TEAMS_MANIFEST_DIR)" \
		--output "$(OUTPUT)"

# --------------------------------------------------------------------------
# Load testing — WebSocket capacity on a simulated 2 CPU / 2 GB server
# --------------------------------------------------------------------------

load-test-build:
	@echo "Building lemma-backend:load-test image from monorepo root…"
	cd .. && docker build -f lemma-backend/Dockerfile -t lemma-backend:load-test .

load-test-up:
	docker compose -f $(LOAD_TEST_COMPOSE) up -d
	@echo "Waiting for API health check…"
	@until docker compose -f $(LOAD_TEST_COMPOSE) exec -T api python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" > /dev/null 2>&1; do \
		printf '.'; sleep 3; \
	done
	@echo " API is ready."

load-test-migrate:
	@echo "Running database migrations…"
	docker compose -f $(LOAD_TEST_COMPOSE) exec -T api alembic upgrade head
	@echo "Migrations complete."

load-test-setup: load-test-migrate
	uv run python load_tests/setup.py

load-test-run:
	@test -f $(LOAD_TEST_ENV) || (echo "$(LOAD_TEST_ENV) not found — run 'make load-test-setup' first" && exit 1)
	@echo "Running k6 WebSocket load test (MAX_SUBSCRIBERS=$(MAX_SUBSCRIBERS), WRITER_VUS=$(WRITER_VUS))…"
	docker run --rm --network host \
		--env-file $(LOAD_TEST_ENV) \
		-e MAX_SUBSCRIBERS=$(MAX_SUBSCRIBERS) \
		-e WRITER_VUS=$(WRITER_VUS) \
		-v "$$(pwd)/load_tests:/scripts" \
		grafana/k6:latest run /scripts/ws_concurrent.js
	@echo ""
	@echo "Container resource snapshot at test end:"
	docker stats lemma-load-api lemma-load-worker --no-stream --format \
		"table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}"

load-test-sse:
	@test -f $(LOAD_TEST_ENV) || (echo "$(LOAD_TEST_ENV) not found — run 'make load-test-setup' first" && exit 1)
	@echo "Running k6 SSE load test (MAX_STREAMERS=$(MAX_STREAMERS), WRITER_VUS=$(WRITER_VUS))…"
	docker run --rm --network host \
		--env-file $(LOAD_TEST_ENV) \
		-e MAX_STREAMERS=$(MAX_STREAMERS) \
		-e WRITER_VUS=$(WRITER_VUS) \
		-v "$$(pwd)/load_tests:/scripts" \
		grafana/k6:latest run /scripts/sse_concurrent.js
	@echo ""
	@echo "Container resource snapshot at test end:"
	docker stats lemma-load-api lemma-load-worker --no-stream --format \
		"table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}"

load-test-monitor:
	@echo "Monitoring DB connections (Ctrl+C to stop)…"
	uv run python load_tests/db_monitor.py --interval 5

load-test-down:
	docker compose -f $(LOAD_TEST_COMPOSE) down -v
	rm -f $(LOAD_TEST_ENV)

load-test: load-test-build load-test-up load-test-migrate load-test-setup load-test-run
	@echo ""
	@echo "Done. Run 'make load-test-down' to remove containers and credentials."

