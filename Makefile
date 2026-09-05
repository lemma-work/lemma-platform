SHELL := /bin/bash

# ──────────────────────────────────────────────────────────────────────────────
# Lemma Platform — root developer workflow
#
#   make init          create .env files with local defaults (idempotent)
#   make dev           start infra + backend + frontend (hot-reload)
#   make dev-public    same, with an ephemeral public Cloudflare API URL
#   make dev RELOAD=1  same, with uvicorn --reload on the backend
#   make dev OTEL=1 LLM_OTEL=1  same, with local HyperDX + Phoenix dashboards
#   make stop          stop backend/frontend processes
#   make stop-all      also stop infra containers
#   make desktop-dev   run Lemma Desktop from this checkout (macOS)
#   make desktop-dmg   build a self-contained macOS test DMG
#   make test          run all component test suites
#   make coverage      full coverage report (unit + e2e per component)
# ──────────────────────────────────────────────────────────────────────────────

.PHONY: help init dev dev-public stop stop-all logs otel-up otel-down otel-tail otel-smoke \
        observability-up observability-down observability-open \
        _prepare-dev _start-public-api-tunnel _ensure-databases _ensure-sandbox-images _wait-backend \
        _ensure-native-connectors _desktop-verify-dist-app _desktop-ensure-sidecars \
        desktop-dev desktop-sidecars desktop-test desktop-test-app desktop-fmt desktop-fmt-fix \
        desktop-lint desktop-guestd desktop-check-windows desktop-check \
        desktop-host-pack desktop-host-pack-check \
        desktop-concepts desktop-concepts-check \
        desktop-runtime-fetch desktop-dmg desktop-exe desktop-verify-agents \
        desktop-verify-guest desktop-clean \
        version-check local-domain-check local-auth-gate-check script-portability-check \
        test-dev-workflow \
        test test-backend test-backend-unit test-backend-e2e \
        test-frontend test-cli test-cli-unit test-cli-e2e test-python \
        scenarios scenarios-guards scenarios-sandbox scenarios-live scenarios-images \
        scenarios-standing-down \
        scenarios-deployment scenarios-provision scenarios-reset \
        scenarios-desktop scenarios-desktop-provision \
        scenarios-record scenarios-replay \
        scenario-coverage scenarios-code-coverage \
        coverage coverage-backend coverage-backend-unit coverage-backend-e2e \
        coverage-backend-module coverage-cli coverage-cli-unit coverage-cli-e2e coverage-frontend \
        lint lint-clients lint-lockfiles measure-clients client-structure-record client-typecheck-record \
        quality quality-frontend check architecture pre-push codeql codeql-python codeql-javascript codeql-all migrate

# ── Configuration ─────────────────────────────────────────────────────────────

RELOAD        ?= 0
E2E_WORKERS   ?= 2
CONTROL       ?= 0
RUN           ?=
MODULE        ?=
OTEL          ?= 0
OTEL_LOGS     ?= 1
LLM_OTEL      ?= 0

# What the backend unit lane selects. One variable because two targets run it
# -- `test-backend-unit` locally and `coverage-backend-unit` in CI -- and they
# had already drifted: only the second excluded `local_guest`, so the lane a
# person runs before pushing collected seventeen tests the lane that gates the
# merge did not. Every exclusion here is an environment-gated suite that would
# otherwise skip green; `scripts/check_pytest_census.py` fails when a new one
# appears.
# Where `desktop-e2e-temp` builds its throwaway stack. Removed on teardown,
# and the harness refuses to delete anything outside it.
E2E_TEMP_ROOT ?= /tmp/lemma-desktop-e2e

UNIT_MARKERS  ?= not e2e and not local_guest and not local_host and not desktop_e2e and not provider

BACKEND_DIR   := lemma-backend
FRONTEND_DIR  := lemma-frontend
CLI_DIR       := lemma-cli
PYTHON_DIR    := lemma-python
TS_DIR        := lemma-typescript
STACK_DIR     := lemma-stack
BUNDLE_DIR    := lemma-pod-bundle
DESKTOP_DIR   := desktop
SCENARIOS_DIR := tests/scenarios

# Desktop is one cargo workspace: the app shell, the durable daemon, the Agent
# Host, and the runtime helpers share a lockfile and a target directory. That
# is why these point at `desktop` and not at five separate crates.
DESKTOP_DOWNLOAD_DIR := $(DESKTOP_DIR)/runtime/download
DESKTOP_BUNDLED_DIR  := $(DESKTOP_DIR)/runtime/bundled
# Pinned in one file, read by the Makefile, dev-local.sh, desktop.ps1, and every
# workflow. It used to be typed inline in five of those.
TAURI_CLI     := @tauri-apps/cli@$(shell tr -d '[:space:]' < $(DESKTOP_DIR)/scripts/tauri-cli-version.txt)
MACOS_TRIPLE         := aarch64-apple-darwin
MACOS_GUEST_TARGET   := macos-aarch64
WINDOWS_TRIPLE       := x86_64-pc-windows-msvc
WINDOWS_GUEST_TARGET := windows-x86_64
# lemma-guestd is the Linux guest daemon: it reaches for std::os::unix
# unconditionally, so it builds on macOS and Linux but not on Windows.
DESKTOP_CARGO_SCOPE  := --workspace

PID_FILE      := .dev-pids
BACKEND_PID_FILE  := $(BACKEND_DIR)/.dev-backend.pid
FRONTEND_PID_FILE := $(FRONTEND_DIR)/.dev-frontend.pid
INFRA_PID_FILE    := $(BACKEND_DIR)/.dev-infra.pid
# Kept only so `make stop` can clean up a manager left by an older checkout.
CLOUDFLARED_API_PID_FILE := .dev-cloudflared-api.pid

DEV_LOG_DIR                  := .dev-logs
CLOUDFLARED_API_LOG_FILE     := $(abspath $(DEV_LOG_DIR)/cloudflared-api.log)
CLOUDFLARED_CONFIG_FILE      := $(abspath $(DEV_LOG_DIR)/cloudflared-quick-tunnel.yml)
PUBLIC_API_URL_FILE          := $(abspath $(DEV_LOG_DIR)/public-api-url)
BACKEND_READY_TIMEOUT        ?= 30
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
DEV_APP_BASE_DOMAIN   := apps.lemma.localhost:$(DEV_BACKEND_PORT)
DEV_APPS_DOMAIN_SUFFIX := apps.lemma.localhost
DEV_DATABASE_URL      := postgresql+asyncpg://postgres:postgres@localhost:$(DEV_POSTGRES_PORT)/lemma
DEV_DATASTORE_DATABASE_URL := postgresql+asyncpg://postgres:postgres@localhost:$(DEV_POSTGRES_PORT)/lemma_datastore
DEV_REDIS_URL         := redis://localhost:$(DEV_REDIS_PORT)/0
DEV_SUPERTOKENS_URL   := http://localhost:$(DEV_SUPERTOKENS_PORT)
DEV_SANDBOX_BACKEND_URL := http://host.lemma.internal:$(DEV_BACKEND_PORT)
DEV_SANDBOX_FRONTEND_URL := http://host.lemma.internal:$(DEV_FRONTEND_PORT)
DEV_WORKSPACE_RUNTIME_CREDENTIAL_KEY ?= dev-workspace-runtime-credential-key-0001
DEV_CORS_ORIGIN_REGEX := https?://(localhost|127\.0\.0\.\d+|127\.\d+\.\d+\.\d+|127-0-0-\d+\.sslip\.io|[\w-]+\.nip\.io)(:\d+)?
DEV_LOG_LEVEL         ?= DEBUG
DEV_JSON_LOGS_ENABLED ?= true
# DEV_LOG_LEVEL is DEBUG so you can read the application's own story. SQLAlchemy
# emits a record per mapped column at import — thousands of lines before the
# first request — which buries exactly that. Hold the chatty dependencies at
# WARNING; set DEV_QUIET_DEPENDENCY_LOGS=0 when debugging one of them.
DEV_QUIET_DEPENDENCY_LOGS ?= 1
# Debug Collector (used only by `make otel-smoke`, a fast CI-safe check that
# signals reach a collector and LLM spans stay isolated — not for human
# review). Numbered away from the real observability stack below so both can
# run at once without a port clash.
OTEL_DEBUG_GRPC_PORT     ?= 29317
OTEL_DEBUG_HTTP_PORT     ?= 29318
OTEL_DEBUG_LLM_GRPC_PORT ?= 29417
OTEL_DEBUG_LLM_HTTP_PORT ?= 29418
OTEL_DEBUG_HEALTH_PORT   ?= 29333

OTEL_DEBUG_COMPOSE_ENV := \
	OTEL_DEBUG_GRPC_PORT=$(OTEL_DEBUG_GRPC_PORT) \
	OTEL_DEBUG_HTTP_PORT=$(OTEL_DEBUG_HTTP_PORT) \
	OTEL_DEBUG_LLM_GRPC_PORT=$(OTEL_DEBUG_LLM_GRPC_PORT) \
	OTEL_DEBUG_LLM_HTTP_PORT=$(OTEL_DEBUG_LLM_HTTP_PORT) \
	OTEL_DEBUG_HEALTH_PORT=$(OTEL_DEBUG_HEALTH_PORT)

# Real observability stack (`make observability-up`): HyperDX/ClickStack for
# general API traces/metrics, Phoenix for LLM/OpenInference prompt review.
# Port defaults mirror lemma-backend/docker-compose.observability.yml.
HYPERDX_UI_PORT        ?= 8080
HYPERDX_OTLP_HTTP_PORT ?= 14318
HYPERDX_OTLP_GRPC_PORT ?= 14317
PHOENIX_UI_PORT         ?= 16006
PHOENIX_OTLP_GRPC_PORT  ?= 16317

OBSERVABILITY_COMPOSE_ENV := \
	HYPERDX_UI_PORT=$(HYPERDX_UI_PORT) \
	HYPERDX_OTLP_HTTP_PORT=$(HYPERDX_OTLP_HTTP_PORT) \
	HYPERDX_OTLP_GRPC_PORT=$(HYPERDX_OTLP_GRPC_PORT) \
	PHOENIX_UI_PORT=$(PHOENIX_UI_PORT) \
	PHOENIX_OTLP_GRPC_PORT=$(PHOENIX_OTLP_GRPC_PORT)

# Ingestion key `observability-up` bootstraps from HyperDX and writes here;
# read back lazily (recursive `=`, not `:=`) so it reflects the file at the
# time `_run-backend` actually starts, not at Makefile parse time.
HYPERDX_API_KEY_FILE := $(abspath $(DEV_LOG_DIR)/hyperdx-api-key)

OTEL_DEV_ENV = \
	OBSERVABILITY_ENABLED=$(if $(filter 1,$(OTEL)),true,false) \
	OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:$(HYPERDX_OTLP_GRPC_PORT) \
	OTEL_EXPORTER_OTLP_PROTOCOL=grpc \
	OTEL_EXPORTER_OTLP_HEADERS=authorization=$(shell cat $(HYPERDX_API_KEY_FILE) 2>/dev/null) \
	OTEL_TRACES_EXPORTER=otlp \
	OTEL_METRICS_EXPORTER=otlp \
	OTEL_LOGS_EXPORTER=$(if $(filter 1,$(OTEL_LOGS)),otlp,none) \
	OTEL_TRACES_SAMPLER=always_on \
	OTEL_METRIC_EXPORT_INTERVAL=5000

