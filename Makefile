SHELL := /bin/bash

# ──────────────────────────────────────────────────────────────────────────────
# Lemma Platform — root developer workflow
#
#   make init          create .env files with local defaults (idempotent)
#   make dev           start infra + backend + frontend (hot-reload)
#   make dev-public    same, with an ephemeral public Cloudflare API URL
#   make dev RELOAD=1  same, with uvicorn --reload on the backend
#   make stop          stop backend/frontend processes
#   make stop-all      also stop infra containers
#   make test          run all component test suites
#   make coverage      full coverage report (unit + e2e per component)
# ──────────────────────────────────────────────────────────────────────────────

.PHONY: help init dev dev-public stop stop-all logs otel-up otel-down otel-tail otel-smoke \
        _prepare-dev _start-public-api-tunnel _ensure-databases _ensure-agentbox-images \
        test-dev-workflow \
        test test-backend test-backend-unit test-backend-e2e \
        test-frontend test-cli test-cli-unit test-cli-e2e test-python \
        coverage coverage-backend coverage-backend-unit coverage-backend-e2e \
        coverage-backend-module coverage-cli coverage-cli-unit coverage-cli-e2e coverage-frontend \
        lint migrate

# ── Configuration ─────────────────────────────────────────────────────────────

RELOAD        ?= 0
E2E_WORKERS   ?= 2
MODULE        ?=
OTEL          ?= 0
OTEL_LOGS     ?= 0
LLM_OTEL      ?= 0

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
# Kept only so `make stop` can clean up a manager left by an older checkout.
LEGACY_AGENTBOX_PID_FILE := $(AGENTBOX_DIR)/.dev-agentbox.pid
CLOUDFLARED_API_PID_FILE := .dev-cloudflared-api.pid

DEV_LOG_DIR                  := .dev-logs
CLOUDFLARED_API_LOG_FILE     := $(abspath $(DEV_LOG_DIR)/cloudflared-api.log)
CLOUDFLARED_CONFIG_FILE      := $(abspath $(DEV_LOG_DIR)/cloudflared-quick-tunnel.yml)
PUBLIC_API_URL_FILE          := $(abspath $(DEV_LOG_DIR)/public-api-url)
AGENTBOX_READY_TIMEOUT       ?= 30
PUBLIC_TUNNEL_READY_TIMEOUT  ?= 30

# ── Canonical dev ports + URLs ───────────────────────────────────────────────
# These are the SINGLE source of truth for the dev stack. Infra (docker
# compose), backend settings (API_URL / FRONTEND_URL / DATABASE_URL / …) and
# the frontend (NEXT_PUBLIC_* + runtime-config.js) all derive from these.
# Change one number here and the whole stack stays consistent. Picked to
# differ from the installed lemma-stack defaults (3700/8700/4173/5432/…)
# so a fresh platform checkout can sit alongside an installed copy.

DEV_BACKEND_PORT      ?= 8710
DEV_FRONTEND_PORT     ?= 3710
DEV_POSTGRES_PORT     ?= 5432
DEV_REDIS_PORT        ?= 6379
DEV_SUPERTOKENS_PORT  ?= 3567

DEV_BACKEND_URL       := http://localhost:$(DEV_BACKEND_PORT)
DEV_FRONTEND_URL      := http://localhost:$(DEV_FRONTEND_PORT)
DEV_AUTH_FRONTEND_URL := $(DEV_FRONTEND_URL)
DEV_DATABASE_URL      := postgresql+asyncpg://postgres:postgres@localhost:$(DEV_POSTGRES_PORT)/lemma
DEV_DATASTORE_DATABASE_URL := postgresql+asyncpg://postgres:postgres@localhost:$(DEV_POSTGRES_PORT)/lemma_datastore
DEV_AGENTBOX_DATABASE_URL  := postgresql+psycopg://postgres:postgres@localhost:$(DEV_POSTGRES_PORT)/agentbox
DEV_REDIS_URL         := redis://localhost:$(DEV_REDIS_PORT)/0
DEV_SUPERTOKENS_URL   := http://localhost:$(DEV_SUPERTOKENS_PORT)
DEV_AGENTBOX_URL      := http://127.0.0.1:$(DEV_BACKEND_PORT)/internal/agentbox
DEV_SANDBOX_BACKEND_URL := http://host.lemma.internal:$(DEV_BACKEND_PORT)
DEV_SANDBOX_FRONTEND_URL := http://host.lemma.internal:$(DEV_FRONTEND_PORT)
DEV_AGENTBOX_API_KEY  ?= dev-agentbox-key
DEV_AGENTBOX_RUNTIME_CREDENTIAL_KEY ?= dev-agentbox-runtime-credential-key-0001
DEV_CORS_ORIGIN_REGEX := https?://(localhost|127\.0\.0\.\d+|127\.\d+\.\d+\.\d+|127-0-0-\d+\.sslip\.io|[\w-]+\.nip\.io)(:\d+)?
DEV_LOG_LEVEL         ?= DEBUG
DEV_JSON_LOGS_ENABLED ?= true
OTEL_DEBUG_GRPC_PORT  ?= 14317
OTEL_DEBUG_LLM_GRPC_PORT ?= 15317
OTEL_DEBUG_HEALTH_PORT ?= 14333

OTEL_DEBUG_COMPOSE_ENV := \
	OTEL_DEBUG_GRPC_PORT=$(OTEL_DEBUG_GRPC_PORT) \
	OTEL_DEBUG_LLM_GRPC_PORT=$(OTEL_DEBUG_LLM_GRPC_PORT) \
	OTEL_DEBUG_HEALTH_PORT=$(OTEL_DEBUG_HEALTH_PORT)

