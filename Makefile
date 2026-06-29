SHELL := /bin/bash

# ──────────────────────────────────────────────────────────────────────────────
# Lemma Platform — root developer workflow
#
#   make init          create .env files with local defaults (idempotent)
#   make dev           start infra + backend + frontend (hot-reload)
#   make dev RELOAD=1  same, with uvicorn --reload on the backend
#   make stop          stop backend/frontend processes
#   make stop-all      also stop infra containers
#   make test          run all component test suites
#   make coverage      full coverage report (unit + e2e per component)
# ──────────────────────────────────────────────────────────────────────────────

.PHONY: help init dev stop stop-all logs \
        test test-backend test-backend-unit test-backend-e2e \
        test-frontend test-cli test-cli-unit test-cli-e2e test-python \
        coverage coverage-backend coverage-backend-unit coverage-backend-e2e \
        coverage-backend-module coverage-cli coverage-cli-unit coverage-cli-e2e coverage-frontend \
        lint migrate

# ── Configuration ─────────────────────────────────────────────────────────────

RELOAD        ?= 0
E2E_WORKERS   ?= 2
MODULE        ?=

BACKEND_DIR   := lemma-backend
FRONTEND_DIR  := lemma-frontend
CLI_DIR       := lemma-cli
PYTHON_DIR    := lemma-python
TS_DIR        := lemma-typescript
AGENTBOX_DIR  := agentbox

PID_FILE      := .dev-pids
BACKEND_PID_FILE  := $(BACKEND_DIR)/.dev-backend.pid
FRONTEND_PID_FILE := $(FRONTEND_DIR)/.dev-frontend.pid
INFRA_PID_FILE    := $(BACKEND_DIR)/.dev-infra.pid
AGENTBOX_PID_FILE := $(AGENTBOX_DIR)/.dev-agentbox.pid

# ── Canonical dev ports + URLs ───────────────────────────────────────────────
# These are the SINGLE source of truth for the dev stack. Infra (docker
# compose), backend settings (API_URL / FRONTEND_URL / DATABASE_URL / …) and
# the frontend (NEXT_PUBLIC_* + runtime-config.js) all derive from these.
# Change one number here and the whole stack stays consistent. Picked to
# differ from the installed lemma-stack defaults (3700/8700/4173/5432/…)
# so a fresh platform checkout can sit alongside an installed copy.

DEV_BACKEND_PORT      ?= 8710
DEV_FRONTEND_PORT     ?= 3710
DEV_AUTH_FRONTEND_PORT?= 4173
DEV_POSTGRES_PORT     ?= 5432
DEV_REDIS_PORT        ?= 6379
DEV_SUPERTOKENS_PORT  ?= 3567
DEV_KREUZBERG_PORT    ?= 8002
DEV_AGENTBOX_PORT     ?= 8721

DEV_BACKEND_URL       := http://localhost:$(DEV_BACKEND_PORT)
DEV_FRONTEND_URL      := http://localhost:$(DEV_FRONTEND_PORT)
DEV_AUTH_FRONTEND_URL := http://localhost:$(DEV_AUTH_FRONTEND_PORT)
DEV_DATABASE_URL      := postgresql+asyncpg://postgres:postgres@localhost:$(DEV_POSTGRES_PORT)/lemma
DEV_REDIS_URL         := redis://localhost:$(DEV_REDIS_PORT)/0
DEV_SUPERTOKENS_URL   := http://localhost:$(DEV_SUPERTOKENS_PORT)
DEV_AGENTBOX_URL      := http://127.0.0.1:$(DEV_AGENTBOX_PORT)
DEV_AGENTBOX_API_KEY  ?= dev-agentbox-key

COMMON_DEV_ENV := \
	DEV_POSTGRES_PORT=$(DEV_POSTGRES_PORT) \
	DEV_REDIS_PORT=$(DEV_REDIS_PORT) \
	DEV_REDIS_UI_PORT=8001 \
	DEV_SUPERTOKENS_PORT=$(DEV_SUPERTOKENS_PORT) \
	DEV_KREUZBERG_PORT=$(DEV_KREUZBERG_PORT)