LLM_OTEL_DEV_ENV = \
	LLM_OTEL_ENABLED=$(if $(filter 1,$(LLM_OTEL)),true,false) \
	LLM_OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:$(PHOENIX_OTLP_GRPC_PORT) \
	LLM_OTEL_EXPORTER_OTLP_PROTOCOL=grpc \
	LLM_OTEL_TRACES_SAMPLER=always_on
# Immutable-profile inputs used by the local Docker provider. Named DEV_* here
# because lemma-backend/Makefile already uses WORKSPACE_IMAGE/FUNCTION_IMAGE for
# the untagged registry path, and these are passed across a recursive-make
# boundary below -- one name with two meanings there has ambiguous precedence.
DEV_WORKSPACE_IMAGE ?= lemma-workspace:dev
DEV_FUNCTION_IMAGE ?= lemma-function:dev
# Split on ":" by hand. `basename`/`suffix` split on ".", so they return the
# whole "name:tag" and an empty tag, and the sub-make then builds "-t name:tag:"
# which docker rejects as an invalid reference.
DEV_WORKSPACE_IMAGE_NAME := $(firstword $(subst :, ,$(DEV_WORKSPACE_IMAGE)))
DEV_WORKSPACE_IMAGE_TAG  := $(lastword $(subst :, ,$(DEV_WORKSPACE_IMAGE)))
DEV_FUNCTION_IMAGE_NAME  := $(firstword $(subst :, ,$(DEV_FUNCTION_IMAGE)))
DEV_FUNCTION_IMAGE_TAG   := $(lastword $(subst :, ,$(DEV_FUNCTION_IMAGE)))

COMMON_DEV_ENV := \
	DEV_POSTGRES_PORT=$(DEV_POSTGRES_PORT) \
	DEV_REDIS_PORT=$(DEV_REDIS_PORT) \
	DEV_SUPERTOKENS_PORT=$(DEV_SUPERTOKENS_PORT)

BACKEND_API_URL                 ?= $(DEV_BACKEND_URL)
BACKEND_FRONTEND_URL            ?= $(DEV_FRONTEND_URL)
BACKEND_AUTH_FRONTEND_URL       ?= $(DEV_AUTH_FRONTEND_URL)
BACKEND_CLI_API_URL             ?= $(DEV_BACKEND_URL)
BACKEND_CLI_AUTH_FRONTEND_URL   ?= $(DEV_AUTH_FRONTEND_URL)
BACKEND_WORKSPACE_CALLBACK_API_URL ?= $(DEV_SANDBOX_BACKEND_URL)
BACKEND_WORKSPACE_CALLBACK_AUTH_URL ?= $(DEV_SANDBOX_FRONTEND_URL)
BACKEND_WORKSPACE_CALLBACK_FRONTEND_URL ?= $(DEV_SANDBOX_FRONTEND_URL)
BACKEND_APP_BASE_DOMAIN         ?= $(DEV_APP_BASE_DOMAIN)
BACKEND_SESSION_COOKIE_DOMAIN   ?=
BACKEND_SESSION_COOKIE_SECURE   ?= false
BACKEND_SESSION_COOKIE_SAME_SITE?= lax
BACKEND_CORS_ORIGINS            ?= ["http://localhost:$(DEV_FRONTEND_PORT)","http://127.0.0.1:$(DEV_FRONTEND_PORT)"]
BACKEND_CORS_ORIGIN_REGEX       ?= $(DEV_CORS_ORIGIN_REGEX)
BACKEND_TELEGRAM_POLLING        ?= true
BACKEND_SLACK_SOCKET_MODE       ?= true

# The auth gates a local install turns off, and why each one is here.
#
# `lemma-stack` already renders exactly this set for the Docker local stack and
# the desktop install (`lemma_stack/config/render.py`), and its own tests pin
# them. `make dev` set only the first, so the documented way to run Lemma on
# your own machine behaved differently from every other local path -- and worse
# than all of them: signing up refused an `@example.com` address, and the sixth
# account in fifteen minutes locked the developer out of their own laptop for
# four minutes. None of these gates protects anything on localhost; they exist
# to stop strangers abusing a public deployment.
DEV_LOCAL_AUTH_ENV := \
	AUTH_EMAIL_VERIFICATION_REQUIRED=false \
	AUTH_EMAIL_DELIVERABILITY_CHECKS_ENABLED=false \
	AUTH_DISPOSABLE_EMAIL_DOMAINS_ENABLED=false \
	AUTH_ABUSE_PROTECTION_ENABLED=false \
	AUTH_ALTCHA_ENABLED=false
DEV_LOCAL_AUTH_KEYS := $(foreach pair,$(DEV_LOCAL_AUTH_ENV),$(firstword $(subst =, ,$(pair))))

BACKEND_DEV_ENV := \
	ENVIRONMENT=local \
	DEBUG=true \
	LOG_LEVEL=$(DEV_LOG_LEVEL) \
	LOG_QUIET_DEPENDENCIES=$(DEV_QUIET_DEPENDENCY_LOGS) \
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
	DOCUMENT_PROCESSOR=xberg \
	STORAGE_BACKEND=local \
	LOCAL_OBJECT_STORAGE_ROOT=$(abspath .local/object-storage) \
	LOCAL_FILE_STORAGE_ROOT=$(abspath .local/files) \
	EMAIL_TRANSPORT=filesystem \
	EMAIL_OUTPUT_DIR=$(abspath .local/emails) \
	$(DEV_LOCAL_AUTH_ENV) \
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
FRONTEND_APPS_DOMAIN_SUFFIX   ?= $(DEV_APPS_DOMAIN_SUFFIX)

FRONTEND_DEV_ENV := \
	NEXT_PUBLIC_API_URL=$(FRONTEND_API_URL) \
	NEXT_PUBLIC_SITE_URL=$(FRONTEND_SITE_URL) \
	NEXT_PUBLIC_AUTH_URL=$(FRONTEND_AUTH_URL) \
	NEXT_PUBLIC_SESSION_TOKEN_DOMAIN=$(FRONTEND_SESSION_TOKEN_DOMAIN) \
	NEXT_PUBLIC_APPS_DOMAIN_SUFFIX=$(FRONTEND_APPS_DOMAIN_SUFFIX)


# ── Workspace sandbox provisioning ────────────────────────────────────────────
# The workspace module provisions sandboxes in-process. Choosing a provider is
# the only decision left:
#
#   make dev                         Docker (developer default)
#   make dev WORKSPACE_PROVIDER=e2b  E2B
#
# E2B needs E2B_API_KEY and the two template ids in lemma-backend/.env.
WORKSPACE_PROVIDER ?= docker

WORKSPACE_DEV_ENV := \
	WORKSPACE_PROVIDER=$(WORKSPACE_PROVIDER) \
	WORKSPACE_RUNTIME_CREDENTIAL_KEY=$(DEV_WORKSPACE_RUNTIME_CREDENTIAL_KEY) \
	WORKSPACE_IMAGE=$(DEV_WORKSPACE_IMAGE) \
	FUNCTION_IMAGE=$(DEV_FUNCTION_IMAGE) \
	WORKSPACE_DOCKER_ALLOW_MUTABLE_IMAGES=true \
	WORKSPACE_ADD_HOST_GATEWAY=true \
	WORKSPACE_HOST_ALIAS=host.lemma.internal

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
	@echo "    make dev OTEL=1         send API traces + metrics + logs to a local HyperDX dashboard"
	@echo "    make dev LLM_OTEL=1     send full LLM prompts/responses to a local Phoenix dashboard"
	@echo "    make observability-up   start HyperDX + Phoenix without the rest of the dev stack"
	@echo "    make observability-open open the HyperDX + Phoenix dashboards in a browser"
	@echo "    make observability-down stop the observability stack"
	@echo "    make otel-smoke         verify traces, metrics, logs, and LLM isolation (CI-safe, no dashboards)"
	@echo ""
	@echo "  Desktop (one cargo workspace: app, locald, Agent Host, runtime helpers)"
	@echo "    make desktop-check      every desktop gate that runs locally — before pushing"
	@echo "    make desktop-dev        run Desktop from this checkout (macOS; CONTROL=1 opens Local settings)"
	@echo "    make desktop-test       Rust tests across the whole desktop workspace"
	@echo "    make desktop-test-app   desktop crate tests only (fast loop)"
	@echo "    make desktop-lint       clippy across the workspace, warnings are errors"
	@echo "    make desktop-fmt        rustfmt check (make desktop-fmt-fix rewrites)"
	@echo "    make desktop-sidecars   build locald, Agent Host, runtime bridge, VZ helper"
	@echo "    make desktop-runtime-fetch RUN=<id>  download runtime artifacts from a CI run"
	@echo "    make desktop-dmg        self-contained macOS test DMG (needs runtime-fetch first)"
	@echo "    make desktop-exe        Windows installer — says how to build it on Windows"
	@echo "    make desktop-guestd     build and test the Linux guest daemon (Linux only)"
	@echo "    make desktop-check-windows  compile the Windows paths from a Mac"
	@echo "    make desktop-host-pack  build the shipped host pack and run its interpreters"
	@echo "    make desktop-verify-guest  workspace sandbox + file persistence in the real VM"
	@echo "    make desktop-clean      remove desktop build output and staged runtime"
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
	@echo "    make scenarios          product scenarios over real HTTP (needs docker)"
	@echo "    make scenarios-guards   scenario suite guards only (fast, no docker)"
	@echo "    SCENARIOS_STANDING_STACK=1 make scenarios   keep the database between runs"
	@echo "    make scenarios-standing-down   and remove it again"
	@echo "    make scenarios-images   build the sandbox images the lane below needs"
	@echo "    make scenarios-sandbox  scenarios that execute functions and workflows"
	@echo "    make scenarios-live     scenarios against real Google, GitHub, Telegram"
	@echo "    make scenarios-provision  build the standing tenant on a deployment"
	@echo "    make scenarios-deployment run the suite against a deployment"
	@echo "    make scenario-coverage  regenerate docs/product/coverage.md"
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
	@echo "  Gates (what CI blocks on)"
	@echo "    make pre-push           alias for quality — run this on every push"
	@echo "    make quality            every gate the 'quality gates' CI job runs"
	@echo "    make architecture       backend architecture ratchet + route inventory"
	@echo "    make measure-clients    ADVISORY: size/complexity/typing in lemma-cli + lemma-python"
	@echo "    make check              quality + frontend gates + CodeQL on this branch's changes"
	@echo "    make lint               ruff + eslint across all components"
	@echo "    make version-check      every Lemma component declares the same version"
	@echo "    make local-domain-check the shell, capability and SDK know every base domain"
	@echo "    make local-auth-gate-check  make dev and the local stack relax the same auth gates"
	@echo ""
	@echo "  Other"
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
	@command -v openssl >/dev/null 2>&1 || (echo "  ✗ openssl not found — required to generate the local sandbox state key"; exit 1)
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
	@echo ""
	@$(MAKE) --no-print-directory _ensure-sandbox-images
	@echo ""
	@echo "Done. Run 'make dev' to start the stack."