OTEL_DEV_ENV := \
	OBSERVABILITY_ENABLED=$(if $(filter 1,$(OTEL)),true,false) \
	OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:$(OTEL_DEBUG_GRPC_PORT) \
	OTEL_EXPORTER_OTLP_PROTOCOL=grpc \
	OTEL_TRACES_EXPORTER=otlp \
	OTEL_METRICS_EXPORTER=otlp \
	OTEL_LOGS_EXPORTER=$(if $(filter 1,$(OTEL_LOGS)),otlp,none) \
	OTEL_TRACES_SAMPLER=always_on \
	OTEL_METRIC_EXPORT_INTERVAL=5000

LLM_OTEL_DEV_ENV := \
	LLM_OTEL_ENABLED=$(if $(filter 1,$(LLM_OTEL)),true,false) \
	LLM_OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:$(OTEL_DEBUG_LLM_GRPC_PORT) \
	LLM_OTEL_EXPORTER_OTLP_PROTOCOL=grpc \
	LLM_OTEL_TRACES_SAMPLER=always_on
# Immutable-profile inputs used by the local Docker provider.
AGENTBOX_WORKSPACE_IMAGE ?= agentbox-workspace:dev
AGENTBOX_FUNCTION_IMAGE ?= agentbox-function:dev

COMMON_DEV_ENV := \
	DEV_POSTGRES_PORT=$(DEV_POSTGRES_PORT) \
	DEV_REDIS_PORT=$(DEV_REDIS_PORT) \
	DEV_REDIS_UI_PORT=8001 \
	DEV_SUPERTOKENS_PORT=$(DEV_SUPERTOKENS_PORT)

BACKEND_API_URL                 ?= $(DEV_BACKEND_URL)
BACKEND_FRONTEND_URL            ?= $(DEV_FRONTEND_URL)
BACKEND_AUTH_FRONTEND_URL       ?= $(DEV_AUTH_FRONTEND_URL)
BACKEND_CLI_API_URL             ?= $(DEV_BACKEND_URL)
BACKEND_CLI_AUTH_FRONTEND_URL   ?= $(DEV_AUTH_FRONTEND_URL)
BACKEND_WORKSPACE_CALLBACK_API_URL ?= $(DEV_SANDBOX_BACKEND_URL)
BACKEND_WORKSPACE_CALLBACK_AUTH_URL ?= $(DEV_SANDBOX_FRONTEND_URL)
BACKEND_WORKSPACE_CALLBACK_FRONTEND_URL ?= $(DEV_SANDBOX_FRONTEND_URL)
BACKEND_APP_BASE_DOMAIN         ?=
BACKEND_SESSION_COOKIE_DOMAIN   ?=
BACKEND_SESSION_COOKIE_SECURE   ?= false
BACKEND_SESSION_COOKIE_SAME_SITE?= lax
BACKEND_CORS_ORIGINS            ?= ["http://localhost:$(DEV_FRONTEND_PORT)","http://127.0.0.1:$(DEV_FRONTEND_PORT)"]
BACKEND_CORS_ORIGIN_REGEX       ?= $(DEV_CORS_ORIGIN_REGEX)
BACKEND_TELEGRAM_POLLING        ?= true
BACKEND_SLACK_SOCKET_MODE       ?= true

BACKEND_DEV_ENV := \
	ENVIRONMENT=local \
	DEBUG=true \
	LOG_LEVEL=$(DEV_LOG_LEVEL) \
	JSON_LOGS_ENABLED=$(DEV_JSON_LOGS_ENABLED) \
	API_URL=$(BACKEND_API_URL) \
	FRONTEND_URL=$(BACKEND_FRONTEND_URL) \
	AUTH_FRONTEND_URL=$(BACKEND_AUTH_FRONTEND_URL) \
	CLI_API_URL=$(BACKEND_CLI_API_URL) \
	CLI_AUTH_FRONTEND_URL=$(BACKEND_CLI_AUTH_FRONTEND_URL) \
	WORKSPACE_CALLBACK_API_URL=$(BACKEND_WORKSPACE_CALLBACK_API_URL) \
	WORKSPACE_CALLBACK_AUTH_URL=$(BACKEND_WORKSPACE_CALLBACK_AUTH_URL) \
	WORKSPACE_CALLBACK_FRONTEND_URL=$(BACKEND_WORKSPACE_CALLBACK_FRONTEND_URL) \
	AUTH_WEBSITE_BASE_PATH=/auth \
	SUPERTOKENS_API_BASE_PATH=/auth \
	SUPERTOKENS_API_GATEWAY_PATH=/st \
	SUPERTOKENS_CORE_URL=$(DEV_SUPERTOKENS_URL) \
	DATABASE_URL=$(DEV_DATABASE_URL) \
	DATASTORE_DATABASE_URL=$(DEV_DATASTORE_DATABASE_URL) \
	REDIS_URL=$(DEV_REDIS_URL) \
	KREUZBERG_URL= \
	DOCUMENT_PROCESSOR=markitdown \
	STORAGE_BACKEND=local \
	LOCAL_OBJECT_STORAGE_ROOT=$(abspath .local/object-storage) \
	LOCAL_FILE_STORAGE_ROOT=$(abspath .local/files) \
	EMAIL_TRANSPORT=filesystem \
	EMAIL_OUTPUT_DIR=$(abspath .local/emails) \
	AUTH_EMAIL_VERIFICATION_REQUIRED=false \
	ENABLE_TELEGRAM_POLLING_MODE=$(BACKEND_TELEGRAM_POLLING) \
	ENABLE_SLACK_SOCKET_MODE=$(BACKEND_SLACK_SOCKET_MODE) \
	APP_BASE_DOMAIN=$(BACKEND_APP_BASE_DOMAIN) \
	SESSION_COOKIE_DOMAIN=$(BACKEND_SESSION_COOKIE_DOMAIN) \
	SESSION_COOKIE_SECURE=$(BACKEND_SESSION_COOKIE_SECURE) \
	SESSION_COOKIE_SAME_SITE=$(BACKEND_SESSION_COOKIE_SAME_SITE) \
	CORS_ORIGINS='$(BACKEND_CORS_ORIGINS)' \
	CORS_ORIGIN_REGEX='$(BACKEND_CORS_ORIGIN_REGEX)'