FRONTEND_DEV_ENV := \
	NEXT_PUBLIC_API_URL=$(DEV_BACKEND_URL) \
	NEXT_PUBLIC_SITE_URL=$(DEV_FRONTEND_URL) \
	NEXT_PUBLIC_AUTH_URL=$(DEV_FRONTEND_URL)

# AgentBox manager — the workspace sandbox provider. Runs as its own uvicorn
# process with the local Docker provider; the backend reaches it over HTTP
# using AGENTBOX_API_URL + AGENTBOX_API_KEY (written into the backend .env).
AGENTBOX_DEV_ENV := \
	AGENTBOX_PROVIDER=docker \
	AGENTBOX_API_KEY=$(DEV_AGENTBOX_API_KEY) \
	AGENTBOX_API_URL=$(DEV_AGENTBOX_URL) \
	AGENTBOX_RUNTIME_IMAGE=ghcr.io/lemma-work/lemma-agentbox-runtime:latest \
	AGENTBOX_STATE_DB_PATH=/tmp/agentbox-state.db \
	AGENTBOX_STORAGE_ROOT=/tmp/agentbox-workspaces \
	AGENTBOX_ENDPOINT_HOST=127.0.0.1 \
	AGENTBOX_SESSION_IDLE_TIMEOUT_SECONDS=300 \
	AGENTBOX_SANDBOX_IDLE_TIMEOUT_SECONDS=300 \
	AGENTBOX_CLEANUP_INTERVAL_SECONDS=30

# ── Help ──────────────────────────────────────────────────────────────────────

help:
	@echo ""
	@echo "Lemma Platform — developer commands"
	@echo ""
	@echo "  Setup"
	@echo "    make init               create .env files with local defaults (idempotent)"
	@echo ""
	@echo "  Dev stack"
	@echo "    make dev                start infra + backend + frontend"
	@echo "    make dev RELOAD=1       same, with uvicorn --reload on the backend"
	@echo "    make stop               stop backend/frontend processes"
	@echo "    make stop-all           also bring down infra containers"
	@echo "    make logs               tail backend logs"
	@echo ""
	@echo "  Tests"
	@echo "    make test               run all component test suites"
	@echo "    make test-backend       backend unit + fast e2e"
	@echo "    make test-backend-unit  backend unit tests only"
	@echo "    make test-backend-e2e   backend fast e2e (E2E_WORKERS=$(E2E_WORKERS))"
	@echo "    make test-frontend      frontend vitest suite"
	@echo "    make test-cli           lemma-cli unit + e2e tests"
	@echo "    make test-cli-unit      lemma-cli unit tests only (no docker)"
	@echo "    make test-cli-e2e       lemma-cli e2e (real backend + docker; needs docker)"
	@echo "    make test-python        lemma-python SDK tests (non-integration)"
	@echo ""
	@echo "  Coverage"
	@echo "    make coverage                 full coverage (unit + e2e, all components)"
	@echo "    make coverage-backend         backend unit + e2e coverage report"
	@echo "    make coverage-backend-unit    backend unit coverage"
	@echo "    make coverage-backend-e2e     backend e2e coverage"
	@echo "    make coverage-backend-module MODULE=agent  per-module backend coverage"
	@echo "    make coverage-cli             lemma-cli unit + e2e coverage"
	@echo "    make coverage-cli-unit        lemma-cli unit coverage (no docker)"
	@echo "    make coverage-cli-e2e         lemma-cli e2e coverage (needs docker)"
	@echo "    make coverage-frontend        frontend vitest coverage"
	@echo ""
	@echo "  Other"
	@echo "    make lint               ruff + eslint across all components"
	@echo "    make migrate            apply backend database migrations"
	@echo ""

# ── Init ──────────────────────────────────────────────────────────────────────