_ensure-sandbox-images:
	@if docker image inspect "$(DEV_WORKSPACE_IMAGE)" >/dev/null 2>&1 \
		&& docker image inspect "$(DEV_FUNCTION_IMAGE)" >/dev/null 2>&1; then \
		echo "  ✓ workspace/function sandbox images already present"; \
	else \
		echo "→ Building canonical workspace/function sandbox images…"; \
		$(MAKE) -C $(BACKEND_DIR) sandbox-image-workspace \
			WORKSPACE_IMAGE="$(DEV_WORKSPACE_IMAGE_NAME)" \
			SANDBOX_TAG="$(DEV_WORKSPACE_IMAGE_TAG)"; \
		$(MAKE) -C $(BACKEND_DIR) sandbox-image-function \
			FUNCTION_IMAGE="$(DEV_FUNCTION_IMAGE_NAME)" \
			SANDBOX_TAG="$(DEV_FUNCTION_IMAGE_TAG)"; \
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
			echo "APP_BASE_DOMAIN=$(DEV_APP_BASE_DOMAIN)"; \
			echo "AUTH_WEBSITE_BASE_PATH=/auth"; \
			echo "SUPERTOKENS_API_BASE_PATH=/auth"; \
			echo "SUPERTOKENS_API_GATEWAY_PATH=/st"; \
			echo "SUPERTOKENS_CORE_URL=$(DEV_SUPERTOKENS_URL)"; \
			echo "DATABASE_URL=$(DEV_DATABASE_URL)"; \
			echo "DATASTORE_DATABASE_URL=$(DEV_DATASTORE_DATABASE_URL)"; \
			echo "REDIS_URL=$(DEV_REDIS_URL)"; \
			echo "KREUZBERG_URL="; \
			echo "DOCUMENT_PROCESSOR=xberg"; \
			echo "STORAGE_BACKEND=local"; \
			echo "LOCAL_OBJECT_STORAGE_ROOT=$(abspath .local/object-storage)"; \
			echo "LOCAL_FILE_STORAGE_ROOT=$(abspath .local/files)"; \
			echo "EMAIL_TRANSPORT=filesystem"; \
			echo "EMAIL_OUTPUT_DIR=$(abspath .local/emails)"; \
			for pair in $(DEV_LOCAL_AUTH_ENV); do echo "$$pair"; done; \
			echo "ENABLE_TELEGRAM_POLLING_MODE=true"; \
			echo "ENABLE_SLACK_SOCKET_MODE=true"; \
			echo 'CORS_ORIGINS=["http://localhost:$(DEV_FRONTEND_PORT)","http://127.0.0.1:$(DEV_FRONTEND_PORT)"]'; \
			echo 'CORS_ORIGIN_REGEX=$(DEV_CORS_ORIGIN_REGEX)'; \
			echo "# sandbox manager — embedded in the local backend"; \
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
	for k in ENVIRONMENT DEBUG API_DOCS_ENABLED LOG_LEVEL JSON_LOGS_ENABLED API_URL FRONTEND_URL AUTH_FRONTEND_URL CLI_API_URL CLI_AUTH_FRONTEND_URL APP_BASE_DOMAIN AUTH_WEBSITE_BASE_PATH SUPERTOKENS_API_BASE_PATH SUPERTOKENS_API_GATEWAY_PATH SUPERTOKENS_CORE_URL DATABASE_URL DATASTORE_DATABASE_URL REDIS_URL DOCUMENT_PROCESSOR STORAGE_BACKEND LOCAL_OBJECT_STORAGE_ROOT LOCAL_FILE_STORAGE_ROOT EMAIL_TRANSPORT EMAIL_OUTPUT_DIR $(DEV_LOCAL_AUTH_KEYS) ENABLE_TELEGRAM_POLLING_MODE ENABLE_SLACK_SOCKET_MODE CORS_ORIGINS CORS_ORIGIN_REGEX; do \
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
		append API_DOCS_ENABLED true; \
		append LOG_LEVEL $(DEV_LOG_LEVEL); \
		append JSON_LOGS_ENABLED $(DEV_JSON_LOGS_ENABLED); \
		append API_URL '$(DEV_BACKEND_URL)'; \
		append FRONTEND_URL '$(DEV_FRONTEND_URL)'; \
		append AUTH_FRONTEND_URL '$(DEV_AUTH_FRONTEND_URL)'; \
		append CLI_API_URL '$(DEV_BACKEND_URL)'; \
		append CLI_AUTH_FRONTEND_URL '$(DEV_AUTH_FRONTEND_URL)'; \
		append APP_BASE_DOMAIN '$(DEV_APP_BASE_DOMAIN)'; \
		append AUTH_WEBSITE_BASE_PATH /auth; \
		append SUPERTOKENS_API_BASE_PATH /auth; \
		append SUPERTOKENS_API_GATEWAY_PATH /st; \
		append SUPERTOKENS_CORE_URL '$(DEV_SUPERTOKENS_URL)'; \
		append DATABASE_URL '$(DEV_DATABASE_URL)'; \
		append DATASTORE_DATABASE_URL '$(DEV_DATASTORE_DATABASE_URL)'; \
		append REDIS_URL '$(DEV_REDIS_URL)'; \
		append DOCUMENT_PROCESSOR xberg; \
		append STORAGE_BACKEND local; \
		append LOCAL_OBJECT_STORAGE_ROOT '$(abspath .local/object-storage)'; \
		append LOCAL_FILE_STORAGE_ROOT '$(abspath .local/files)'; \
		append EMAIL_TRANSPORT filesystem; \
		append EMAIL_OUTPUT_DIR '$(abspath .local/emails)'; \
		for pair in $(DEV_LOCAL_AUTH_ENV); do \
			append "$${pair%%=*}" "$${pair#*=}"; \
		done; \
		append ENABLE_TELEGRAM_POLLING_MODE true; \
		append ENABLE_SLACK_SOCKET_MODE true; \
		append CORS_ORIGINS '["http://localhost:$(DEV_FRONTEND_PORT)","http://127.0.0.1:$(DEV_FRONTEND_PORT)"]'; \
		append CORS_ORIGIN_REGEX '$(DEV_CORS_ORIGIN_REGEX)'; \
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
			echo "NEXT_PUBLIC_APPS_DOMAIN_SUFFIX=$(DEV_APPS_DOMAIN_SUFFIX)"; \
		} > $(FRONTEND_DIR)/.env.local; \
		cd $(FRONTEND_DIR) && npm run gen:runtime-config --silent; \
	else \
		$(MAKE) --no-print-directory _ensure-frontend-env-keys; \
	fi

_ensure-frontend-env-keys:
	@set -e; missing=""; \
	for k in NEXT_PUBLIC_API_URL NEXT_PUBLIC_SITE_URL NEXT_PUBLIC_AUTH_URL NEXT_PUBLIC_APPS_DOMAIN_SUFFIX; do \
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
		append NEXT_PUBLIC_APPS_DOMAIN_SUFFIX '$(DEV_APPS_DOMAIN_SUFFIX)'; \
		cd $(FRONTEND_DIR) && npm run gen:runtime-config --silent; \
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
	@echo ""
	@echo "  Run local Codex/Claude Code/OpenCode/Cursor against this stack:"
	@echo "    make desktop-dev       (Lemma Desktop, which supervises Agent Host)"
	@echo ""
	@echo "  Debug and safe request-access logs are enabled."
	@echo "  Press Ctrl-C or run 'make stop' to stop."
	@echo ""
	@# The readiness check is what confirms the unified application finished
	@# starting; sandbox provisioning is part of it rather than a second service.
	@trap '$(MAKE) --no-print-directory stop; exit 0' INT TERM HUP; \
		$(MAKE) --no-print-directory _run-backend & \
		$(MAKE) --no-print-directory _run-frontend & \
		$(MAKE) --no-print-directory _wait-backend || { \
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
	echo ""; \
	echo "  Webhook callbacks and generated links use the public API URL."; \
	echo "  Public URLs are ephemeral and change each time this command starts."; \
	echo "  Debug and safe request-access logs are enabled."; \
	echo "  Press Ctrl-C or run 'make stop' to stop."; \
	echo ""; \
	trap '$(MAKE) --no-print-directory stop; exit 0' INT TERM HUP; \
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
	$(MAKE) --no-print-directory _wait-backend || { \
		status=$$?; $(MAKE) --no-print-directory stop; wait 2>/dev/null || true; exit $$status; \
	}; \
	wait

_prepare-dev:
	@$(MAKE) --no-print-directory stop 2>/dev/null || true
	@$(MAKE) --no-print-directory _ensure-init
	@$(MAKE) --no-print-directory _ensure-sandbox-images
	@$(MAKE) --no-print-directory _infra-up
	@$(MAKE) --no-print-directory _wait-infra
	@$(MAKE) --no-print-directory _ensure-databases
	@$(MAKE) --no-print-directory migrate
	@$(MAKE) --no-print-directory _ensure-native-connectors
	@if [ "$(OTEL)" = "1" ] || [ "$(LLM_OTEL)" = "1" ]; then $(MAKE) --no-print-directory observability-up; fi

_ensure-native-connectors:
	@telegram_present=$$(cd $(BACKEND_DIR) && docker compose exec -T db \
		psql -U postgres -d lemma -tAc "SELECT 1 FROM connectors WHERE id = 'telegram' LIMIT 1" \
		2>/dev/null | tr -d '[:space:]'); \
	if [ "$$telegram_present" = "1" ]; then \
		echo "  ✓ Native connector catalog already present"; \
	else \
		echo "→ Importing native connector catalog…"; \
		cd $(BACKEND_DIR) && $(BACKEND_DEV_ENV) \
			uv run python scripts/import_connector_catalog.py --provider native; \
		echo "  ✓ Native connector catalog ready"; \
	fi

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
	@test -f $(TS_DIR)/dist/index.js || { echo "  ! $(TS_DIR)/dist missing — run 'make init' (or cd $(TS_DIR) && npm run build)"; exit 1; }
	@$(MAKE) --no-print-directory _ensure-backend-env-keys
	@$(MAKE) --no-print-directory _ensure-frontend-env-keys
	@cd $(BACKEND_DIR) && uv run --extra local python -c 'import xberg, psycopg, psycopg_pool' >/dev/null || { echo "  ! Local backend dependencies missing — run 'make init'"; exit 1; }
	@echo "  Using $(BACKEND_DIR)/.env + $(FRONTEND_DIR)/.env.local"

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
	@echo "  Ensuring extra databases (supertokens, lemma_datastore) exist…"
	@cd $(BACKEND_DIR) && \
		for db in supertokens lemma_datastore; do \
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
	@echo "  Starting unified backend ($(BACKEND_API_URL))…"
	@mkdir -p $(BACKEND_DIR)
	@cd $(BACKEND_DIR) && rm -f $(notdir $(BACKEND_PID_FILE)) && \
		$(COMMON_DEV_ENV) $(BACKEND_DEV_ENV) $(WORKSPACE_DEV_ENV) $(OTEL_DEV_ENV) $(LLM_OTEL_DEV_ENV) \
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

_wait-backend:
	@echo "  Waiting for the backend on $(DEV_BACKEND_URL)…"
	@ready=0; \
	for i in $$(seq 1 $(BACKEND_READY_TIMEOUT)); do \
		if curl -fsS $(DEV_BACKEND_URL)/health/ready >/dev/null 2>&1; then ready=1; break; fi; \
		if [ -f $(BACKEND_PID_FILE) ]; then \
			pid=$$(cat $(BACKEND_PID_FILE)); \
			if [ -n "$$pid" ] && ! kill -0 "$$pid" 2>/dev/null; then \
				echo "  ✗ Backend exited before becoming ready (PID $$pid)"; \
				exit 1; \
			fi; \
		fi; \
		sleep 1; \
	done; \
	if [ "$$ready" != "1" ]; then \
		pid=$$(cat $(BACKEND_PID_FILE) 2>/dev/null || true); \
		if [ -n "$$pid" ] && kill -0 "$$pid" 2>/dev/null; then state="PID $$pid is still running"; else state="process is not running"; fi; \
		echo "  ✗ Backend did not become ready within $(BACKEND_READY_TIMEOUT)s ($$state)"; \
		exit 1; \
	fi; \
	echo "  ✓ Backend ready"

stop:
	@echo "→ Stopping dev processes…"
	@for p in $(FRONTEND_PID_FILE) $(BACKEND_PID_FILE) $(CLOUDFLARED_API_PID_FILE); do \
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
	@$(MAKE) --no-print-directory observability-down

observability-up:
	@echo "→ Starting HyperDX (ClickStack) + Phoenix observability stack…"
	@cd $(BACKEND_DIR) && $(OBSERVABILITY_COMPOSE_ENV) docker compose -f docker-compose.observability.yml up -d --quiet-pull
	@ready=0; for i in $$(seq 1 60); do \
		if curl -fsS http://localhost:$(HYPERDX_UI_PORT)/api/health >/dev/null 2>&1; then \
			ready=1; break; \
		fi; \
		sleep 1; \
	done; [ "$$ready" = "1" ] || { echo "  ✗ HyperDX did not become ready"; exit 1; }
	@mkdir -p $(DEV_LOG_DIR)
	@cd $(BACKEND_DIR) && uv run python scripts/hyperdx_bootstrap.py \
		--base-url http://localhost:$(HYPERDX_UI_PORT) > $(HYPERDX_API_KEY_FILE) || { \
		echo "  ✗ HyperDX bootstrap failed"; rm -f $(HYPERDX_API_KEY_FILE); exit 1; \
	}
	@echo "  ✓ HyperDX  → http://localhost:$(HYPERDX_UI_PORT)  (general API traces/metrics)"
	@echo "  ✓ Phoenix  → http://localhost:$(PHOENIX_UI_PORT)  (LLM prompts/responses)"