FRONTEND_API_URL              ?= $(DEV_BACKEND_URL)
FRONTEND_SITE_URL             ?= $(DEV_FRONTEND_URL)
FRONTEND_AUTH_URL             ?= $(DEV_AUTH_FRONTEND_URL)
FRONTEND_SESSION_TOKEN_DOMAIN ?=
FRONTEND_APPS_DOMAIN_SUFFIX   ?=

FRONTEND_DEV_ENV := \
	NEXT_PUBLIC_API_URL=$(FRONTEND_API_URL) \
	NEXT_PUBLIC_SITE_URL=$(FRONTEND_SITE_URL) \
	NEXT_PUBLIC_AUTH_URL=$(FRONTEND_AUTH_URL) \
	NEXT_PUBLIC_SESSION_TOKEN_DOMAIN=$(FRONTEND_SESSION_TOKEN_DOMAIN) \
	NEXT_PUBLIC_APPS_DOMAIN_SUFFIX=$(FRONTEND_APPS_DOMAIN_SUFFIX)

AGENTBOX_ENV_FILE := $(AGENTBOX_DIR)/.env

# AgentBox manager — embedded in the all-in-one local backend and mounted at
# /internal/agentbox. The Docker provider remains the transitional developer
# sandbox runtime while desktop-native providers are introduced.
AGENTBOX_DEV_ENV := \
	AGENTBOX_ENVIRONMENT=local \
	AGENTBOX_LOG_LEVEL=$(DEV_LOG_LEVEL) \
	AGENTBOX_PROVIDER=docker \
	AGENTBOX_API_KEY=$(DEV_AGENTBOX_API_KEY) \
	AGENTBOX_RUNTIME_CREDENTIAL_KEY=$(DEV_AGENTBOX_RUNTIME_CREDENTIAL_KEY) \
	AGENTBOX_API_URL=$(DEV_AGENTBOX_URL) \
	AGENTBOX_PUBLIC_URL=$(DEV_AGENTBOX_URL) \
	AGENTBOX_WORKSPACE_IMAGE=$(AGENTBOX_WORKSPACE_IMAGE) \
	AGENTBOX_FUNCTION_IMAGE=$(AGENTBOX_FUNCTION_IMAGE) \
	AGENTBOX_STATE_DATABASE_URL=$(DEV_AGENTBOX_DATABASE_URL) \
	AGENTBOX_AUTO_CREATE_SCHEMA=true \
	AGENTBOX_DOCKER_SOCKET_PATH=/var/run/docker.sock \
	AGENTBOX_DOCKER_SCOPE=docker:development \
	AGENTBOX_DOCKER_ALLOW_MUTABLE_IMAGES=true \
	AGENTBOX_DOCKER_PRIVATE_NETWORK= \
	AGENTBOX_ADD_HOST_GATEWAY=true \
	AGENTBOX_HOST_ALIAS=host.lemma.internal \
	AGENTBOX_WORKSPACE_IDLE_SECONDS=300 \
	AGENTBOX_FUNCTION_IDLE_SECONDS=300 \
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
	@echo "    make dev-public         start with an ephemeral public API tunnel"
	@echo "    make dev RELOAD=1       same, with uvicorn --reload on the backend"
	@echo "    make stop               stop app and tunnel processes"
	@echo "    make stop-all           also bring down infra containers"
	@echo "    make logs               tail infrastructure container logs"
	@echo "    make dev OTEL=1         enable local OTLP traces + metrics"
	@echo "    make otel-smoke         verify traces, metrics, logs, and LLM isolation"
	@echo ""
	@echo "  Tests"
	@echo "    make test-dev-workflow  test generated dev config and startup diagnostics"
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
	@command -v docker >/dev/null 2>&1 || \
		(echo "  ✗ Docker with Compose is required — install Docker Desktop"; exit 1)
	@docker compose version >/dev/null 2>&1 || \
		(echo "  ✗ Docker Compose v2 is required — install/update Docker Desktop"; exit 1)
	@command -v node >/dev/null 2>&1 || (echo "  ✗ Node.js not found — install from https://nodejs.org/"; exit 1)
	@command -v openssl >/dev/null 2>&1 || (echo "  ✗ openssl not found — required to generate the local AgentBox state key"; exit 1)
	@echo "  ✓ Prerequisites OK"
	@echo ""
	@echo "→ Installing dependencies…"
	@cd $(BACKEND_DIR) && uv sync --extra local --quiet
	@cd $(CLI_DIR) && uv sync --quiet
	@cd $(PYTHON_DIR) && uv sync --quiet
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
	@$(MAKE) --no-print-directory _init-agentbox-env
	@echo ""
	@$(MAKE) --no-print-directory _ensure-agentbox-images
	@echo ""
	@echo "Done. Run 'make dev' to start the stack."