init:
	@echo "→ Checking prerequisites…"
	@command -v uv >/dev/null 2>&1 || (echo "  ✗ uv not found — install from https://docs.astral.sh/uv/"; exit 1)
	@command -v docker >/dev/null 2>&1 || command -v podman >/dev/null 2>&1 || \
		(echo "  ✗ Docker or Podman required — install Docker Desktop or Podman"; exit 1)
	@command -v node >/dev/null 2>&1 || (echo "  ✗ Node.js not found — install from https://nodejs.org/"; exit 1)
	@echo "  ✓ Prerequisites OK"
	@echo ""
	@echo "→ Installing dependencies…"
	@cd $(BACKEND_DIR) && uv sync --quiet
	@cd $(CLI_DIR) && uv sync --quiet
	@cd $(PYTHON_DIR) && uv sync --quiet
	@cd $(AGENTBOX_DIR) && uv sync --quiet
	@cd $(TS_DIR) && npm install --silent
	@cd $(FRONTEND_DIR) && npm install --silent
	@echo "  ✓ Dependencies installed"
	@echo ""
	@echo "→ Building lemma-sdk (lemma-typescript)…"
	@cd $(TS_DIR) && npm run build --silent
	@echo "  ✓ lemma-sdk built — dist/ ready for frontend import"
	@echo ""
	@# Env files come AFTER install: _init-frontend-env runs the frontend's
	@# gen:runtime-config, which imports @next/env from node_modules. Generating
	@# env before `npm install` aborts a fresh-clone `make init` with
	@# ERR_MODULE_NOT_FOUND before any dependency is installed.
	@echo "→ Creating .env files (skipped if already present)…"
	@$(MAKE) --no-print-directory _init-backend-env
	@$(MAKE) --no-print-directory _init-frontend-env
	@echo ""
	@echo "Done. Run 'make dev' to start the stack."

_init-backend-env:
	@if [ ! -f $(BACKEND_DIR)/.env ]; then \
		echo "  Creating $(BACKEND_DIR)/.env …"; \
		set -e; \
		{ \
			echo "# Lemma backend — local dev defaults (generated by make init)"; \
			echo "# Stack URLs — kept in sync with the canonical ports at the top of the Makefile."; \
			echo "API_URL=$(DEV_BACKEND_URL)"; \
			echo "FRONTEND_URL=$(DEV_FRONTEND_URL)"; \
			echo "AUTH_FRONTEND_URL=$(DEV_AUTH_FRONTEND_URL)"; \
			echo "SUPERTOKENS_CORE_URL=$(DEV_SUPERTOKENS_URL)"; \
			echo "DATABASE_URL=$(DEV_DATABASE_URL)"; \
			echo "REDIS_URL=$(DEV_REDIS_URL)"; \
			printf 'CORS_ORIGINS=["http://localhost:%s","http://127.0.0.1:%s"]\n' "$(DEV_FRONTEND_PORT)" "$(DEV_FRONTEND_PORT)"; \
			echo 'CORS_ORIGIN_REGEX=https?://(localhost|127\.0\.0\.\d+|127\.\d+\.\d+\.\d+|127-0-0-\d+\.sslip\.io|[\w-]+\.nip\.io)(:\d+)?'; \
			echo "# AgentBox sandbox manager — started by 'make dev' on $(DEV_AGENTBOX_URL)"; \
			echo "AGENTBOX_API_URL=$(DEV_AGENTBOX_URL)"; \
			echo "AGENTBOX_API_KEY=$(DEV_AGENTBOX_API_KEY)"; \
			echo "# Model provider — set at least one of the keys below."; \
			echo "LEMMA_DEFAULT_MODEL_TYPE=openai_compat"; \
			echo "LEMMA_OPENAI_API_KEY="; \
			echo "LEMMA_OPENAI_BASE_URL=https://api.openai.com/v1"; \
			echo "LEMMA_OPENAI_DEFAULT_MODEL=gpt-4o"; \
			echo "LEMMA_OPENAI_MODEL_NAMES=gpt-4o,gpt-4o-mini"; \
			echo "# Uncomment for Anthropic instead:"; \
			echo "# LEMMA_DEFAULT_MODEL_TYPE=anthropic_compat"; \
			echo "# LEMMA_ANTHROPIC_API_KEY="; \
			echo "# LEMMA_ANTHROPIC_DEFAULT_MODEL=claude-sonnet-4-5"; \
		} > $(BACKEND_DIR)/.env; \
	else \
		$(MAKE) --no-print-directory _ensure-backend-env-keys; \
	fi