observability-down:
	@cd $(BACKEND_DIR) && docker compose -f docker-compose.observability.yml down >/dev/null 2>&1 || true
	@rm -f $(HYPERDX_API_KEY_FILE)

observability-open:
	@case "$$(uname)" in \
		Darwin) open http://localhost:$(HYPERDX_UI_PORT) http://localhost:$(PHOENIX_UI_PORT) ;; \
		Linux) xdg-open http://localhost:$(HYPERDX_UI_PORT) >/dev/null 2>&1 & \
			xdg-open http://localhost:$(PHOENIX_UI_PORT) >/dev/null 2>&1 & ;; \
		*) echo "  HyperDX → http://localhost:$(HYPERDX_UI_PORT)"; \
			echo "  Phoenix → http://localhost:$(PHOENIX_UI_PORT)" ;; \
	esac

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
		if echo "$$logs" | grep -qF "SELECT 'CANARY'"; then \
			echo "  ✗ unsafe canary (db.statement) reached Collector via the general pipeline"; exit 1; \
		fi; \
		if echo "$$logs" | grep -qF "CANARY.invalid"; then \
			echo "  ✗ unsafe canary (url.full) reached Collector via the general pipeline"; exit 1; \
		fi; \
		if ! echo "$$logs" | grep -qF "CANARY prompt"; then \
			echo "  ✗ LLM pipeline did not carry prompt content (regression: content should ship when LLM_OTEL_ENABLED)"; exit 1; \
		fi
	@echo "  ✓ General pipeline stayed sanitized; isolated LLM pipeline carried full prompt content"

logs:
	@cd $(BACKEND_DIR) && docker compose logs -f

# ── Desktop ───────────────────────────────────────────────────────────────────
#
# Lemma Desktop is one cargo workspace rooted at desktop/Cargo.toml: the app
# shell, the durable daemon (locald), the Agent Host, and the runtime helpers.
# It ships on macOS and Windows.
#
# The Makefile is the macOS and Linux entrypoint. Windows has no `make`, so the
# same verbs live in desktop/scripts/desktop.ps1 and every target that cannot
# run on this host names its counterpart rather than just failing.

# Agent Host has no headless install any more, so this replaced `make
# agent-host`: there is one dev entrypoint, and it is the whole app.
#
# dev-local.sh builds every sidecar, retires the daemon a previous run left
# behind — a locald an hour older than the fix under test will happily keep
# reproducing the bug it fixed — and points every mutable path at a throwaway
# root, so a dev session cannot adopt or corrupt the identity a real install
# owns. It borrows the managed guest from an installed release, because a
# checkout cannot build one.
desktop-dev:
	@test "$$(uname -s)" = "Darwin" || ( \
		echo "  ✗ make desktop-dev runs the app from source, which is macOS only"; \
		echo "    On Windows, build and install the app instead:"; \
		echo "      pwsh desktop\\scripts\\desktop.ps1 exe"; exit 1)
	@command -v cargo >/dev/null 2>&1 || \
		(echo "  ✗ cargo not found — install Rust from https://rustup.rs"; exit 1)
	@command -v node >/dev/null 2>&1 || \
		(echo "  ✗ node not found — install Node.js 22 from https://nodejs.org"; exit 1)
	@$(DESKTOP_DIR)/scripts/dev-local.sh --source $(if $(filter 1,$(CONTROL)),--control,)

# Four binaries from one cargo invocation. Asking for them separately would
# resolve features over three different package sets and rebuild reqwest, tokio
# and hyper from scratch each time.
desktop-sidecars:
	@command -v cargo >/dev/null 2>&1 || \
		(echo "  ✗ cargo not found — install Rust from https://rustup.rs"; exit 1)
	@case "$$(uname -s)" in \
		Darwin) $(DESKTOP_DIR)/scripts/build-sidecar.sh ;; \
		*) echo "  ✗ sidecars are built per platform"; \
		   echo "    On Windows: pwsh desktop\\scripts\\desktop.ps1 sidecars"; exit 1 ;; \
	esac

# tauri-build resolves the externalBin sidecars at build-script time, so
# anything that compiles lemma-desktop -- clippy and the tests included -- fails
# outright on a fresh clone until they exist. Build them once rather than making
# every contributor learn that from a build-script panic.
_desktop-ensure-sidecars:
	@case "$$(uname -s)" in \
		Darwin) built="$(DESKTOP_DIR)/binaries/lemma-locald-$(MACOS_TRIPLE)" ;; \
		*) built="" ;; \
	esac; \
	if [ -n "$$built" ] && [ ! -f "$$built" ]; then \
		echo "→ Building native sidecars (lemma-desktop cannot compile without them)…"; \
		$(DESKTOP_DIR)/scripts/build-sidecar.sh >/dev/null; \
		echo "  ✓ sidecars ready"; \
	fi

# One workspace, one test command — the point of collapsing six crates into it.
# lemma-guestd is included here because it builds on macOS and Linux; only
# Windows has to skip it.
desktop-test: _desktop-ensure-sidecars
	@command -v cargo >/dev/null 2>&1 || \
		(echo "  ✗ cargo not found — install Rust from https://rustup.rs"; exit 1)
	@echo "→ Desktop workspace tests…"
	@cd $(DESKTOP_DIR) && cargo test $(DESKTOP_CARGO_SCOPE) --locked
	@echo "  ✓ desktop workspace tests pass"

# The app crate alone, for when the shell is what changed.
desktop-test-app: _desktop-ensure-sidecars
	@echo "→ Desktop app tests…"
	@cd $(DESKTOP_DIR) && cargo test -p lemma-desktop --locked

desktop-fmt:
	@echo "→ Desktop rustfmt…"
	@cd $(DESKTOP_DIR) && cargo fmt --all --check

desktop-fmt-fix:
	@echo "→ Rewriting the desktop workspace with rustfmt…"
	@cd $(DESKTOP_DIR) && cargo fmt --all

desktop-lint: _desktop-ensure-sidecars
	@command -v cargo >/dev/null 2>&1 || \
		(echo "  ✗ cargo not found — install Rust from https://rustup.rs"; exit 1)
	@echo "→ Desktop workspace clippy…"
	@cd $(DESKTOP_DIR) && cargo clippy $(DESKTOP_CARGO_SCOPE) --locked --all-targets -- -D warnings
	@echo "  ✓ clippy clean"
	@echo "→ Memory balloon policy…"
	@$(DESKTOP_DIR)/scripts/check-balloon-policy.sh

# Build the ~1 GB artifact the app ships, and prove its interpreters run.
#
# This is what CI's `host-pack-macos` job does, runnable by hand. It is the only
# check that catches a relocatable CPython whose baked `sys.prefix` still points
# at the build machine -- which unpacks perfectly, reports the right version,
# and fails the instant it is asked to import anything, on a user's machine,
# four minutes into a first run.
#
# ~15 minutes and ~1 GB. PACK_OUT to keep the result; the default is a
# throwaway. PYTHON_ROOT to copy an interpreter you already have, which is the
# fix when your `uv` predates the pinned CPython patch version.
PACK_OUT     ?= out/host-pack
PYTHON_ROOT  ?=

desktop-host-pack:
	@echo "→ Building the host pack (this takes a while and ~1 GB)…"
	@mkdir -p $(PACK_OUT)
	@python3 -c "import json,pathlib,sys; \
	  image=lambda n: {'ref': f'ghcr.io/lemma-work/{n}:0.0.0-local', 'digest': 'sha256:'+'0'*64}; \
	  pathlib.Path(sys.argv[1]).write_text(json.dumps({'schema_version': 1, \
	    'version': '0.0.0-local', 'min_admin_version': '0.1.0', \
	    'images': {n: image('lemma-'+n) for n in ('backend','frontend','workspace','function')}, \
	    'infra': {'postgres': image('postgres'), 'redis': image('redis')}}, indent=2)+chr(10))" \
	  $(PACK_OUT)/host-release.json
	@uv run --no-project python scripts/build_local_host_pack.py 		--output $(PACK_OUT)/local-runtime 		--release-manifest $(PACK_OUT)/host-release.json 		$(if $(PYTHON_ROOT),--python-root $(PYTHON_ROOT),)
	@uv run --no-project python scripts/check_host_pack.py $(PACK_OUT)/local-runtime

# Just the checks, against a pack you already have.
desktop-host-pack-check:
	@uv run --no-project python scripts/check_host_pack.py $(PACK_OUT)/local-runtime

# Compile the Windows code paths from a Mac, before pushing.
#
# The `desktop-windows` CI job is not in the desktop path filter, so its result
# arrives a push later -- and it has now caught four separate things one round
# at a time: unix-only test helpers, a `Path` import behind the wrong cfg, CRLF
# breaking source searches, and tests that spawn `/bin/sh`.
#
# This catches the *compile* half of that class locally. `lemma-agent-host` is
# left out on purpose: it pulls `libsqlite3-sys`, whose build script needs a
# Windows C toolchain that a Mac does not have. locald is where every one of
# those failures was.
#
# The runtime half -- a POSIX binary that is simply not there -- is not a
# compile error, and is caught by the source lint in `host_process.rs` instead.
# The desktop gates that can run on a developer machine, cheapest first.
#
# Not all of CI: bundling and codesigning need release certificates, and the app
# crate cannot cross-compile to msvc from macOS (libsqlite3-sys wants a C
# toolchain), so `desktop-check-windows` covers locald and the runtime manager
# only. desktop/README.md has the gnu-target recipe for the rest.
#
# The pieces already existed; nothing ran them together, so "I ran the desktop
# checks" meant whichever two someone remembered. Both halves of that bit this
# repo in one afternoon: `desktop-lint` does not run rustfmt, so a formatting
# diff reached CI green-locally, and `desktop-check-windows` is the only thing
# that compiles the Windows cfg paths, so a `#[cfg(unix)]` helper called from an
# ungated one failed a 90-minute Windows job that a 15-second local check would
# have caught.
#
# Not covered here, deliberately: the DMG/NSIS bundle and codesigning steps.
# They need release certificates, so they cannot run on a contributor's machine
# -- `make desktop-dmg` is the local approximation.
desktop-check: desktop-fmt desktop-concepts-check desktop-lint desktop-test desktop-check-windows
	@echo ""
	@echo "  ✓ desktop: fmt, concepts, clippy, tests, and the locald/runtime-manager Windows paths"