_ensure-agentbox-images:
	@if docker image inspect "$(AGENTBOX_WORKSPACE_IMAGE)" >/dev/null 2>&1 \
		&& docker image inspect "$(AGENTBOX_FUNCTION_IMAGE)" >/dev/null 2>&1; then \
		echo "  ✓ AgentBox workspace/function images already present"; \
	else \
		echo "→ Building canonical AgentBox workspace/function images…"; \
		$(MAKE) -C agentbox build-test-images \
			TEST_WORKSPACE_IMAGE="$(AGENTBOX_WORKSPACE_IMAGE)" \
			TEST_FUNCTION_IMAGE="$(AGENTBOX_FUNCTION_IMAGE)"; \
	fi

_init-backend-env:
	@if [ ! -f $(BACKEND_DIR)/.env ]; then \
		echo "  Creating $(BACKEND_DIR)/.env …"; \
		set -e; \
		{ \
			echo "# Lemma backend — local dev defaults (generated by make init)"; \
			echo "# Stack-owned values are also injected by make dev so port overrides stay in sync."; \
			echo "ENVIRONMENT=local"; \
			echo "DEBUG=true"; \
			echo "LOG_LEVEL=$(DEV_LOG_LEVEL)"; \
			echo "JSON_LOGS_ENABLED=$(DEV_JSON_LOGS_ENABLED)"; \
			echo "API_URL=$(DEV_BACKEND_URL)"; \
			echo "FRONTEND_URL=$(DEV_FRONTEND_URL)"; \
			echo "AUTH_FRONTEND_URL=$(DEV_AUTH_FRONTEND_URL)"; \
			echo "CLI_API_URL=$(DEV_BACKEND_URL)"; \
			echo "CLI_AUTH_FRONTEND_URL=$(DEV_AUTH_FRONTEND_URL)"; \
			echo "AUTH_WEBSITE_BASE_PATH=/auth"; \
			echo "SUPERTOKENS_API_BASE_PATH=/auth"; \
			echo "SUPERTOKENS_API_GATEWAY_PATH=/st"; \
			echo "SUPERTOKENS_CORE_URL=$(DEV_SUPERTOKENS_URL)"; \
			echo "DATABASE_URL=$(DEV_DATABASE_URL)"; \
			echo "DATASTORE_DATABASE_URL=$(DEV_DATASTORE_DATABASE_URL)"; \
			echo "REDIS_URL=$(DEV_REDIS_URL)"; \
			echo "KREUZBERG_URL="; \
			echo "DOCUMENT_PROCESSOR=markitdown"; \
			echo "STORAGE_BACKEND=local"; \
			echo "LOCAL_OBJECT_STORAGE_ROOT=$(abspath .local/object-storage)"; \
			echo "LOCAL_FILE_STORAGE_ROOT=$(abspath .local/files)"; \
			echo "EMAIL_TRANSPORT=filesystem"; \
			echo "EMAIL_OUTPUT_DIR=$(abspath .local/emails)"; \
			echo "AUTH_EMAIL_VERIFICATION_REQUIRED=false"; \
			echo "ENABLE_TELEGRAM_POLLING_MODE=true"; \
			echo "ENABLE_SLACK_SOCKET_MODE=true"; \
			echo 'CORS_ORIGINS=["http://localhost:$(DEV_FRONTEND_PORT)","http://127.0.0.1:$(DEV_FRONTEND_PORT)"]'; \
			echo 'CORS_ORIGIN_REGEX=$(DEV_CORS_ORIGIN_REGEX)'; \
			echo "# AgentBox sandbox manager — embedded in the local backend"; \
			echo "AGENTBOX_API_URL=$(DEV_AGENTBOX_URL)"; \
			echo "AGENTBOX_API_KEY=$(DEV_AGENTBOX_API_KEY)"; \
			echo "# Model provider — set a key and the exact model names available to it."; \
			echo "LEMMA_DEFAULT_MODEL_TYPE=openai_compat"; \
			echo "LEMMA_OPENAI_API_KEY="; \
			echo "LEMMA_OPENAI_BASE_URL=https://api.openai.com/v1"; \
			echo "LEMMA_OPENAI_DEFAULT_MODEL="; \
			echo "LEMMA_OPENAI_MODEL_NAMES="; \
			echo "LEMMA_OPENAI_VISION_MODEL_NAMES="; \
			echo "# Uncomment for Anthropic instead:"; \
			echo "# LEMMA_DEFAULT_MODEL_TYPE=anthropic_compat"; \
			echo "# LEMMA_ANTHROPIC_API_KEY="; \
			echo "# LEMMA_ANTHROPIC_DEFAULT_MODEL="; \
			echo "# LEMMA_ANTHROPIC_MODEL_NAMES="; \
		} > $(BACKEND_DIR)/.env; \
	else \
		$(MAKE) --no-print-directory _ensure-backend-env-keys; \
	fi