_ensure-backend-env-keys:
	@set -e; missing=""; \
	for k in API_URL FRONTEND_URL AUTH_FRONTEND_URL SUPERTOKENS_CORE_URL DATABASE_URL REDIS_URL CORS_ORIGINS CORS_ORIGIN_REGEX AGENTBOX_API_URL AGENTBOX_API_KEY; do \
		if ! grep -qE "^$$k=" $(BACKEND_DIR)/.env; then missing="$$missing $$k"; fi; \
	done; \
	if [ -z "$$missing" ]; then \
		echo "  $(BACKEND_DIR)/.env already exists with all required keys"; \
	else \
		echo "  $(BACKEND_DIR)/.env missing keys ($$missing) — appending…"; \
		{ \
			echo ""; \
			echo "# Added by make init (stack URLs in sync with canonical ports)"; \
			echo "API_URL=$(DEV_BACKEND_URL)"; \
			echo "FRONTEND_URL=$(DEV_FRONTEND_URL)"; \
			echo "AUTH_FRONTEND_URL=$(DEV_AUTH_FRONTEND_URL)"; \
			echo "SUPERTOKENS_CORE_URL=$(DEV_SUPERTOKENS_URL)"; \
			echo "DATABASE_URL=$(DEV_DATABASE_URL)"; \
			echo "REDIS_URL=$(DEV_REDIS_URL)"; \
			printf 'CORS_ORIGINS=["http://localhost:%s","http://127.0.0.1:%s"]\n' "$(DEV_FRONTEND_PORT)" "$(DEV_FRONTEND_PORT)"; \
			echo 'CORS_ORIGIN_REGEX=https?://(localhost|127\.0\.0\.\d+|127\.\d+\.\d+\.\d+|127-0-0-\d+\.sslip\.io|[\w-]+\.nip\.io)(:\d+)?'; \
			echo "# Added by make init (AgentBox manager)"; \
			echo "AGENTBOX_API_URL=$(DEV_AGENTBOX_URL)"; \
			echo "AGENTBOX_API_KEY=$(DEV_AGENTBOX_API_KEY)"; \
		} >> $(BACKEND_DIR)/.env; \
	fi

_init-frontend-env:
	@if [ ! -f $(FRONTEND_DIR)/.env.local ]; then \
		echo "  Creating $(FRONTEND_DIR)/.env.local …"; \
		set -e; \
		{ \
			echo "# Lemma frontend — local dev defaults (generated by make init)."; \
			echo "# Kept in sync with the canonical ports at the top of the Makefile."; \
			echo "NEXT_PUBLIC_API_URL=$(DEV_BACKEND_URL)"; \
			echo "NEXT_PUBLIC_SITE_URL=$(DEV_FRONTEND_URL)"; \
			echo "NEXT_PUBLIC_AUTH_URL=$(DEV_FRONTEND_URL)"; \
		} > $(FRONTEND_DIR)/.env.local; \
		cd $(FRONTEND_DIR) && npm run gen:runtime-config --silent; \
	else \
		$(MAKE) --no-print-directory _ensure-frontend-env-keys; \
	fi

_ensure-frontend-env-keys:
	@set -e; missing=""; \
	for k in NEXT_PUBLIC_API_URL NEXT_PUBLIC_SITE_URL NEXT_PUBLIC_AUTH_URL; do \
		if ! grep -qE "^$$k=" $(FRONTEND_DIR)/.env.local; then missing="$$missing $$k"; fi; \
	done; \
	if [ -z "$$missing" ]; then \
		echo "  $(FRONTEND_DIR)/.env.local already exists with all required keys"; \
	else \
		echo "  $(FRONTEND_DIR)/.env.local missing keys ($$missing) — appending…"; \
		{ \
			echo ""; \
			echo "# Added by make init"; \
			echo "NEXT_PUBLIC_API_URL=$(DEV_BACKEND_URL)"; \
			echo "NEXT_PUBLIC_SITE_URL=$(DEV_FRONTEND_URL)"; \
			echo "NEXT_PUBLIC_AUTH_URL=$(DEV_FRONTEND_URL)"; \
		} >> $(FRONTEND_DIR)/.env.local; \
		cd $(FRONTEND_DIR) && npm run gen:runtime-config --silent; \
	fi