desktop-check-windows:
	@rustup target list --installed | grep -q x86_64-pc-windows-msvc || ( \
		echo "→ Adding the Windows target…"; \
		rustup target add x86_64-pc-windows-msvc)
	@echo "→ Windows compile check (locald, runtime manager, tests included)…"
	@cd $(DESKTOP_DIR) && cargo clippy \
		-p lemma-locald -p lemma-runtime-manager \
		--target x86_64-pc-windows-msvc --all-targets --locked -- -D warnings
	@echo "  ✓ the Windows code paths compile and lint"

# guestd's vsock listener is behind a Linux cfg that only a Linux build ever
# compiles, so a green macOS run says nothing about the code that actually runs
# in the guest.
desktop-guestd:
	@test "$$(uname -s)" = "Linux" || ( \
		echo "  ✗ the guest daemon's Linux-only paths need a Linux host"; \
		echo "    CI runs this on ubuntu-latest."; exit 1)
	@echo "→ Guest daemon (Linux)…"
	@cd $(DESKTOP_DIR) && cargo clippy -p lemma-guestd --locked --all-targets -- -D warnings
	@cd $(DESKTOP_DIR) && cargo test -p lemma-guestd --locked

# desktop/ui/concepts.gen.json is generated from the frontend's concept registry
# and committed, so the splash can name concepts without importing the frontend.
desktop-concepts:
	@echo "→ Baking splash concepts…"
	@node $(DESKTOP_DIR)/scripts/extract-concepts.mjs

desktop-concepts-check: desktop-concepts
	@git diff --exit-code $(DESKTOP_DIR)/ui/concepts.gen.json || ( \
		echo "  ✗ desktop/ui/concepts.gen.json is stale — commit the regenerated file"; \
		exit 1)

# The host pack and the guest runtime are release artifacts, not build outputs.
# The pack embeds digests of a specific set of container builds; the guest is an
# ext4 image (macOS) or a WSL rootfs (Windows) assembled under docker buildx with
# a kernel unpacked by zstd. Neither can be produced from a checkout on demand,
# which is why this fetches them. One run feeds both a Mac and a Windows box.
desktop-runtime-fetch:
	@command -v gh >/dev/null 2>&1 || \
		(echo "  ✗ gh not found — install from https://cli.github.com"; exit 1)
	@test -n "$(RUN)" || ( \
		echo "RUN is required, e.g. make desktop-runtime-fetch RUN=12345678901"; \
		echo ""; \
		echo "  Cut one first:"; \
		echo "    gh workflow run release-local-images.yml -f version=0.7.0 -f publish=false"; \
		echo "    gh run list --workflow release-local-images.yml"; \
		exit 1)
	@echo "→ Downloading runtime artifacts from run $(RUN)…"
	@rm -rf $(DESKTOP_DOWNLOAD_DIR)
	@gh run download $(RUN) --dir $(DESKTOP_DOWNLOAD_DIR) \
		--pattern 'host-pack-*' \
		--pattern 'guest-runtime-*' \
		--pattern 'lemma-local-test-manifest-*' || ( \
		echo "  ✗ download failed — is $(RUN) a Release Local Stack Images run with publish: false?"; \
		exit 1)
	@echo "  ✓ artifacts in $(DESKTOP_DOWNLOAD_DIR)"

# The one command that turns this checkout into an installable macOS build.
#
# "Self-contained" means the DMG carries the host pack and the guest runtime as
# app resources instead of downloading them on first launch, so it installs on a
# machine with no network and against no published release. That is the build
# worth handing someone to try a branch — CI's build-check DMG points at
# unresolvable URLs on purpose and refuses to install.
#
# Self-contained builds live here and only here. CI used to package one too,
# but Apple's notary service unpacks host-runtime.zip and rejects the bundled
# CPython and node_modules inside it, so a self-contained DMG can never be
# notarized -- which makes it useless for handing to anyone else, and not worth
# half a gigabyte a run. CI publishes the signed online DMG instead
# (`release-local-images.yml` with `share`); this is the one you install
# yourself, from the runtime artifacts that workflow uploads.
desktop-dmg:
	@test "$$(uname -s)" = "Darwin" || ( \
		echo "  ✗ desktop-dmg builds a macOS DMG"; \
		echo "    On Windows: pwsh desktop\\scripts\\desktop.ps1 exe"; exit 1)
	@command -v cargo >/dev/null 2>&1 || \
		(echo "  ✗ cargo not found — install Rust from https://rustup.rs"; exit 1)
	@command -v swift >/dev/null 2>&1 || \
		(echo "  ✗ swift not found — install Xcode or the Command Line Tools"; exit 1)
	@command -v node >/dev/null 2>&1 || \
		(echo "  ✗ node not found — install Node.js 22 from https://nodejs.org"; exit 1)
	@command -v jq >/dev/null 2>&1 || (echo "  ✗ jq not found — brew install jq"; exit 1)
	@test -d $(DESKTOP_DOWNLOAD_DIR) || ( \
		echo "  ✗ no runtime artifacts in $(DESKTOP_DOWNLOAD_DIR)"; \
		echo "    Fetch them first: make desktop-runtime-fetch RUN=<run-id>"; exit 1)
	@echo "→ Staging the bundled runtime for $(MACOS_TRIPLE)…"
	@python3 scripts/prepare_desktop_test_runtime.py \
		--artifacts-dir $(DESKTOP_DOWNLOAD_DIR) \
		--mode bundled \
		--host-target $(MACOS_TRIPLE) \
		--guest-target $(MACOS_GUEST_TARGET) \
		--stage-dir $(DESKTOP_BUNDLED_DIR) \
		--output $(DESKTOP_BUNDLED_DIR)/lemma-local.json >/dev/null
	@echo "  ✓ host and guest archives verified and staged"
	@$(MAKE) --no-print-directory desktop-concepts-check
	@echo "→ Building native sidecars…"
	@$(DESKTOP_DIR)/scripts/build-sidecar.sh >/dev/null
	@# Virtualization.framework checks the signature of whoever creates the VM,
	@# and the bundler re-signs sidecars without helper entitlements — which is
	@# why the VZ helper ships as a pre-signed resource, re-sealed here exactly
	@# as CI does it.
	@echo "→ Re-sealing the virtualization helper…"
	@codesign --force --options runtime \
		--entitlements $(DESKTOP_DIR)/entitlements.plist \
		--sign "$${APPLE_SIGNING_IDENTITY:--}" \
		$(DESKTOP_DIR)/binaries/lemma-vz-$(MACOS_TRIPLE) 2>/dev/null
	@codesign --verify --strict $(DESKTOP_DIR)/binaries/lemma-vz-$(MACOS_TRIPLE)
	@echo "→ Bundling the self-contained DMG…"
	@cd $(DESKTOP_DIR) && APPLE_SIGNING_IDENTITY="$${APPLE_SIGNING_IDENTITY:--}" \
		npx -y $(TAURI_CLI) build --config tauri.dist.conf.json
	@$(MAKE) --no-print-directory _desktop-verify-dist-app
	@echo ""
	@dmg=$$(ls $(DESKTOP_DIR)/target/release/bundle/dmg/Lemma_*.dmg 2>/dev/null | head -1); \
	echo "  ✓ $$dmg"
	@test -n "$${APPLE_SIGNING_IDENTITY:-}" || ( \
		echo "    Ad-hoc signed: Gatekeeper will ask on first open, and locald"; \
		echo "    re-prompts for keychain access after every rebuild. Set"; \
		echo "    APPLE_SIGNING_IDENTITY to a Developer ID to avoid both."; true)

# Windows cannot be cross-built from here: the NSIS bundler and the MSVC
# toolchain both have to run on the target. This target exists to say so.
desktop-exe:
	@echo "  ✗ the Windows installer is built on Windows"
	@echo "    There, run:"
	@echo "      pwsh desktop\\scripts\\desktop.ps1 runtime-fetch -Run <run-id>"
	@echo "      pwsh desktop\\scripts\\desktop.ps1 exe"
	@exit 1

# The one command that answers "does ACP chat over Agent Host actually work?"
# Drives Codex, Claude Code and OpenCode over real ACP and asserts each streams
# a real answer back through the host protocol, keeps one provider session
# across two turns, and survives a session the provider has forgotten.
#
# Spends real provider quota and needs those agents authenticated locally, which
# is why it is opt-in and excluded from CI.
desktop-verify-agents:
	@command -v cargo >/dev/null 2>&1 || \
		(echo "  ✗ cargo not found — install Rust from https://rustup.rs"; exit 1)
	@echo "→ Verifying ACP chat over Agent Host (real agents, real quota)…"
	@cd $(DESKTOP_DIR) && \
		LEMMA_REAL_AGENT_HOST_DATA_DIR="$${LEMMA_REAL_AGENT_HOST_DATA_DIR:-$$HOME/Library/Application Support/Lemma/agent-host}" \
		cargo test -p lemma-agent-host --locked --test real_harness_e2e -- \
			--ignored --nocapture --test-threads=1
	@echo "  ✓ ACP chat over Agent Host verified"

# Does a workspace sandbox actually run in the macOS guest, and do its files
# survive being released?
#
# Everything below the guest boundary is already covered by a stub. This is the
# guest itself: a real container from a real image inside the VZ VM, and the
# release-then-reacquire cycle the idle sweep performs on every workspace. If
# the volume did not survive that, the sweep would destroy user work and the
# report would arrive hours later as "my files are gone".
#
# Needs Lemma Desktop installed and its runtime up; the test names anything
# still missing rather than failing halfway through.
desktop-verify-guest:
	@test "$$(uname -s)" = "Darwin" || \
		(echo "  ✗ the managed guest is a macOS VZ virtual machine"; exit 1)
	@echo "→ Building the runtime bridge…"
	@cd $(DESKTOP_DIR) && cargo build --release --locked -p lemma-runtime
	@echo "→ Workspace sandbox and file persistence in the real guest…"
	@cd $(BACKEND_DIR) && LEMMA_LOCAL_REAL_GUEST=1 uv run pytest -m local_guest -q
	@echo "  ✓ the guest runs workspaces and keeps their files"

