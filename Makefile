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

PID_FILE      := .dev-pids

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
	@echo "→ Creating .env files (skipped if already present)…"
	@$(MAKE) --no-print-directory _init-backend-env
	@$(MAKE) --no-print-directory _init-frontend-env
	@echo ""
	@echo "→ Installing dependencies…"
	@cd $(BACKEND_DIR) && uv sync --quiet
	@cd $(CLI_DIR) && uv sync --quiet
	@cd $(PYTHON_DIR) && uv sync --quiet
	@cd $(TS_DIR) && npm install --silent
	@cd $(FRONTEND_DIR) && npm install --silent
	@echo "  ✓ Dependencies installed"
	@echo ""
	@echo "Done. Run 'make dev' to start the stack."

_init-backend-env:
	@if [ ! -f $(BACKEND_DIR)/.env ]; then \
		echo "  Creating $(BACKEND_DIR)/.env …"; \
		cat > $(BACKEND_DIR)/.env <<'EOF'; \
# Lemma backend — local dev defaults \
# Required: set at least one model provider \
LEMMA_DEFAULT_MODEL_TYPE=openai_compat \
LEMMA_OPENAI_API_KEY= \
LEMMA_OPENAI_BASE_URL=https://api.openai.com/v1 \
LEMMA_OPENAI_DEFAULT_MODEL=gpt-4o \
LEMMA_OPENAI_MODEL_NAMES=gpt-4o,gpt-4o-mini \
# Uncomment for Anthropic instead: \
# LEMMA_DEFAULT_MODEL_TYPE=anthropic_compat \
# LEMMA_ANTHROPIC_API_KEY= \
# LEMMA_ANTHROPIC_DEFAULT_MODEL=claude-sonnet-4-5 \
EOF \
	else \
		echo "  $(BACKEND_DIR)/.env already exists — skipping"; \
	fi

_init-frontend-env:
	@if [ ! -f $(FRONTEND_DIR)/.env.local ]; then \
		echo "  Creating $(FRONTEND_DIR)/.env.local …"; \
		cp $(FRONTEND_DIR)/.env.example $(FRONTEND_DIR)/.env.local 2>/dev/null || \
		printf 'NEXT_PUBLIC_API_URL=http://localhost:8710\nNEXT_PUBLIC_SITE_URL=http://localhost:3710\nNEXT_PUBLIC_AUTH_URL=http://localhost:3710\n' \
			> $(FRONTEND_DIR)/.env.local; \
	else \
		echo "  $(FRONTEND_DIR)/.env.local already exists — skipping"; \
	fi

# ── Dev stack ─────────────────────────────────────────────────────────────────

dev:
	@echo "→ Starting Lemma dev stack…"
	@$(MAKE) --no-print-directory _infra-up
	@$(MAKE) --no-print-directory _wait-infra
	@$(MAKE) --no-print-directory _run-backend &
	@$(MAKE) --no-print-directory _run-frontend &
	@echo ""
	@echo "  Frontend  →  http://localhost:3710"
	@echo "  API       →  http://localhost:8710"
	@echo "  API docs  →  http://localhost:8710/scalar"
	@echo ""
	@echo "  Press Ctrl-C or run 'make stop' to stop."
	@wait

_infra-up:
	@echo "  Starting infra (postgres, redis, supertokens, kreuzberg)…"
	@cd $(BACKEND_DIR) && docker compose up -d --quiet-pull 2>&1 | grep -v "^$$" || true

_wait-infra:
	@echo "  Waiting for postgres…"
	@cd $(BACKEND_DIR) && \
		for i in $$(seq 1 30); do \
			docker compose exec db pg_isready -q 2>/dev/null && echo "  ✓ Postgres ready" && break; \
			sleep 1; \
		done

_run-backend:
	@echo "  Starting backend (port 8710)…"
	@mkdir -p $(dir $(PID_FILE))
	@if [ "$(RELOAD)" = "1" ]; then \
		cd $(BACKEND_DIR) && uv run uvicorn app.standalone:app --host 0.0.0.0 --port 8710 --reload & echo $$! >> ../$(PID_FILE); \
	else \
		cd $(BACKEND_DIR) && uv run uvicorn app.standalone:app --host 0.0.0.0 --port 8710 & echo $$! >> ../$(PID_FILE); \
	fi

_run-frontend:
	@echo "  Starting frontend (port 3710)…"
	@cd $(FRONTEND_DIR) && npm run dev -- --port 3710 & echo $$! >> ../$(PID_FILE)

stop:
	@if [ -f $(PID_FILE) ]; then \
		echo "→ Stopping dev processes…"; \
		while IFS= read -r pid; do \
			kill "$$pid" 2>/dev/null && echo "  Stopped $$pid" || true; \
		done < $(PID_FILE); \
		rm -f $(PID_FILE); \
	else \
		echo "No .dev-pids file found — nothing to stop."; \
	fi

stop-all: stop
	@echo "→ Stopping infra containers…"
	@cd $(BACKEND_DIR) && docker compose down

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