_ensure-backend-env-keys:
	@set -e; missing=""; \
	for k in ENVIRONMENT DEBUG LOG_LEVEL JSON_LOGS_ENABLED API_URL FRONTEND_URL AUTH_FRONTEND_URL CLI_API_URL CLI_AUTH_FRONTEND_URL AUTH_WEBSITE_BASE_PATH SUPERTOKENS_API_BASE_PATH SUPERTOKENS_API_GATEWAY_PATH SUPERTOKENS_CORE_URL DATABASE_URL DATASTORE_DATABASE_URL REDIS_URL DOCUMENT_PROCESSOR STORAGE_BACKEND LOCAL_OBJECT_STORAGE_ROOT LOCAL_FILE_STORAGE_ROOT EMAIL_TRANSPORT EMAIL_OUTPUT_DIR AUTH_EMAIL_VERIFICATION_REQUIRED ENABLE_TELEGRAM_POLLING_MODE ENABLE_SLACK_SOCKET_MODE CORS_ORIGINS CORS_ORIGIN_REGEX AGENTBOX_API_URL AGENTBOX_API_KEY; do \
		if ! grep -qE "^$$k=" $(BACKEND_DIR)/.env; then missing="$$missing $$k"; fi; \
	done; \
	if [ -z "$$missing" ]; then \
		echo "  $(BACKEND_DIR)/.env already exists with all required keys"; \
	else \
		echo "  $(BACKEND_DIR)/.env missing keys ($$missing) — appending…"; \
		printf '\n# Added by make init (missing local stack settings only)\n' >> $(BACKEND_DIR)/.env; \
		append() { key="$$1"; value="$$2"; grep -qE "^$${key}=" $(BACKEND_DIR)/.env || printf '%s=%s\n' "$$key" "$$value" >> $(BACKEND_DIR)/.env; }; \
		append ENVIRONMENT local; \
		append DEBUG true; \
		append LOG_LEVEL $(DEV_LOG_LEVEL); \
		append JSON_LOGS_ENABLED $(DEV_JSON_LOGS_ENABLED); \
		append API_URL '$(DEV_BACKEND_URL)'; \
		append FRONTEND_URL '$(DEV_FRONTEND_URL)'; \
		append AUTH_FRONTEND_URL '$(DEV_AUTH_FRONTEND_URL)'; \
		append CLI_API_URL '$(DEV_BACKEND_URL)'; \
		append CLI_AUTH_FRONTEND_URL '$(DEV_AUTH_FRONTEND_URL)'; \
		append AUTH_WEBSITE_BASE_PATH /auth; \
		append SUPERTOKENS_API_BASE_PATH /auth; \
		append SUPERTOKENS_API_GATEWAY_PATH /st; \
		append SUPERTOKENS_CORE_URL '$(DEV_SUPERTOKENS_URL)'; \
		append DATABASE_URL '$(DEV_DATABASE_URL)'; \
		append DATASTORE_DATABASE_URL '$(DEV_DATASTORE_DATABASE_URL)'; \
		append REDIS_URL '$(DEV_REDIS_URL)'; \
		append DOCUMENT_PROCESSOR markitdown; \
		append STORAGE_BACKEND local; \
		append LOCAL_OBJECT_STORAGE_ROOT '$(abspath .local/object-storage)'; \
		append LOCAL_FILE_STORAGE_ROOT '$(abspath .local/files)'; \
		append EMAIL_TRANSPORT filesystem; \
		append EMAIL_OUTPUT_DIR '$(abspath .local/emails)'; \
		append AUTH_EMAIL_VERIFICATION_REQUIRED false; \
		append ENABLE_TELEGRAM_POLLING_MODE true; \
		append ENABLE_SLACK_SOCKET_MODE true; \
		append CORS_ORIGINS '["http://localhost:$(DEV_FRONTEND_PORT)","http://127.0.0.1:$(DEV_FRONTEND_PORT)"]'; \
		append CORS_ORIGIN_REGEX '$(DEV_CORS_ORIGIN_REGEX)'; \
		append AGENTBOX_API_URL '$(DEV_AGENTBOX_URL)'; \
		append AGENTBOX_API_KEY '$(DEV_AGENTBOX_API_KEY)'; \
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
			echo "NEXT_PUBLIC_AUTH_URL=$(DEV_AUTH_FRONTEND_URL)"; \
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
		printf '\n# Added by make init (missing local stack settings only)\n' >> $(FRONTEND_DIR)/.env.local; \
		append() { key="$$1"; value="$$2"; grep -qE "^$${key}=" $(FRONTEND_DIR)/.env.local || printf '%s=%s\n' "$$key" "$$value" >> $(FRONTEND_DIR)/.env.local; }; \
		append NEXT_PUBLIC_API_URL '$(DEV_BACKEND_URL)'; \
		append NEXT_PUBLIC_SITE_URL '$(DEV_FRONTEND_URL)'; \
		append NEXT_PUBLIC_AUTH_URL '$(DEV_AUTH_FRONTEND_URL)'; \
		cd $(FRONTEND_DIR) && npm run gen:runtime-config --silent; \
	fi

_init-agentbox-env:
	@if [ -f $(AGENTBOX_ENV_FILE) ]; then \
		echo "  $(AGENTBOX_ENV_FILE) already exists"; \
	else \
		echo "# AgentBox local overrides (generated by make init; gitignored)" > $(AGENTBOX_ENV_FILE); \
		echo "  Created $(AGENTBOX_ENV_FILE)"; \
	fi

# ── Dev stack ─────────────────────────────────────────────────────────────────

dev:
	@echo "→ Starting Lemma dev stack…"
	@$(MAKE) --no-print-directory _prepare-dev
	@echo ""
	@echo "  Frontend  →  $(DEV_FRONTEND_URL)"
	@echo "  Auth UI   →  $(DEV_AUTH_FRONTEND_URL)/auth"
	@echo "  API       →  $(DEV_BACKEND_URL)"
	@echo "  API docs  →  $(DEV_BACKEND_URL)/scalar"
	@echo "  AgentBox  →  $(DEV_AGENTBOX_URL)"
	@echo ""
	@echo "  Debug and safe request-access logs are enabled."
	@echo "  Press Ctrl-C or run 'make stop' to stop."
	@echo ""
	@# AgentBox is mounted inside the backend; its readiness check therefore also
	@# verifies that the unified application completed startup.
	@trap '$(MAKE) --no-print-directory stop; exit 0' INT TERM; \
		$(MAKE) --no-print-directory _run-backend & \
		$(MAKE) --no-print-directory _run-frontend & \
		$(MAKE) --no-print-directory _wait-agentbox || { \
			status=$$?; $(MAKE) --no-print-directory stop; wait 2>/dev/null || true; exit $$status; \
		}; \
		wait