# The journeys that only exist on the desktop build, run for real.
#
# Apps served by host, functions dispatched into the guest, pod files on local
# disk, and a pod app loaded in WKWebView. Every one of these differs from the
# server build, and two of them have already shipped broken to users while the
# whole server-side suite stayed green:
#
#   * pod apps rendered signed out -- WebKit will not send the session cookie
#     between two `.localhost` hosts, and Chromium will, so nothing that drives
#     Chromium could see it;
#   * every function died at `getaddrinfo`, because sandboxes were handed an
#     address only the Mac can resolve.
#
# Nothing here is mocked. The suite signs up its own user, makes its own pod,
# app, function and files, and removes them again -- it never reads or writes
# anything that was already in the install.
#
# Needs Lemma Desktop installed and running; it reads that install's own ports
# out of locald/network.json rather than guessing. Point it at a different
# install with LEMMA_DESKTOP_E2E_LOCALD_ROOT.
#
# Agents are a separate lane (`desktop-verify-agents`), because they drive real
# Claude Code / Codex / OpenCode and spend real provider quota. `AGENTS=1` runs
# them too. They are named here rather than left unmentioned so that a green run
# of this target is not mistaken for agent coverage.
desktop-e2e:
	@test "$$(uname -s)" = "Darwin" || \
		(echo "  ✗ the desktop build and its guest are macOS-only"; exit 1)
	@command -v swift >/dev/null 2>&1 || \
		(echo "  ✗ swift not found — the WKWebView probe needs Xcode's toolchain"; exit 1)
	@echo "→ Desktop journeys against the running install…"
	@cd $(BACKEND_DIR) && LEMMA_DESKTOP_E2E=1 uv run pytest -m desktop_e2e -q
	@echo "  ✓ apps, functions, files and the WKWebView session all work"
	@if [ "$(AGENTS)" = "1" ]; then \
		$(MAKE) desktop-verify-agents; \
	else \
		echo "  · agents not run — they spend real provider quota."; \
		echo "    Add AGENTS=1, or run 'make desktop-verify-agents' on its own."; \
	fi

# The same journeys, against a stack built from YOUR WORKING TREE.
#
# `desktop-e2e` tests the install you have; this tests the code you just wrote,
# without waiting for a DMG. It stands up its own backend from this checkout --
# in the packed shape a release ships, with the environment rendered by this
# checkout's own locald -- on its own port, its own databases and its own
# storage, then tears all of it down.
#
# Postgres, Redis and SuperTokens are borrowed from the running install's guest
# VM. A second VM would cost ~4 GiB for infrastructure this cannot affect; the
# throwaway databases live beside the install's and are dropped afterwards, and
# Redis is isolated to its own numbered database so the two backends never join
# each other's consumer groups.
#
# Not covered here: functions. They are dispatched into guest sandboxes by
# locald, and this stack has none -- that lane needs `desktop-e2e` against a
# packaged install, and it says so rather than timing out.
desktop-e2e-temp:
	@test "$$(uname -s)" = "Darwin" || \
		(echo "  ✗ the desktop build and its guest are macOS-only"; exit 1)
	@command -v swift >/dev/null 2>&1 || \
		(echo "  ✗ swift not found — the WKWebView probe needs Xcode's toolchain"; exit 1)
	@command -v psql >/dev/null 2>&1 || \
		(echo "  ✗ psql not found — brew install libpq (the throwaway databases need it)"; exit 1)
	@set -e; \
	stack="$$PWD/desktop/e2e/throwaway_stack.py"; \
	trap 'python3 '"$$stack"' down --root $(E2E_TEMP_ROOT) >/dev/null 2>&1 || true' EXIT INT TERM; \
	python3 "$$stack" down --root $(E2E_TEMP_ROOT) >/dev/null 2>&1 || true; \
	python3 "$$stack" up --root $(E2E_TEMP_ROOT) >/dev/null; \
	cd $(BACKEND_DIR) && LEMMA_DESKTOP_E2E=1 \
		LEMMA_DESKTOP_E2E_LOCALD_ROOT=$(E2E_TEMP_ROOT)/locald \
		uv run pytest -m desktop_e2e -q
	@echo "  ✓ this working tree serves apps with a session, and cleans up after itself"

# Everything a build produces. desktop/capabilities/ is hand-written and stays;
# desktop/permissions/autogenerated/ is written by build.rs and regenerates on
# the next build of the app crate.
desktop-clean:
	@echo "→ Removing desktop build output…"
	@rm -rf $(DESKTOP_DIR)/target $(DESKTOP_DIR)/binaries $(DESKTOP_DIR)/gen \
		$(DESKTOP_DIR)/permissions/autogenerated \
		$(DESKTOP_DOWNLOAD_DIR) $(DESKTOP_BUNDLED_DIR) \
		$(DESKTOP_DIR)/local-runtime/macos-vz/.build
	@echo "  ✓ clean"

# Exactly the gates release-local-images.yml applies to its PR test DMG, so a
# bundle that passes here is the bundle CI would have accepted — plus the
# locald identifier check, which a local build is the likeliest place to lose.
_desktop-verify-dist-app:
	@set -eu; \
	app="$(DESKTOP_DIR)/target/release/bundle/macos/Lemma.app"; \
	test -x "$$app/Contents/MacOS/lemma-locald"; \
	test -x "$$app/Contents/MacOS/lemma-agent-host"; \
	test -x "$$app/Contents/MacOS/lemma-runtime"; \
	test -x "$$app/Contents/Resources/lemma-vz"; \
	test -f "$$app/Contents/Resources/lemma-local.json"; \
	test -f "$$app/Contents/Resources/host-runtime.zip"; \
	test -f "$$app/Contents/Resources/guest-runtime.zip"; \
	test ! -e "$$app/Contents/Resources/local-runtime"; \
	test ! -e "$$app/Contents/Resources/managed-runtime"; \
	app_bytes=$$(du -sk "$$app" | awk '{print $$1 * 1024}'); \
	test "$$app_bytes" -le $$((850 * 1024 * 1024)) || ( \
		echo "  ✗ app is $$app_bytes bytes; the bundled gate is 850 MiB"; exit 1); \
	codesign --verify --deep --strict "$$app"; \
	codesign -d --entitlements :- "$$app/Contents/Resources/lemma-vz" 2>&1 \
		| grep -qF "com.apple.security.virtualization"; \
	codesign -dvvv "$$app/Contents/MacOS/lemma-locald" 2>&1 \
		| grep -qFx "Identifier=work.lemma.locald" || ( \
		echo "  ✗ locald lost its embedded Info.plist identifier — the credential"; \
		echo "    vault would treat every rebuild as a different program"; exit 1); \
	test "$$(jq -r .version "$$app/Contents/Resources/lemma-local.json")" = \
		"$$(jq -r .version $(DESKTOP_DIR)/tauri.conf.json)" || ( \
		echo "  ✗ the bundled runtime manifest and Desktop disagree on the version"; \
		exit 1); \
	echo "  ✓ bundle verified (sidecars, resources, size, signature, identifier, version)"

# Consistency matters when something is published, not while it is being
# written, so this is not part of `make quality`. It is here because the Rust
# crates were never covered at all: release rewrites one manifest, and before
# the workspace the other five would have drifted silently.
version-check:
	@echo "→ Component versions…"
	@python3 scripts/check_version_consistency.py

# The base domain an install serves under is decided at runtime and spelled out
# in four places, in three languages. Nothing tied them together, and the cost
# was a shipped build where this computer could not pair with its own workspace:
# two loopback checks still said `.localhost` after the base had moved, so the
# refusal was silent and onboarding waited for ever.
local-domain-check:
	@echo "→ Local domain lists…"
	@python3 scripts/check_local_domain_consistency.py

local-auth-gate-check:
	@echo "→ Local auth gates…"
	@python3 scripts/check_local_auth_gates.py

# CI runs scripts/ with a bare `python`, which on the Windows and macOS runners
# is not the 3.14 the backend pins. Syntax they cannot parse is not a failing
# step, it is a SyntaxError before the first line -- which is how one
# unparenthesised `except` stopped every Windows host pack from building.
script-portability-check:
	@echo "→ Script portability…"
	@python3 scripts/check_script_portability.py

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
	@cd $(BACKEND_DIR) && uv run pytest -m "$(UNIT_MARKERS)" -q

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
	@cd $(PYTHON_DIR) && uv run --with pytest --with pytest-asyncio pytest tests/ -m "not integration" -q

# ── Product scenarios ─────────────────────────────────────────────────────────
# The black-box suite: it boots the real stack (postgres/redis/supertokens +
# uvicorn) and drives it over HTTP as an external client, so it needs docker.
# Output is grouped by product journey rather than by module — see
# docs/product/README.md for what each scenario is proving, and
# tests/scenarios/README.md for how the suite is put together.

scenarios:
	@echo "→ Product scenarios (real HTTP, needs docker)…"
	@cd $(SCENARIOS_DIR) && uv run pytest -q