# ── Dev stack ─────────────────────────────────────────────────────────────────

dev:
	@echo "→ Starting Lemma dev stack…"
	@$(MAKE) --no-print-directory stop 2>/dev/null || true
	@$(MAKE) --no-print-directory _ensure-init
	@$(MAKE) --no-print-directory _infra-up
	@$(MAKE) --no-print-directory _wait-infra
	@echo ""
	@echo "  Frontend  →  $(DEV_FRONTEND_URL)"
	@echo "  Auth UI   →  $(DEV_AUTH_FRONTEND_URL)"
	@echo "  API       →  $(DEV_BACKEND_URL)"
	@echo "  API docs  →  $(DEV_BACKEND_URL)/scalar"
	@echo "  AgentBox  →  $(DEV_AGENTBOX_URL)"
	@echo ""
	@echo "  Tail backend logs : make logs"
	@echo "  Press Ctrl-C or run 'make stop' to stop."
	@echo ""
	@# Launch all three dev servers and wait in ONE shell. Make runs each recipe
	@# line in its own shell, so backgrounding with `&` on separate lines orphans
	@# the jobs and a `wait` on the next line returns immediately (make dev would
	@# exit while the servers kept running detached). Keeping the launches + wait
	@# in a single backslash-joined line fixes that; the trap turns Ctrl-C into a
	@# clean `make stop` of every server and port.
	@trap '$(MAKE) --no-print-directory stop; exit 0' INT TERM; \
		$(MAKE) --no-print-directory _run-agentbox & \
		$(MAKE) --no-print-directory _wait-agentbox; \
		$(MAKE) --no-print-directory _run-backend & \
		$(MAKE) --no-print-directory _run-frontend & \
		wait

_ensure-init:
	@test -f $(BACKEND_DIR)/.env  || { echo "  ! $(BACKEND_DIR)/.env missing — run 'make init'"; exit 1; }
	@test -f $(FRONTEND_DIR)/.env.local || { echo "  ! $(FRONTEND_DIR)/.env.local missing — run 'make init'"; exit 1; }
	@test -f $(TS_DIR)/dist/index.js || { echo "  ! $(TS_DIR)/dist missing — run 'make init' (or cd $(TS_DIR) && npm run build)"; exit 1; }
	@$(MAKE) --no-print-directory _ensure-backend-env-keys
	@echo "  Using $(BACKEND_DIR)/.env + $(FRONTEND_DIR)/.env.local"

_infra-up:
	@echo "  Starting infra (postgres, redis, supertokens, kreuzberg)…"
	@cd $(BACKEND_DIR) && rm -f $(INFRA_PID_FILE) && $(COMMON_DEV_ENV) docker compose up -d --quiet-pull 2>&1 | grep -v "^$$" || true

_wait-infra:
	@echo "  Waiting for postgres on localhost:$(DEV_POSTGRES_PORT)…"
	@cd $(BACKEND_DIR) && \
		for i in $$(seq 1 30); do \
			pg_isready -h localhost -p $(DEV_POSTGRES_PORT) -q 2>/dev/null && echo "  ✓ Postgres ready" && break; \
			sleep 1; \
		done