dev-public:
	@echo "→ Starting Lemma dev stack with a public Cloudflare API URL…"
	@$(MAKE) --no-print-directory _prepare-dev
	@$(MAKE) --no-print-directory _start-public-api-tunnel || { $(MAKE) --no-print-directory stop; exit 1; }
	@public_api_url=$$(cat $(PUBLIC_API_URL_FILE)); \
	echo ""; \
	echo "  Frontend        →  $(DEV_FRONTEND_URL)"; \
	echo "  Auth UI         →  $(DEV_AUTH_FRONTEND_URL)/auth"; \
	echo "  Public API      →  $$public_api_url"; \
	echo "  Public API docs →  $$public_api_url/scalar"; \
	echo "  AgentBox        →  $(DEV_AGENTBOX_URL)"; \
	echo ""; \
	echo "  Webhook callbacks and generated links use the public API URL."; \
	echo "  Public URLs are ephemeral and change each time this command starts."; \
	echo "  Debug and safe request-access logs are enabled."; \
	echo "  Press Ctrl-C or run 'make stop' to stop."; \
	echo ""; \
	trap '$(MAKE) --no-print-directory stop; exit 0' INT TERM; \
	$(MAKE) --no-print-directory _run-backend \
		BACKEND_API_URL="$$public_api_url" \
		BACKEND_SESSION_COOKIE_DOMAIN= \
		BACKEND_SESSION_COOKIE_SECURE=true \
		BACKEND_SESSION_COOKIE_SAME_SITE=none \
		BACKEND_TELEGRAM_POLLING=false \
		BACKEND_SLACK_SOCKET_MODE=false & \
	$(MAKE) --no-print-directory _run-frontend \
		FRONTEND_API_URL="$$public_api_url" \
		FRONTEND_SESSION_TOKEN_DOMAIN= & \
	$(MAKE) --no-print-directory _wait-agentbox || { \
		status=$$?; $(MAKE) --no-print-directory stop; wait 2>/dev/null || true; exit $$status; \
	}; \
	wait

_prepare-dev:
	@$(MAKE) --no-print-directory stop 2>/dev/null || true
	@$(MAKE) --no-print-directory _ensure-init
	@$(MAKE) --no-print-directory _ensure-agentbox-images
	@$(MAKE) --no-print-directory _infra-up
	@$(MAKE) --no-print-directory _wait-infra
	@$(MAKE) --no-print-directory _ensure-databases
	@$(MAKE) --no-print-directory migrate
	@if [ "$(OTEL)" = "1" ]; then $(MAKE) --no-print-directory otel-up; fi