# Build the sandbox images the `sandbox` lane needs. Local tags rather than the
# content-addressed names the backend's own e2e uses: those rebuild whenever
# anything in the repo changes, which is right for a release gate and wrong for
# a suite meant to be run constantly. Rebuild when sandbox-images/ changes.
SCENARIOS_PLATFORM ?= linux/$(shell uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')

scenarios-images:
	@echo "→ Building sandbox images ($(SCENARIOS_PLATFORM))…"
	@docker build --platform $(SCENARIOS_PLATFORM) \
		-f $(BACKEND_DIR)/sandbox-images/Dockerfile.function \
		-t lemma-function:scenarios .
	@docker build --platform $(SCENARIOS_PLATFORM) \
		-f $(BACKEND_DIR)/sandbox-images/Dockerfile.workspace \
		-t lemma-workspace:scenarios .
	@echo "✓ sandbox images ready; run 'make scenarios-sandbox'"

# The sandbox lane: functions executing, workflows running their graphs, and
# bundle imports that build what they import. Needs `make scenarios-images`
# first — minutes, not seconds, which is why they are not in `scenarios`.
scenarios-sandbox:
	@echo "→ Product scenarios needing a sandbox…"
	@cd $(SCENARIOS_DIR) && uv run pytest -q -m sandbox

# The live lane: the same platform, driven against the real third parties people
# connect — Google, GitHub, Telegram, Composio — and a real model. Credentials
# come from the backend's own .env (or LEMMA_ENV_FILE) — there are no test-only
# credential variables; providers you have not configured are skipped with a
# reason naming what is missing. See tests/scenarios/LIVE.md.
scenarios-live:
	@echo "→ Product scenarios against real providers…"
	@cd $(SCENARIOS_DIR) && SCENARIOS_USE_DEPLOYMENT_ENV=1 SCENARIOS_LLM_MODE=real \
		SCENARIOS_CONNECTOR_CATALOGUE=all SCENARIOS_TELEGRAM_POLLING=true \
		uv run pytest -q -m live --timeout=900 journeys/live

# ── Real providers, recorded once ─────────────────────────────────────────────
# Everything Lemma sends outward goes through one proxy. `record` drives the
# real Telegram, Google, GitHub and Slack with real credentials and writes what
# happened to tests/scenarios/cassettes/; `replay` serves that back and refuses
# anything it has not seen, so a run cannot quietly reach the internet.
#
# The recordings are committed and reviewed like code: a diff in one is a third
# party changing its API, which is the signal a hand-written fake could never
# give. See tests/scenarios/harness/egress.py.
#
# `CASSETTE` names the recording — one per journey keeps the diffs small.
CASSETTE ?= all

scenarios-record:
	@echo "→ Recording against the real providers…"
	@cd $(SCENARIOS_DIR) && SCENARIOS_EGRESS=record SCENARIOS_CASSETTE="$(CASSETTE)" \
		SCENARIOS_USE_DEPLOYMENT_ENV=1 SCENARIOS_LLM_MODE=real \
		uv run pytest -q --timeout=900 $(ARGS)

scenarios-replay:
	@echo "→ Replaying $(CASSETTE)…"
	@cd $(SCENARIOS_DIR) && SCENARIOS_EGRESS=replay SCENARIOS_CASSETTE="$(CASSETTE)" \
		uv run pytest -q $(ARGS)

# ── The standing tenant ───────────────────────────────────────────────────────
# The suite runs as a fixed cast of colleagues at Vantage Freight who sign *in*
# rather than up — which is what lets it run against a deployment whose
# registration gates are on, and what keeps a run from leaving another
# organization behind. See tests/scenarios/harness/tenant.py.
#
# `TARGET` names the deployment; `SCENARIOS_BASE_URL` does the same thing if you
# would rather set it once. There is no default, on purpose: these register
# accounts and create organizations, and an organization cannot be deleted.
TARGET ?= $(SCENARIOS_BASE_URL)

# Extra pytest arguments for the desktop lane, so a `-k` filter or `-x` does
# not mean copying the target's body out of here to run one journey.
SCENARIOS_ARGS ?=

# Run once per environment, by a person who can see what it did. Never part of
# a run: this is the only thing that registers anybody, and a deployment counts
# every call to its auth routes. Idempotent — run it twice and the second run
# reports it had nothing to do.
scenarios-provision:
	@echo "→ Provisioning the standing tenant…"
	@cd $(SCENARIOS_DIR) && uv run python -m harness.provision --base-url "$(TARGET)"

# For when a run died partway through and left somebody promoted. Clears what
# runs left behind — matched on the `scn` mark, so it can only ever touch
# things a run made — and puts the cast's roles back to what tenant.py declares.
scenarios-reset:
	@echo "→ Resetting the standing tenant…"
	@cd $(SCENARIOS_DIR) && uv run python -m harness.provision --reset --base-url "$(TARGET)"

# The suite against a real Lemma, with whatever model that deployment is
# configured with. Boots nothing; the tenant must already be there.
scenarios-deployment:
	@echo "→ Product scenarios against $(TARGET)…"
	@test -n "$(TARGET)" || { echo "set TARGET=https://your-lemma (or SCENARIOS_BASE_URL)"; exit 1; }
	@cd $(SCENARIOS_DIR) && uv run pytest -q --base-url "$(TARGET)" --timeout=900

# The suite against the Lemma Desktop install running on this machine.
#
# This is the widest coverage the desktop build has by a distance: the journey
# scenarios exercise orgs, pods, tables, files, agents, functions, workflows,
# schedules, bundles and app publishing, and against a desktop install they do
# it through the real host pack, the real guest VM and the real services rather
# than a stack booted for the occasion. No new test code -- `--base-url` was
# already plumbed; what was missing was the address, which locald allocates at
# first launch and so cannot be written down here.
#
# Provisioning is separate and deliberate: a deployment run refuses to register
# anybody (see `sessions` in tests/scenarios/conftest.py), so the standing cast
# has to exist first. Run `make scenarios-desktop-provision` once per install.
#
# SCENARIOS_TARGET_INSTANCE_ID is set from the install's own id, so if the app
# is restarted onto a different install mid-session the run stops instead of
# writing into it -- the suite creates organizations and the product cannot
# delete one.
scenarios-desktop:
	@set -e; \
	eval "$$(python3 desktop/e2e/install_address.py)"; \
	echo "→ Product scenarios against the desktop install at $$LEMMA_DESKTOP_API_URL…"; \
	cd $(SCENARIOS_DIR) && \
	SCENARIOS_TARGET_INSTANCE_ID="$$LEMMA_DESKTOP_INSTANCE_ID" \
	uv run pytest -q --base-url "$$LEMMA_DESKTOP_API_URL" --timeout=900 $(SCENARIOS_ARGS)

# The standing cast, on the desktop install. Once per install, not per run.
#
# A never-provisioned install trips the harness's own guard, which stops before
# making anything and asks for SCENARIOS_ALLOW_NEW_CAST=1. That is deliberate
# and is not passed for you: on a shared deployment the same symptom means the
# cast already exists under different addresses, and answering it blindly would
# build a second parallel one out of organizations nothing can delete. On a
# fresh install it just means "yes, this is the first time":
#
#   make scenarios-desktop-provision SCENARIOS_ALLOW_NEW_CAST=1
scenarios-desktop-provision:
	@set -e; \
	eval "$$(python3 desktop/e2e/install_address.py)"; \
	echo "→ Provisioning the standing tenant on $$LEMMA_DESKTOP_API_URL…"; \
	cd $(SCENARIOS_DIR) && \
	SCENARIOS_TARGET_INSTANCE_ID="$$LEMMA_DESKTOP_INSTANCE_ID" \
	SCENARIOS_ALLOW_NEW_CAST="$(SCENARIOS_ALLOW_NEW_CAST)" \
	uv run python -m harness.provision --base-url "$$LEMMA_DESKTOP_API_URL"

# The guards on the suite itself: no imports of the app under test, no mocking,
# no sleeping, every test declaring what it proves. No docker, no stack, ~20ms —
# so this is the one to run in a tight loop while writing scenarios.
scenarios-guards:
	@echo "→ Scenario suite guards…"
	@cd $(SCENARIOS_DIR) && uv run pytest journeys/test_harness_contract.py -q

# Infrastructure that stands between runs, so a connected account survives.
#
# GitHub, Slack and Gmail accounts exist only after a person consented in a
# browser, and the product has no way to store one without that. A throwaway
# database therefore discards the one thing the suite cannot recreate for
# itself — so every re-run asked somebody to click through OAuth again.
#
# Opt-in: `SCENARIOS_STANDING_STACK=1 make scenarios`. Anything that would be
# rude to leave behind is removed by the target below.
scenarios-standing-down:
	@echo "→ Removing the standing scenario infrastructure…"
	@docker rm -f lemma-scenarios-postgres lemma-scenarios-redis \
		lemma-scenarios-supertokens >/dev/null 2>&1 || true
	@docker volume rm lemma-scenarios-postgres-data >/dev/null 2>&1 || true
	@docker network rm lemma-scenarios >/dev/null 2>&1 || true
	@echo "  gone — the tenant and every connected account with it."

# What the scenario suite actually executes in the backend. Instruments the
# uvicorn and worker subprocesses, so this measures the product being driven
# over HTTP rather than functions being called directly.
scenarios-code-coverage:
	@echo "→ Product scenarios under coverage…"
	@cd $(BACKEND_DIR) && uv run coverage erase
	@cd $(SCENARIOS_DIR) && SCENARIOS_COVERAGE=1 uv run pytest -q || true
	@cd $(BACKEND_DIR) && uv run coverage combine && uv run coverage report | tail -30

# Regenerate docs/product/coverage.md. `make quality` checks it is current.
scenario-coverage:
	@python3 scripts/check_scenario_coverage.py --write

# ── Coverage ──────────────────────────────────────────────────────────────────

coverage: coverage-backend-unit coverage-backend-e2e coverage-cli coverage-frontend
	@echo ""
	@echo "✓ Coverage reports written:"
	@echo "    $(BACKEND_DIR)/coverage-unit.xml"
	@echo "    $(BACKEND_DIR)/coverage-e2e.xml"

coverage-backend: coverage-backend-unit coverage-backend-e2e

# `local_guest` and `provider` are excluded explicitly rather than left to skip.
#
# The seventeen `local_guest` tests need Lemma Desktop installed with its VM
# booted, and the three `provider` ones need real E2B credentials. Neither is
# marked `e2e` -- so `-m "not e2e"` collected all twenty on every backend CI run
# and they reported green skips. A suite that always skips is indistinguishable
# from one that has quietly stopped existing. Deselecting says so in the
# summary instead, and `scripts/check_pytest_census.py` now fails if another
# environment-gated suite drifts back into this lane.
#
# Keep this expression identical to `test-backend-unit`'s and to the two
# entries in that script's LANES table.
coverage-backend-unit:
	@echo "→ Backend unit coverage…"
	@cd $(BACKEND_DIR) && uv run pytest -m "$(UNIT_MARKERS)" \
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

# Ruff cannot float. Two versions disagree about formatting output, and 0.16
# widened the default rule selection enough to turn 1 finding into 711 on the
# same tree -- so both the `--check` gate and `lint` have to name a version.
#
# Most components resolve one from their own `ruff>=0.14.8,<0.16` bound and use
# `uv run ruff`. `lemma-pod-bundle` and `tests/scenarios` carry no ruff
# dependency, so this is where they get one. EVERY PLACE THAT NAMES A VERSION
# has to move together: this line and that bound in each pyproject.
RUFF := uvx ruff@0.15.22

# ── Lint ──────────────────────────────────────────────────────────────────────

# Every component's linter, and all four can fail. Three of them used to end
# in `2>/dev/null || true`, so `make lint` printed four arrows and could only
# ever report the backend -- a green run here meant nothing for the other
# three. Components whose toolchain is not installed are skipped out loud
# rather than silently passed.
lint:
	@echo "→ Backend (ruff)…"
	@# Delegates rather than running `ruff check .`, which walked into the
	@# vendored lemma-backend/lemma-connectors tree and failed on generated
	@# code. That is why this target had been red for a while without anyone
	@# noticing: the backend line was the one line here that could fail, and
	@# `make quality` -- the documented gate -- calls the scoped target below.
	@cd $(BACKEND_DIR) && $(MAKE) --no-print-directory lint
	@$(MAKE) --no-print-directory lint-clients
	@echo "→ Frontend (eslint)…"
	@if [ -d $(FRONTEND_DIR)/node_modules ]; then \
		cd $(FRONTEND_DIR) && npm run lint --silent; \
	else \
		echo "  skipped: run 'npm ci' in $(FRONTEND_DIR) first"; \
	fi

# Every first-party Python package except the backend, which has its own gates.
# Extracted from `lint` so that `quality` -- the documented pre-PR command and
# the one CI runs -- covers them too. It did not: four of these were linted by
# `make lint`, which no CI job called, so a finding in the CLI or the scenario
# suite reached main unnoticed.
# All five run the same pinned ruff rather than each project's own venv: linting
# needs no imports, so a venv per package bought nothing and made this the only
# gate that could not run on a clean checkout without syncing three projects.
lint-clients:
	@echo "→ CLI (ruff)…"
	@cd $(CLI_DIR) && $(RUFF) check . --quiet
	@echo "→ Python SDK (ruff)…"
	@cd $(PYTHON_DIR) && $(RUFF) check . --quiet
	@echo "→ Stack (ruff)…"
	@cd $(STACK_DIR) && $(RUFF) check . --quiet
	@echo "→ Pod bundle (ruff)…"
	@cd $(BUNDLE_DIR) && $(RUFF) check . --quiet
	@echo "→ Scenarios (ruff)…"
	@cd $(SCENARIOS_DIR) && $(RUFF) check . --quiet

# Every project whose uv.lock can go stale, which is every one that depends on a
# sibling by path: change a dependency in `lemma-python` and eight other locks
# are suddenly out of date. Two of these are the sandbox image templates, and
# their Dockerfiles run `uv sync --locked` -- so a stale lock is not a warning
# there, it fails the image build. That is a slow way to find out: it cost two
# CI round trips before this gate existed, once for each template.
#
# `--check` resolves without writing, and the whole sweep is well under a second.
LOCKED_PROJECTS = \
	$(BACKEND_DIR) \
	$(BACKEND_DIR)/lemma-connectors \
	$(BACKEND_DIR)/sandbox-images/templates/function-python \
	$(BACKEND_DIR)/sandbox-images/templates/workspace-python \
	$(CLI_DIR) \
	$(PYTHON_DIR) \
	$(BUNDLE_DIR) \
	$(STACK_DIR) \
	$(SCENARIOS_DIR)

lint-lockfiles:
	@for project in $(LOCKED_PROJECTS); do \
		(cd $$project && uv lock --check --quiet) \
			|| { echo "  $$project/uv.lock is stale — run 'uv lock' there"; exit 1; }; \
	done
	@echo "9 lockfiles current."

# ── Format ────────────────────────────────────────────────────────────────────
#
# Every first-party Python file is `ruff format` clean. Generated trees are
# excluded and stay excluded: `lemma-backend/lemma-connectors/` comes from
# provider OpenAPI specs and `lemma-python/lemma_sdk/openapi_client/` from the
# API spec, so formatting either one would be reverted by the next generation
# and read as codegen drift.
#
# `format-check` is part of `quality`, so formatting is a merge requirement.
# It was held back through the reformat so open branches could rebase first;
# in the fortnight that took, main merged seven unformatted files that the next
# branch had to re-flatten. Enforced is the only state that stays true.
SDK_FORMAT_EXCLUDE = --exclude lemma_sdk/openapi_client



format:
	@echo "→ Backend…"
	@cd $(BACKEND_DIR) && $(MAKE) --no-print-directory format
	@echo "→ CLI…"
	@cd $(CLI_DIR) && $(RUFF) format .
	@echo "→ Python SDK…"
	@cd $(PYTHON_DIR) && $(RUFF) format $(SDK_FORMAT_EXCLUDE) .
	@echo "→ Stack…"
	@cd $(STACK_DIR) && $(RUFF) format .
	@echo "→ Pod bundle…"
	@cd $(BUNDLE_DIR) && $(RUFF) format .
	@# Scenarios was in `lint` but in neither of these, so `ruff check` held
	@# while formatting drifted across 51 files. A directory that is checked
	@# but never formatted is the one that drifts, because nothing says so.
	@echo "→ Scenarios…"
	@cd $(SCENARIOS_DIR) && $(RUFF) format .

format-check:
	@echo "→ Backend…"
	@cd $(BACKEND_DIR) && $(MAKE) --no-print-directory format-check
	@echo "→ CLI…"
	@cd $(CLI_DIR) && $(RUFF) format --check .
	@echo "→ Python SDK…"
	@cd $(PYTHON_DIR) && $(RUFF) format --check $(SDK_FORMAT_EXCLUDE) .
	@echo "→ Stack…"
	@cd $(STACK_DIR) && $(RUFF) format --check .
	@echo "→ Pod bundle…"
	@cd $(BUNDLE_DIR) && $(RUFF) format --check .
	@echo "→ Scenarios…"
	@cd $(SCENARIOS_DIR) && $(RUFF) format --check .

# ── Static analysis ───────────────────────────────────────────────────────────
#
# `lint` above is the fast pass. These are the gates CI actually blocks on, so
# that a red PR is something you can reproduce and fix here instead of learning
# about it twenty minutes after pushing.

# Everything the "lemma-backend quality gates" job runs, in its order — except
# the runtime connection-scope suite, which needs Docker. Run that with
# `make -C lemma-backend test-connection-scope`. Stated rather than implied,
# because the last comment here claiming parity was wrong for weeks.
#
# `lint-e2e-waits` used to be the other exception, and it had no reason to be:
# it is an AST pass over the test tree that needs nothing running, so leaving
# it out only meant learning about a new clock-wait from CI.
quality:
	@echo "→ Formatting…"
	@$(MAKE) --no-print-directory format-check
	@echo "→ Ruff…"
	@cd $(BACKEND_DIR) && $(MAKE) --no-print-directory lint
	@echo "→ Ruff (CLI, SDK, stack, bundle, scenarios)…"
	@$(MAKE) --no-print-directory lint-clients
	@echo "→ Lockfiles…"
	@$(MAKE) --no-print-directory lint-lockfiles
	@echo "→ Client structure (ADVISORY — new, records only)…"
	@$(MAKE) --no-print-directory client-structure-record
	@echo "→ Client types (ADVISORY — new, records only)…"
	@$(MAKE) --no-print-directory client-typecheck-record
	@echo "→ Async-safety…"
	@cd $(BACKEND_DIR) && $(MAKE) --no-print-directory lint-async
	@echo "→ Connector package (ruff, excludes generated clients)…"
	@cd $(BACKEND_DIR) && $(MAKE) --no-print-directory lint-connectors
	@echo "→ DB connection scope…"
	@cd $(BACKEND_DIR) && $(MAKE) --no-print-directory lint-session-scope
	@echo "→ I/O hygiene…"
	@cd $(BACKEND_DIR) && $(MAKE) --no-print-directory lint-io-hygiene
	@cd $(BACKEND_DIR) && $(MAKE) --no-print-directory lint-unbounded
	@echo "→ Import-bound defaults…"
	@cd $(BACKEND_DIR) && $(MAKE) --no-print-directory lint-import-bound-defaults
	@echo "→ Typed surfaces…"
	@cd $(BACKEND_DIR) && $(MAKE) --no-print-directory lint-controller-types
	@echo "→ Swallowed errors…"
	@cd $(BACKEND_DIR) && $(MAKE) --no-print-directory lint-swallowed-errors
	@echo "→ In-subject test doubles…"
	@cd $(BACKEND_DIR) && $(MAKE) --no-print-directory lint-test-doubles
	@echo "→ Import budget…"
	@cd $(BACKEND_DIR) && $(MAKE) --no-print-directory lint-import-budget
	@echo "→ Critical domain types…"
	@cd $(BACKEND_DIR) && $(MAKE) --no-print-directory typecheck-critical
	@echo "→ Architecture ratchet + route inventory…"
	@cd $(BACKEND_DIR) && $(MAKE) --no-print-directory architecture
	@echo "→ Logging event catalog freshness…"
	@# The catalog is generated from the literal logger calls in the tree and is
	@# what the logging contract is enforced against. Nothing ran the generator,
	@# so nothing noticed when the two diverged.
	@cd $(BACKEND_DIR) && uv run python ../scripts/generate_logging_event_catalogs.py --check
	@echo "→ OpenAPI spec freshness…"
	@cd $(BACKEND_DIR) && uv run python scripts/dump_openapi_spec.py --check
	@echo "→ Module contract coverage…"
	@cd $(BACKEND_DIR) && uv run python scripts/check_contracts.py
	@echo "→ E2E wait patterns…"
	@cd $(BACKEND_DIR) && $(MAKE) --no-print-directory lint-e2e-waits
	@echo "→ Local domain lists…"
	@$(MAKE) --no-print-directory local-domain-check
	@echo "→ Local auth gates…"
	@$(MAKE) --no-print-directory local-auth-gate-check
	@echo "→ Script portability…"
	@$(MAKE) --no-print-directory script-portability-check
	@echo "→ CI aggregators + job timeouts…"
	@cd $(BACKEND_DIR) && uv run python ../scripts/check_ci_aggregators.py
	@echo "→ Test census (no suite has quietly stopped running)…"
	@python3 scripts/check_pytest_census.py
	@echo "→ E2E shard layout…"
	@python3 scripts/plan_e2e_shards.py --verify
	@echo "→ Product scenario traceability…"
	@python3 scripts/check_scenario_coverage.py
	@echo "✓ quality gates pass"

# The backend's architecture ratchet, from the repo root. AGENTS.md and
# CONTRIBUTING.md both tell you to run `make architecture`; until now only one
# of them mentioned that it exists solely inside lemma-backend.
architecture:
	@cd $(BACKEND_DIR) && $(MAKE) --no-print-directory architecture

# ── Client measurement (advisory) ─────────────────────────────────────────────
#
# `quality` runs twenty-odd gates and fifteen of them begin `cd lemma-backend`.
# The two packages a user actually installs got three: `format-check`, a bare
# `ruff check`, and the lockfile sweep. Nothing counted a file's length, a
# function's branchiness or an annotation that gave up -- so all three grew with
# no number attached, and the first measurement found nine CLI files over the
# backend's 600-line ceiling and a command function scoring 103 against a
# backend worst case of 56.
#
# Both targets below are ADVISORY: they compare against a recorded baseline,
# print anything that grew, and exit 0. That is deliberate and temporary. A
# baseline this size arms into a gate that fails a hundred unrelated branches on
# the day it is taken, and a gate people have to route around teaches that gates
# can be routed around. Drop `--advisory` from each line to arm the ratchet;
# nothing else has to change.
measure-clients: client-structure-record client-typecheck-record

# Size, complexity and untyped escapes, at lemma-backend's own thresholds.
# Through `uv run` from lemma-cli rather than a bare `python3`: it parses 3.14
# source, and macOS's system interpreter reports valid PEP 758 syntax as a
# SyntaxError.
client-structure-record:
	@cd $(CLI_DIR) && uv run python ../scripts/check_client_structure.py --advisory

# basedpyright over lemma_cli and lemma_sdk. Runs in each project's own
# environment -- outside it, every `typer`/`textual`/`httpx` import is an error
# that says nothing about this code. That has a price worth stating: this is
# the only step in `quality` that installs an environment other than the
# backend's, so the CI job now syncs lemma-cli and lemma-python too. Locally
# they are already synced and it costs about four seconds.
client-typecheck-record:
	@python3 scripts/check_client_types.py --advisory

# The tight loop before pushing: the gates that catch the most per second.
# `quality` is the full pre-PR pass, but two of its steps import the whole app
# (~16s each), which is too slow to run on every save.
# A third list of gates, kept as a "fast subset" of `quality` — which is how it
# came to omit format-check, the swallowed-errors gate and everything covering
# the CLI and the SDKs, and to tell you that you had passed when CI would not
# agree. `quality` is ~35s in total, so the subset was buying seconds and
# costing a category of surprise. One list.
pre-push: quality

# CodeQL, the same suites CI runs. Reports only what this branch changed;
# `codeql-all` reports the repository's full backlog.
codeql:
	@./scripts/run_codeql.sh

codeql-python:
	@./scripts/run_codeql.sh --language python

codeql-javascript:
	@./scripts/run_codeql.sh --language javascript-typescript

codeql-all:
	@./scripts/run_codeql.sh --all

# The frontend half of the pre-PR pass.
#
# `quality` above is entirely Python: `format-check` covers the backend, the
# CLI, both SDKs and the scenarios, and stops at the language boundary. So a
# frontend-only change could pass every gate this repository offers locally and
# still meet eslint, tsc, the design-system audit and the education-anchor
# check for the first time in CI, ten minutes after pushing -- which is the
# shape of gate `rust-toolchain.toml`'s own comment argues against.
#
# Skipped rather than failed when the dependencies are not installed. A backend
# contributor who has never run `npm ci` should not have `make check` break on
# them; CI is the gate, this is the shortcut.
# Missing dependencies are a refusal, not a pass. This used to print "skipped"
# and exit 0, which meant `make check` -- documented as "quality plus the
# frontend plus CodeQL" -- reported success on a machine where not one frontend
# gate had run, and said so in a line that scrolled past.
quality-frontend:
	@if [ ! -d "$(FRONTEND_DIR)/node_modules" ] || [ ! -d "$(TS_DIR)/node_modules" ]; then \
		echo "make: *** cannot run the frontend gates: node_modules is missing."; \
		echo "    run 'npm ci' in $(TS_DIR) and $(FRONTEND_DIR),"; \
		echo "    or run 'make quality' if your change is Python-only."; \
		exit 1; \
	fi
	@echo "→ Frontend lint, types, design audit, education anchors…"
	@cd $(FRONTEND_DIR) && npm run --silent check

# Everything a PR is judged on, short of the test suites themselves.
check: quality quality-frontend codeql

# ── Migrations ────────────────────────────────────────────────────────────────

migrate:
	@echo "→ Applying database migrations…"
	@cd $(BACKEND_DIR) && uv run alembic upgrade head