_run-backend:
	@echo "  Starting backend ($(DEV_BACKEND_URL))…"
	@mkdir -p $(BACKEND_DIR)
	@cd $(BACKEND_DIR) && rm -f $(notdir $(BACKEND_PID_FILE)) && \
		$(COMMON_DEV_ENV) \
		bash -c "if [ '$(RELOAD)' = '1' ]; then \
			uv run uvicorn standalone_app:app --host 0.0.0.0 --port $(DEV_BACKEND_PORT) --reload & echo \$$! > $(notdir $(BACKEND_PID_FILE)); \
		else \
			uv run uvicorn standalone_app:app --host 0.0.0.0 --port $(DEV_BACKEND_PORT) & echo \$$! > $(notdir $(BACKEND_PID_FILE)); \
		fi; wait"

_run-frontend:
	@echo "  Starting frontend ($(DEV_FRONTEND_URL))…"
	@mkdir -p $(FRONTEND_DIR)
	@cd $(FRONTEND_DIR) && rm -f $(notdir $(FRONTEND_PID_FILE)) && \
		$(COMMON_DEV_ENV) $(FRONTEND_DEV_ENV) \
		bash -c "npm run dev -- --port $(DEV_FRONTEND_PORT) & echo \$$! > $(notdir $(FRONTEND_PID_FILE)); wait"

_run-agentbox:
	@echo "  Starting agentbox manager ($(DEV_AGENTBOX_URL), provider=docker)…"
	@mkdir -p $(AGENTBOX_DIR)
	@cd $(AGENTBOX_DIR) && rm -f $(notdir $(AGENTBOX_PID_FILE)) && \
		$(AGENTBOX_DEV_ENV) \
		bash -c "uv run uvicorn agentbox.server:app --host 127.0.0.1 --port $(DEV_AGENTBOX_PORT) & echo \$$! > $(notdir $(AGENTBOX_PID_FILE)); wait"

_wait-agentbox:
	@echo "  Waiting for agentbox manager on $(DEV_AGENTBOX_URL)…"
	@for i in $$(seq 1 30); do \
		curl -fsS $(DEV_AGENTBOX_URL)/health >/dev/null 2>&1 && echo "  ✓ AgentBox ready" && break; \
		sleep 1; \
	done

stop:
	@echo "→ Stopping dev processes…"
	@for p in $(FRONTEND_PID_FILE) $(BACKEND_PID_FILE) $(AGENTBOX_PID_FILE); do \
		if [ -f $$p ]; then \
			pid=$$(cat $$p); \
			kill $$pid 2>/dev/null && echo "  Stopped $$pid ($$p)" || true; \
			rm -f $$p; \
		fi; \
	done
	@# belt + braces: anything still listening on the dev ports
	@for port in $(DEV_FRONTEND_PORT) $(DEV_BACKEND_PORT) $(DEV_AGENTBOX_PORT); do \
		lsof -ti tcp:$$port 2>/dev/null | xargs -r kill 2>/dev/null && echo "  Killed leftovers on port $$port" || true; \
	done

stop-all: stop
	@echo "→ Stopping infra containers…"
	@cd $(BACKEND_DIR) && $(COMMON_DEV_ENV) docker compose down

logs:
	@cd $(BACKEND_DIR) && docker compose logs -f

# ── Tests ─────────────────────────────────────────────────────────────────────

test: test-backend-unit test-backend-e2e test-cli test-python test-frontend
	@echo ""
	@echo "✓ All test suites complete."

test-backend:
	$(MAKE) test-backend-unit test-backend-e2e

test-backend-unit:
	@echo "→ Backend unit tests…"
	@cd $(BACKEND_DIR) && uv run pytest -m "not e2e" -q

test-backend-e2e:
	@echo "→ Backend e2e tests (workers=$(E2E_WORKERS))…"
	@cd $(BACKEND_DIR) && uv run pytest \
		-n $(E2E_WORKERS) --dist loadscope \
		-m "e2e and not slow and not worker and not workspace and not provider and not local_cli" \
		-q

test-backend-e2e-full:
	@echo "→ Backend full e2e suite (including slow/runtime)…"
	@cd $(BACKEND_DIR) && uv run pytest -m e2e -q

test-frontend:
	@echo "→ Frontend tests…"
	@cd $(FRONTEND_DIR) && npm test