_start-public-api-tunnel:
	@command -v cloudflared >/dev/null 2>&1 || { \
		echo "  ✗ cloudflared is required for make dev-public"; \
		echo "    Install it from https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"; \
		exit 1; \
	}
	@mkdir -p $(DEV_LOG_DIR)
	@printf '{}\n' > $(CLOUDFLARED_CONFIG_FILE)
	@rm -f $(PUBLIC_API_URL_FILE) $(CLOUDFLARED_API_PID_FILE)
	@echo "  Starting ephemeral Cloudflare API tunnel…"
	@cloudflared tunnel --config $(CLOUDFLARED_CONFIG_FILE) --no-autoupdate --loglevel info --url http://127.0.0.1:$(DEV_BACKEND_PORT) > $(CLOUDFLARED_API_LOG_FILE) 2>&1 & echo $$! > $(CLOUDFLARED_API_PID_FILE)
	@ready=0; \
	for i in $$(seq 1 $(PUBLIC_TUNNEL_READY_TIMEOUT)); do \
		api_url=$$(grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' $(CLOUDFLARED_API_LOG_FILE) 2>/dev/null | grep -v '^https://api\.trycloudflare\.com$$' | tail -n 1); \
		api_pid=$$(cat $(CLOUDFLARED_API_PID_FILE) 2>/dev/null || true); \
		if [ -z "$$api_pid" ] || ! kill -0 "$$api_pid" 2>/dev/null; then \
			echo "  ✗ Cloudflare API tunnel exited before publishing a URL"; \
			echo "    Log: $(CLOUDFLARED_API_LOG_FILE)"; \
			tail -n 20 $(CLOUDFLARED_API_LOG_FILE) 2>/dev/null || true; exit 1; \
		fi; \
		if [ -n "$$api_url" ] && grep -q 'Registered tunnel connection' $(CLOUDFLARED_API_LOG_FILE); then \
			printf '%s\n' "$$api_url" > $(PUBLIC_API_URL_FILE); \
			ready=1; break; \
		fi; \
		sleep 1; \
	done; \
	if [ "$$ready" != "1" ]; then \
		echo "  ✗ Cloudflare API tunnel did not publish a URL within $(PUBLIC_TUNNEL_READY_TIMEOUT)s"; \
		echo "    Log: $(CLOUDFLARED_API_LOG_FILE)"; \
		exit 1; \
	fi; \
	echo "  ✓ Cloudflare API tunnel ready"

_ensure-init:
	@test -f $(BACKEND_DIR)/.env  || { echo "  ! $(BACKEND_DIR)/.env missing — run 'make init'"; exit 1; }
	@test -f $(FRONTEND_DIR)/.env.local || { echo "  ! $(FRONTEND_DIR)/.env.local missing — run 'make init'"; exit 1; }
	@test -f $(AGENTBOX_ENV_FILE) || { echo "  ! $(AGENTBOX_ENV_FILE) missing — run 'make init'"; exit 1; }
	@test -f $(TS_DIR)/dist/index.js || { echo "  ! $(TS_DIR)/dist missing — run 'make init' (or cd $(TS_DIR) && npm run build)"; exit 1; }
	@$(MAKE) --no-print-directory _ensure-backend-env-keys
	@$(MAKE) --no-print-directory _ensure-frontend-env-keys
	@cd $(BACKEND_DIR) && uv run --extra local python -c 'import agentbox, markitdown, psycopg, psycopg_pool' >/dev/null || { echo "  ! Local backend dependencies missing — run 'make init'"; exit 1; }
	@echo "  Using $(BACKEND_DIR)/.env + $(FRONTEND_DIR)/.env.local + $(AGENTBOX_ENV_FILE)"

_infra-up:
	@echo "  Starting infra (postgres, redis, supertokens)…"
	@cd $(BACKEND_DIR) && rm -f $(INFRA_PID_FILE) && $(COMMON_DEV_ENV) docker compose up -d --quiet-pull --remove-orphans db redis supertokens

_wait-infra:
	@echo "  Waiting for postgres on localhost:$(DEV_POSTGRES_PORT)…"
	@cd $(BACKEND_DIR) && ready=0; \
		for i in $$(seq 1 30); do \
			if docker compose exec -T db pg_isready -U postgres -q >/dev/null 2>&1; then ready=1; break; fi; \
			sleep 1; \
		done; \
		if [ "$$ready" != "1" ]; then \
			echo "  ✗ Postgres did not become ready within 30s"; \
			docker compose ps; docker compose logs --tail=30 db; exit 1; \
		fi; \
		echo "  ✓ Postgres ready"

_ensure-databases:
	@echo "  Ensuring extra databases (supertokens, lemma_datastore, agentbox) exist…"
	@cd $(BACKEND_DIR) && \
		for db in supertokens lemma_datastore agentbox; do \
			exists=$$(docker compose exec -T db psql -U postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '$$db'" 2>/dev/null | tr -d '[:space:]'); \
			if [ "$$exists" != "1" ]; then \
				echo "    Creating database $$db…"; \
				docker compose exec -T db psql -U postgres -c "CREATE DATABASE $$db" >/dev/null; \
				if [ "$$db" = "supertokens" ]; then \
					echo "    Restarting supertokens (picks up new database)…"; \
					docker compose restart supertokens >/dev/null; \
				fi; \
			fi; \
		done; \
		docker compose exec -T db psql -U postgres -d lemma -c "CREATE EXTENSION IF NOT EXISTS vector" >/dev/null; \
		docker compose exec -T db psql -U postgres -d lemma_datastore -c "CREATE EXTENSION IF NOT EXISTS vector" >/dev/null
	@echo "  ✓ Databases ready"

_run-backend:
	@echo "  Starting unified backend ($(BACKEND_API_URL), AgentBox=$(DEV_AGENTBOX_URL))…"
	@mkdir -p $(BACKEND_DIR)
	@cd $(BACKEND_DIR) && rm -f $(notdir $(BACKEND_PID_FILE)) && \
		$(COMMON_DEV_ENV) $(BACKEND_DEV_ENV) $(AGENTBOX_DEV_ENV) $(OTEL_DEV_ENV) $(LLM_OTEL_DEV_ENV) \
		bash -c "if [ '$(RELOAD)' = '1' ]; then \
			uv run --extra local uvicorn local_app:app --host 0.0.0.0 --port $(DEV_BACKEND_PORT) --reload & echo \$$! > $(notdir $(BACKEND_PID_FILE)); \
		else \
			uv run --extra local uvicorn local_app:app --host 0.0.0.0 --port $(DEV_BACKEND_PORT) & echo \$$! > $(notdir $(BACKEND_PID_FILE)); \
		fi; wait"

_run-frontend:
	@echo "  Starting frontend ($(FRONTEND_SITE_URL))…"
	@mkdir -p $(FRONTEND_DIR)
	@cd $(FRONTEND_DIR) && rm -f $(notdir $(FRONTEND_PID_FILE)) && \
		$(COMMON_DEV_ENV) $(FRONTEND_DEV_ENV) \
		bash -c "npm run dev -- --port $(DEV_FRONTEND_PORT) & echo \$$! > $(notdir $(FRONTEND_PID_FILE)); wait"

_wait-agentbox:
	@echo "  Waiting for embedded AgentBox on $(DEV_AGENTBOX_URL)…"
	@ready=0; \
	for i in $$(seq 1 $(AGENTBOX_READY_TIMEOUT)); do \
		if curl -fsS $(DEV_AGENTBOX_URL)/health/ready >/dev/null 2>&1; then ready=1; break; fi; \
		if [ -f $(BACKEND_PID_FILE) ]; then \
			pid=$$(cat $(BACKEND_PID_FILE)); \
			if [ -n "$$pid" ] && ! kill -0 "$$pid" 2>/dev/null; then \
				echo "  ✗ Unified backend exited before embedded AgentBox became ready (PID $$pid)"; \
				exit 1; \
			fi; \
		fi; \
		sleep 1; \
	done; \
	if [ "$$ready" != "1" ]; then \
		pid=$$(cat $(BACKEND_PID_FILE) 2>/dev/null || true); \
		if [ -n "$$pid" ] && kill -0 "$$pid" 2>/dev/null; then state="PID $$pid is still running"; else state="process is not running"; fi; \
		echo "  ✗ Embedded AgentBox did not become ready within $(AGENTBOX_READY_TIMEOUT)s (unified backend $$state)"; \
		exit 1; \
	fi; \
	echo "  ✓ Embedded AgentBox ready"

stop:
	@echo "→ Stopping dev processes…"
	@for p in $(FRONTEND_PID_FILE) $(BACKEND_PID_FILE) $(LEGACY_AGENTBOX_PID_FILE) $(CLOUDFLARED_API_PID_FILE); do \
		if [ -f $$p ]; then \
			pid=$$(cat $$p); \
			children=$$(pgrep -P $$pid 2>/dev/null || true); \
			targets="$$children $$pid"; \
			kill $$targets 2>/dev/null || true; \
			for i in $$(seq 1 40); do \
				alive=""; \
				for target in $$targets; do \
					kill -0 $$target 2>/dev/null && alive="$$alive $$target" || true; \
				done; \
				[ -z "$$alive" ] && break; \
				sleep 0.25; \
			done; \
			for target in $$targets; do \
				if kill -0 $$target 2>/dev/null; then \
					kill -KILL $$target 2>/dev/null || true; \
					echo "  Force-stopped stale child $$target ($$p)"; \
				fi; \
			done; \
			echo "  Stopped $$pid ($$p)"; \
			rm -f $$p; \
		fi; \
	done
	@# A previous graceful shutdown could have closed its listening socket but
	@# remained stuck in the embedded worker, after its pidfile was removed. Such
	@# an orphan still consumes Redis jobs and writes the worker heartbeat.
	@stale=$$(pgrep -f '$(abspath $(BACKEND_DIR))/.venv/bin/python .*uvicorn \(standalone_app\|local_app\):app .*--port $(DEV_BACKEND_PORT)' 2>/dev/null || true); \
	if [ -n "$$stale" ]; then \
		kill $$stale 2>/dev/null || true; \
		sleep 1; \
		for pid in $$stale; do \
			kill -0 $$pid 2>/dev/null && kill -KILL $$pid 2>/dev/null || true; \
		done; \
		echo "  Removed stale backend workers: $$stale"; \
	fi
	@# belt + braces: anything still listening on the dev ports
	@for port in $(DEV_FRONTEND_PORT) $(DEV_BACKEND_PORT); do \
		lsof -ti tcp:$$port 2>/dev/null | xargs -r kill 2>/dev/null && echo "  Killed leftovers on port $$port" || true; \
	done
	@rm -f $(PUBLIC_API_URL_FILE)

stop-all: stop
	@echo "→ Stopping infra containers…"
	@cd $(BACKEND_DIR) && $(COMMON_DEV_ENV) docker compose down
	@$(MAKE) --no-print-directory otel-down

otel-up:
	@echo "→ Starting pinned OpenTelemetry debug Collector…"
	@cd $(BACKEND_DIR) && $(OTEL_DEBUG_COMPOSE_ENV) docker compose -f docker-compose.otel-debug.yml up -d
	@ready=0; for i in $$(seq 1 30); do \
		if curl -fsS http://127.0.0.1:$(OTEL_DEBUG_HEALTH_PORT)/ >/dev/null 2>&1; then \
			echo "  ✓ Collector ready"; ready=1; break; \
		fi; \
		sleep 1; \
	done; [ "$$ready" = "1" ] || { echo "  ✗ Collector did not become ready"; exit 1; }

otel-down:
	@cd $(BACKEND_DIR) && $(OTEL_DEBUG_COMPOSE_ENV) docker compose -f docker-compose.otel-debug.yml down >/dev/null 2>&1 || true

otel-tail:
	@cd $(BACKEND_DIR) && $(OTEL_DEBUG_COMPOSE_ENV) docker compose -f docker-compose.otel-debug.yml logs -f otel-collector

otel-smoke: otel-down otel-up
	@cd $(BACKEND_DIR) && uv run python scripts/otel_smoke.py \
		--endpoint http://127.0.0.1:$(OTEL_DEBUG_GRPC_PORT) \
		--llm-endpoint http://127.0.0.1:$(OTEL_DEBUG_LLM_GRPC_PORT)
	@sleep 3
	@set -e; logs="$$(cd $(BACKEND_DIR) && $(OTEL_DEBUG_COMPOSE_ENV) docker compose -f docker-compose.otel-debug.yml logs --no-color otel-collector)"; \
		echo "$$logs" | grep -q "lemma-otel-smoke"; \
		echo "$$logs" | grep -q "lemma.observability.smoke"; \
		echo "$$logs" | grep -q "smoke-model"; \
		if echo "$$logs" | grep -q "CANARY"; then echo "  ✗ unsafe canary reached Collector"; exit 1; fi
	@echo "  ✓ General traces/metrics/logs and isolated LLM traces reached Collector safely"

logs:
	@cd $(BACKEND_DIR) && docker compose logs -f

# ── Tests ─────────────────────────────────────────────────────────────────────

test: test-dev-workflow test-backend-unit test-backend-e2e test-cli test-python test-frontend
	@echo ""
	@echo "✓ All test suites complete."

test-dev-workflow:
	@echo "→ Dev workflow tests…"
	@python3 -m unittest tests.test_dev_workflow -v

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