# lemma-cli: unit tests use fake SDK clients (no network/docker); e2e tests spin
# up the real backend + docker infra (postgres/redis/supertokens) and drive the
# CLI over TCP. `test-cli` runs both; use the split targets for faster loops.
test-cli: test-cli-unit test-cli-e2e
	@echo ""
	@echo "✓ lemma-cli unit + e2e tests complete."

test-cli-unit:
	@echo "→ lemma-cli unit tests…"
	@cd $(CLI_DIR) && uv run pytest -m "not e2e" -q

test-cli-e2e:
	@echo "→ lemma-cli e2e tests (real backend + docker)…"
	@cd $(CLI_DIR) && uv run pytest -m e2e -q

test-python:
	@echo "→ lemma-python SDK tests (non-integration)…"
	@cd $(PYTHON_DIR) && uv run --with pytest pytest tests/ -m "not integration" -q

# ── Coverage ──────────────────────────────────────────────────────────────────

coverage: coverage-backend-unit coverage-backend-e2e coverage-cli coverage-frontend
	@echo ""
	@echo "✓ Coverage reports written:"
	@echo "    $(BACKEND_DIR)/coverage-unit.xml"
	@echo "    $(BACKEND_DIR)/coverage-e2e.xml"

coverage-backend: coverage-backend-unit coverage-backend-e2e

coverage-backend-unit:
	@echo "→ Backend unit coverage…"
	@cd $(BACKEND_DIR) && uv run pytest -m "not e2e" \
		--cov=app --cov-report=term-missing --cov-report=xml:coverage-unit.xml -q

coverage-backend-e2e:
	@echo "→ Backend e2e coverage (workers=$(E2E_WORKERS))…"
	@cd $(BACKEND_DIR) && uv run pytest \
		-n $(E2E_WORKERS) --dist loadscope \
		-m "e2e and not slow and not worker and not workspace and not provider and not local_cli" \
		--cov=app --cov-report=term-missing --cov-report=xml:coverage-e2e.xml -q

coverage-backend-module:
	@test -n "$(MODULE)" || (echo "MODULE is required, e.g. make coverage-backend-module MODULE=agent"; exit 1)
	@echo "→ Backend module coverage: $(MODULE)…"
	@cd $(BACKEND_DIR) && uv run pytest app/modules/$(MODULE) \
		--cov=app/modules/$(MODULE) --cov-report=term-missing --cov-fail-under=0 -q

coverage-cli: coverage-cli-unit
	@echo ""
	@echo "✓ lemma-cli coverage complete."

coverage-cli-unit:
	@echo "→ lemma-cli unit coverage…"
	@cd $(CLI_DIR) && uv run --with pytest-cov pytest -m "not e2e" \
		--cov=lemma_cli --cov-report=term-missing -q

coverage-cli-e2e:
	@echo "→ lemma-cli e2e coverage (real backend + docker)…"
	@cd $(CLI_DIR) && uv run --with pytest-cov pytest -m e2e \
		--cov=lemma_cli --cov-report=term-missing -q

coverage-frontend:
	@echo "→ Frontend coverage…"
	@cd $(FRONTEND_DIR) && npx vitest run --coverage 2>/dev/null || \
		(echo "  Install @vitest/coverage-v8: npm install -D @vitest/coverage-v8"; exit 1)

# ── Lint ──────────────────────────────────────────────────────────────────────

lint:
	@echo "→ Backend (ruff)…"
	@cd $(BACKEND_DIR) && uv run ruff check . --quiet
	@echo "→ CLI (ruff)…"
	@cd $(CLI_DIR) && uv run ruff check . --quiet 2>/dev/null || true
	@echo "→ Python SDK (ruff)…"
	@cd $(PYTHON_DIR) && uv run ruff check . --quiet 2>/dev/null || true
	@echo "→ Frontend (eslint)…"
	@cd $(FRONTEND_DIR) && npm run lint --silent 2>/dev/null || true

# ── Migrations ────────────────────────────────────────────────────────────────

migrate:
	@echo "→ Applying database migrations…"
	@cd $(BACKEND_DIR) && uv run alembic upgrade head
