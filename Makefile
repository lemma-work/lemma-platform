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
#   make lint          ruff + eslint across all components
#   make format-check  ruff format --check (Python) + prettier --check (TS/JS)
#   make typecheck     pyright (Python) + tsc (frontend + TypeScript SDK)
# ──────────────────────────────────────────────────────────────────────────────

.PHONY: help init dev stop stop-all logs _ensure-databases _ensure-agentbox-image \
        test test-backend test-backend-unit test-backend-e2e \
        test-frontend test-cli test-cli-unit test-cli-e2e test-python \
        test-typescript test-agentbox test-agentbox-client test-agentbox-e2e \
        test-stack test-stack-install test-pod-bundle test-desktop \
        coverage coverage-backend coverage-backend-unit coverage-backend-e2e \
        coverage-backend-module coverage-cli coverage-cli-unit coverage-cli-e2e coverage-frontend \
        lint lint-desktop format-check format-check-desktop \
        typecheck typecheck-python typecheck-frontend typecheck-typescript migrate \
        update-agentbox

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
AGENTBOX_CLIENT_DIR := agentbox-client
STACK_DIR     := lemma-stack
POD_DIR       := lemma-pod-bundle
DESKTOP_DIR   := desktop

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
# ~2.9GB image. docker run auto-pulls a missing image synchronously and
# uncapped — the first sandbox creation after a fresh clone would otherwise
# block on this pull with zero progress feedback, which looks exactly like a
# hung agent ("stuck at exec command, no sandbox created"). Pre-pulling in
# `make init`/`make dev` turns that invisible stall into a visible, one-time
# download.
#
# SECURITY: the runtime image IS the sandbox boundary (agentbox
# runtime_server.py uses `shell=True` paths inside it). Pinning to a digest
# instead of `:latest` means a GHCR namespace compromise or accidental
# republish cannot silently push a malicious image to every `make dev` host.
# The same digest is the one the production lemma-stack installer already
# resolves via release-local-images.yml. To bump after operator review, run
# `make update-agentbox` — it resolves the current `:latest` digest from GHCR
# and rewrites this line in place. The same digest is also written into
# lemma-backend/.env (see _init-backend-env) so operators can see what the
# default is; that .env line is documentation only, not read back by make.
AGENTBOX_RUNTIME_IMAGE := ghcr.io/lemma-work/lemma-agentbox-runtime@sha256:4e85bb6327b18b4b7eaf13b3e53f34718e4211967340e0ac908c1635e422d492

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
	AGENTBOX_RUNTIME_IMAGE=$(AGENTBOX_RUNTIME_IMAGE) \
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
	@echo "    make update-agentbox    bump AGENTBOX_RUNTIME_IMAGE to the latest GHCR digest (operator-reviewed)"
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
	@echo "    make test-typescript   lemma-typescript SDK vitest suite (no bundle rebuild)"
	@echo "    make test-agentbox     agentbox manager unit tests (no e2e)"
	@echo "    make test-agentbox-e2e  agentbox docker-backed e2e (opt-in, needs docker)"
	@echo "    make test-agentbox-client  agentbox-client unit tests"
	@echo "    make test-stack        lemma-stack unit tests (no install_experience)"
	@echo "    make test-stack-install  lemma-stack install_experience tests (opt-in, needs docker)"
	@echo "    make test-pod-bundle   lemma-pod-bundle stdlib-only tests"
	@echo "    make test-desktop      desktop Tauri cargo test (needs cargo)"
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
	@echo "    make lint               ruff + eslint + cargo clippy across all components"
	@echo "    make lint-desktop       cargo clippy for the desktop Tauri crate (Rust)"
	@echo "    make format-check       ruff format --check (Python) + prettier --check (TS/JS) + cargo fmt --check (desktop)"
	@echo "    make typecheck            pyright (Python) + tsc (frontend + TypeScript SDK)"
	@echo "    make typecheck-python     pyright per-package (Python only)"
	@echo "    make typecheck-frontend   tsc --noEmit for lemma-frontend (Next.js)"
	@echo "    make typecheck-typescript tsc --noEmit for lemma-typescript SDK (src + tests)"
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
	@$(MAKE) --no-print-directory _ensure-agentbox-image
	@echo ""
	@echo "Done. Run 'make dev' to start the stack."

_ensure-agentbox-image:
	@if docker image inspect "$(AGENTBOX_RUNTIME_IMAGE)" >/dev/null 2>&1; then \
			echo "  ✓ AgentBox runtime image already present: $(AGENTBOX_RUNTIME_IMAGE)"; \
		else \
			echo "→ Pulling AgentBox runtime image (one-time, ~2.9GB): $(AGENTBOX_RUNTIME_IMAGE)…"; \
			docker pull "$(AGENTBOX_RUNTIME_IMAGE)" && \
			echo "  ✓ AgentBox runtime image ready"; \
	fi

# Bump AGENTBOX_RUNTIME_IMAGE to the digest that GHCR currently serves for
# the upstream :latest tag. The digest is the manifest-list digest, which is
# what docker pull <image>@<digest> accepts (multi-arch index digest, not
# a per-platform one). The current pinned digest is replaced in place on
# the AGENTBOX_RUNTIME_IMAGE := line near the top of this file; review the
# diff before committing. This target is opt-in: make init / make dev
# will never fetch a new digest silently. Requires docker (uses
# docker buildx imagetools inspect, part of the standard docker buildx
# install).
AGENTBOX_IMAGE_REPO := ghcr.io/lemma-work/lemma-agentbox-runtime
update-agentbox:
	@command -v docker >/dev/null 2>&1 || (echo "  ✗ docker not found on PATH"; exit 1)
	@current_digest=$$(printf '$(AGENTBOX_RUNTIME_IMAGE)' | sed -nE 's|.*@(sha256:[0-9a-f]+).*|\1|p'); \
		if [ -z "$$current_digest" ]; then \
			echo "  ✗ Could not parse the pinned digest out of AGENTBOX_RUNTIME_IMAGE=$(AGENTBOX_RUNTIME_IMAGE)"; \
			echo "    (this should be a @sha256:... ref)"; \
			exit 1; \
		fi; \
		new_digest=$$(docker buildx imagetools inspect '$(AGENTBOX_IMAGE_REPO):latest' --format '{{json .Manifest.Digest}}' 2>/dev/null | tr -d '"'); \
		if [ -z "$$new_digest" ] || ! echo "$$new_digest" | grep -qE '^sha256:[0-9a-f]+$$'; then \
			echo "  ✗ Failed to resolve a digest from $(AGENTBOX_IMAGE_REPO):latest"; \
			echo "    Check your network / registry auth and try again."; \
			exit 1; \
		fi; \
		echo "  current digest: $$current_digest"; \
		echo "  newest  digest: $$new_digest"; \
		if [ "$$current_digest" = "$$new_digest" ]; then \
			echo "  ✓ Already up to date — no changes."; \
			exit 0; \
		fi; \
		new_ref='$(AGENTBOX_IMAGE_REPO)@'"$$new_digest"; \
		awk -v old="^AGENTBOX_RUNTIME_IMAGE := .*$$" \
			-v repl="AGENTBOX_RUNTIME_IMAGE := $$new_ref" \
			'BEGIN{replaced=0} { if ($$0 ~ old && !replaced) { print repl; replaced=1; next } print }' \
			$(MAKEFILE_LIST) > $(MAKEFILE_LIST).tmp; \
		mv $(MAKEFILE_LIST).tmp $(MAKEFILE_LIST); \
		echo "  ✓ Bumped $(AGENTBOX_IMAGE_REPO) digest in this Makefile."; \
		echo "    Review the diff, then re-run make init to pull the new image."


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
			echo "# Sandbox runtime image (digest-pinned; bump with make update-agentbox)."; \
			echo "# The dev Makefile passes this value to the manager via the shell env, so"; \
			echo "# editing it here alone will NOT change what the manager uses — also set"; \
			echo "# AGENTBOX_RUNTIME_IMAGE in your shell or re-run make update-agentbox."; \
			echo "AGENTBOX_RUNTIME_IMAGE=$(AGENTBOX_RUNTIME_IMAGE)"; \
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
			echo "# Local dev only: opt in to the deterministic Fernet seed."; \
			echo "# The seed is a public repo constant — anyone with the repo can"; \
			echo "# decrypt your local secrets. For real data, replace this with a"; \
			echo "# real key (generate one with: python -c \"from cryptography.fernet"; \
			echo "# import Fernet; print(Fernet.generate_key().decode())\") and set"; \
			echo "# SECRET_ENCRYPTION_KEY=<key> here instead."; \
			echo "LEMMA_ALLOW_LOCAL_FALLBACK_KEY=true"; \
		} > $(BACKEND_DIR)/.env; \
	else \
		$(MAKE) --no-print-directory _ensure-backend-env-keys; \
	fi

_ensure-backend-env-keys:
	@set -e; missing=""; \
	for k in API_URL FRONTEND_URL AUTH_FRONTEND_URL SUPERTOKENS_CORE_URL DATABASE_URL REDIS_URL CORS_ORIGINS CORS_ORIGIN_REGEX AGENTBOX_API_URL AGENTBOX_API_KEY AGENTBOX_RUNTIME_IMAGE; do \
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
			echo "# Added by make init (sandbox runtime image, digest-pinned)"; \
			echo "AGENTBOX_RUNTIME_IMAGE=$(AGENTBOX_RUNTIME_IMAGE)"; \
		} >> $(BACKEND_DIR)/.env; \
	fi
	@if ! grep -qE '^LEMMA_ALLOW_LOCAL_FALLBACK_KEY=' $(BACKEND_DIR)/.env; then \
		echo "  $(BACKEND_DIR)/.env missing LEMMA_ALLOW_LOCAL_FALLBACK_KEY — appending (dev seed opt-in)…"; \
		{ \
			echo ""; \
			echo "# Added by make init (local-dev opt-in to the deterministic Fernet seed)"; \
			echo "# The seed is public — remove this line and set SECRET_ENCRYPTION_KEY instead for any real data."; \
			echo "LEMMA_ALLOW_LOCAL_FALLBACK_KEY=true"; \
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
	@$(MAKE) --no-print-directory _ensure-agentbox-image
	@$(MAKE) --no-print-directory _infra-up
	@$(MAKE) --no-print-directory _wait-infra
	@$(MAKE) --no-print-directory _ensure-databases
	@$(MAKE) --no-print-directory migrate
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

# Root `make test` runs every package's fast/offline suite. Docker-backed or
# live-service integration tests are kept under separate explicit targets
# (`test-agentbox-e2e`, `test-stack-install`, `test-backend-e2e-full`, …) so
# they stay opt-in — the same convention already used for `test-cli-e2e`.
test: test-backend-unit test-backend-e2e test-cli test-python test-frontend \
      test-typescript test-agentbox test-agentbox-client \
      test-stack test-pod-bundle test-desktop
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

# lemma-typescript: `npm test` runs `build:bundle && vitest run`, which rebuilds
# the browser bundle on every invocation — slow and unnecessary because the
# bundle is checked in at public/lemma-client.js (only browser-bundle.test.ts
# reads it, and the file is committed). Invoke vitest directly via `npx vitest
# run` to skip the rebuild while still covering every src/**/*.{test,spec}.ts
# per vitest.config.ts. Per https://vitest.dev/guide/cli, `vitest run` runs the
# suite once and exits with non-zero on failure (CI-friendly; the default
# `vitest` command is the watch-mode REPL and is the wrong choice for CI).
test-typescript:
	@echo "→ TypeScript SDK tests (vitest, no bundle rebuild)…"
	@cd $(TS_DIR) && npx vitest run

# agentbox: top-level tests mock docker/kubernetes and run fully in-process via
# pytest-asyncio (no docker daemon required). The e2e suite under tests/e2e/
# builds a real AgentBox runtime image and requires a running docker daemon —
# kept behind a separate opt-in target. agentbox/agentbox/config.py
# instantiates pydantic Settings() at import time and requires AGENTBOX_API_KEY
# + AGENTBOX_API_URL; the values are never read by the offline tests (they are
# monkeypatched per-test), so the dev defaults from _init-backend-env are
# sufficient to satisfy Settings() without touching application code.
test-agentbox:
	@echo "→ Agentbox manager unit tests (no e2e)…"
	@cd $(AGENTBOX_DIR) && \
		PYTHONPATH=. \
		AGENTBOX_API_KEY=$${AGENTBOX_API_KEY:-$(DEV_AGENTBOX_API_KEY)} \
		AGENTBOX_API_URL=$${AGENTBOX_API_URL:-$(DEV_AGENTBOX_URL)} \
		uv run pytest -m "not e2e" -q

test-agentbox-e2e:
	@echo "→ Agentbox docker-backed e2e tests (needs docker, slow)…"
	@cd $(AGENTBOX_DIR) && uv run pytest -m e2e -q

# agentbox-client: the only package-level tests are httpx-based unit tests —
# no e2e, no docker, no kubernetes. `uv run pytest` discovers tests/ via the
# default testpaths (no [tool.pytest.ini_options] in pyproject.toml).
test-agentbox-client:
	@echo "→ Agentbox client unit tests…"
	@cd $(AGENTBOX_CLIENT_DIR) && uv run pytest -q

# lemma-stack: most tests are pure-Python (manifest, config store, register,
# resolve_provider, specs, supervise). The `install_experience` marker flags
# Docker-based cross-platform install tests (slow, opt-in). The marker is
# declared in pyproject.toml's [tool.pytest.ini_options].
test-stack:
	@echo "→ Lemma stack unit tests (no install_experience)…"
	@cd $(STACK_DIR) && uv run pytest -m "not install_experience" -q

test-stack-install:
	@echo "→ Lemma stack install_experience tests (needs docker, slow)…"
	@cd $(STACK_DIR) && uv run pytest -m install_experience -q

# lemma-pod-bundle: stdlib-only package — pyproject.toml's [dependency-groups]
# has no pytest entry, so follow the same `uv run --with pytest` pattern used
# by test-python.
test-pod-bundle:
	@echo "→ Lemma pod bundle tests (stdlib only)…"
	@cd $(POD_DIR) && uv run --with pytest pytest tests/ -q

# desktop: Tauri (Rust) crate. Per the Cargo Book
# (https://doc.rust-lang.org/cargo/commands/cargo-test.html), `cargo test` runs
# unit + integration tests for the crate in the current working directory;
# `--manifest-path` scopes the invocation to desktop/Cargo.toml so we never
# pick up an unrelated workspace. cargo is NOT a hard prerequisite of `make
# init` (a fresh clone can build/run the Python + JS stack without Rust), so
# skip the leg gracefully when the toolchain is missing — same pattern as
# `make lint-desktop` / `format-check-desktop` above. Operators without Rust
# get a clear `install from https://rustup.rs/` message instead of a hard
# failure that blocks the rest of `make test`.
test-desktop:
	@{ \
		if ! command -v cargo >/dev/null 2>&1; then \
			echo "  ✗ cargo not found on PATH — install Rust from https://rustup.rs/"; \
			echo "  (skipping desktop tests until cargo is installed)"; \
			exit 0; \
		fi; \
		echo "→ Desktop Tauri crate tests (cargo)…"; \
		cd $(DESKTOP_DIR) && cargo test --manifest-path Cargo.toml; \
	}

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
	@cd $(CLI_DIR) && uv run ruff check . --quiet
	@echo "→ Python SDK (ruff)…"
	@cd $(PYTHON_DIR) && uv run ruff check . --quiet
	@echo "→ Agentbox (ruff)…"
	@cd $(AGENTBOX_DIR) && uv run ruff check . --quiet
	@echo "→ Agentbox client (ruff)…"
	@cd $(AGENTBOX_CLIENT_DIR) && uv run ruff check . --quiet
	@echo "→ Lemma stack (ruff)…"
	@cd $(STACK_DIR) && uv run ruff check . --quiet
	@echo "→ Lemma pod bundle (ruff)…"
	@cd $(POD_DIR) && uv run ruff check . --quiet
	@echo "→ Frontend (eslint)…"
	@cd $(FRONTEND_DIR) && npm run lint --silent 2>/dev/null || true

# ── Desktop (Rust) ─────────────────────────────────────────────────────────────
#
# Wire Rust linting + format-check into the umbrella `make lint` /
# `make format-check` flows. Clippy + rustfmt are separate rustup components
# on top of the default `rustc`+`cargo`, so they're NOT auto-installed by
# `desktop/rust-toolchain.toml` (which deliberately only declares `rustfmt`).
# The Makefile surfaces the exact `rustup component add` command if a tool
# is missing and skips that step with a clear diagnostic instead of failing
# the whole `make lint` flow — that keeps a fresh-clone dev box without a
# Rust toolchain from blocking on the Python/JS linters.
#
# Flags (verified against https://doc.rust-lang.org/cargo/commands/cargo-clippy.html):
#   --manifest-path <PATH>  cargo package selection: operate on this Cargo.toml
#   --all-targets           cargo compile flag: check lib + bins + tests + examples + benches
#   --all-features          cargo feature-selection flag: activate every declared feature
#   -- -D warnings          pass `-D warnings` to rustc: deny all warnings so any
#                           clippy regression fails CI (see rustc lint levels docs)
#
# Scope: desktop/ only — no other workspace is affected. No `clippy.toml` is
# created: per https://doc.rust-lang.org/clippy/configuration.html the
# configuration file format is unstable and may be deprecated, so defaults
# are used. Defaults are the same set every other Rust crate gets, which is
# the safest baseline for a new Tauri project.

lint-desktop:
# cargo dispatches `cargo <sub>` to `cargo-<sub>`. Probe the binary directly
# so we do not depend on any Cargo.toml on disk.
# --manifest-path scopes cargo to desktop/, --all-targets covers
# lib+bin+test+example+bench, --all-features activates every declared
# feature, `-- -D warnings` promotes warnings to errors so any clippy
# regression fails CI (rustc lint-level docs).
	@{ \
		if ! command -v cargo >/dev/null 2>&1; then \
			echo '  ✗ cargo not found on PATH — install Rust from https://rustup.rs/'; \
			echo '  (skipping desktop lint until cargo is installed)'; \
			exit 0; \
		fi; \
		if ! command -v cargo-clippy >/dev/null 2>&1; then \
			echo "  ✗ cargo clippy subcommand unavailable — the 'clippy' rustup component is missing."; \
			echo '    Clippy is a separate rustup component, not part of the default toolchain.'; \
			echo '    Install on demand (the Makefile does NOT auto-install):'; \
			echo '      rustup component add clippy'; \
			echo '  (skipping desktop lint until clippy is installed)'; \
			exit 0; \
		fi; \
		cargo clippy --manifest-path $(DESKTOP_DIR)/Cargo.toml --all-targets --all-features -- -D warnings; \
	}

format-check-desktop:
	@{ \
		if ! command -v cargo >/dev/null 2>&1; then \
			echo '  ✗ cargo not found on PATH — install Rust from https://rustup.rs/'; \
			echo '  (rustfmt is declared in desktop/rust-toolchain.toml and installs on first use)'; \
			echo '  (skipping desktop format-check until cargo is installed)'; \
			exit 0; \
		fi; \
		if ! command -v rustfmt >/dev/null 2>&1; then \
			echo "  ✗ cargo fmt subcommand unavailable — the 'rustfmt' rustup component is missing."; \
			echo '    Install on demand (the Makefile does NOT auto-install):'; \
			echo '      rustup component add rustfmt'; \
			echo '  (skipping desktop format-check until rustfmt is installed)'; \
			exit 0; \
		fi; \
		cargo fmt --check --manifest-path $(DESKTOP_DIR)/Cargo.toml; \
	}

# ── Format check ────────────────────────────────────────────────────────────────
#
# One umbrella target (`make format-check`) fans out to per-language targets so
# callers don't need to know which formatter a package uses:
#
#   Python       ruff format --check per package (7 packages). Ruff's own docs
#                confirm `ruff format --check <path>` "will avoid writing any
#                formatted files back, and instead exit with a non-zero status
#                code upon detecting any unformatted files" — see
#                https://docs.astral.sh/ruff/formatter/. `--quiet` suppresses
#                per-file output; the non-zero exit code on drift is unchanged.
#                Each pyproject.toml carries a [tool.ruff.format] block so the
#                formatter's quote/indent/line-ending style is declarative, not
#                CLI-flag driven.
#
#   TypeScript   `npm run format:check` in lemma-typescript (delegates to
#      SDK        `prettier . --check`). Per https://prettier.io/docs/cli,
#                `prettier . --check` is the documented CI-friendly flag and
#                exits 1 on drift (exit 0 = clean, 2 = Prettier itself errored).
#                Config: .prettierrc.json (style) + .prettierignore (skips
#                generated/vendor/build — node_modules, dist, public/r,
#                src/openapi_client/, etc.).
#
#   Frontend     `npm run format:check` in lemma-frontend (same prettier
#                semantics). Config: .prettierrc.json + .prettierignore (skips
#                node_modules, .next, out, build, next-env.d.ts, public/
#                runtime-config.js, etc.). Next.js auto-generates next-env.d.ts
#                and runtime-config.js — both excluded so their drift doesn't
#                fail the check.
#
#   Desktop      `make format-check-desktop` runs `cargo fmt --check
#                --manifest-path desktop/Cargo.toml`. Per the Cargo Book
#                (https://doc.rust-lang.org/cargo/commands/cargo-fmt.html),
#                `cargo fmt --check` exits non-zero on any formatting drift
#                without modifying any files, and `--manifest-path <PATH>`
#                scopes cargo to a specific Cargo.toml so we never pick up an
#                unrelated workspace. The rust-toolchain.toml in desktop/
#                declares the rustfmt component so it installs on first use;
#                if cargo / rustfmt is missing on the dev box, the target
#                prints the exact `rustup component add rustfmt` command and
#                skips gracefully instead of failing the whole umbrella run —
#                that keeps a fresh clone without a Rust toolchain from
#                blocking on the Python/JS formatter legs.
#
# Package-level scripts this target calls (so contributors can run any leg
# directly without going through the umbrella target):
#   lemma-backend/        uv run ruff format --check .
#   lemma-cli/            uv run ruff format --check .
#   lemma-python/         uv run ruff format --check .
#   agentbox/             uv run ruff format --check .
#   agentbox-client/      uv run ruff format --check .
#   lemma-stack/          uv run ruff format --check .
#   lemma-pod-bundle/     uv run ruff format --check .
#   lemma-typescript/     npm run format:check   (prettier . --check)
#   lemma-frontend/       npm run format:check   (prettier . --check)
#   desktop/              cargo fmt --check --manifest-path Cargo.toml

format-check:
	@echo "→ Backend (ruff format --check)…"
	@cd $(BACKEND_DIR) && uv run ruff format --check . --quiet
	@echo "→ CLI (ruff format --check)…"
	@cd $(CLI_DIR) && uv run ruff format --check . --quiet
	@echo "→ Python SDK (ruff format --check)…"
	@cd $(PYTHON_DIR) && uv run ruff format --check . --quiet
	@echo "→ Agentbox (ruff format --check)…"
	@cd $(AGENTBOX_DIR) && uv run ruff format --check . --quiet
	@echo "→ Agentbox client (ruff format --check)…"
	@cd $(AGENTBOX_CLIENT_DIR) && uv run ruff format --check . --quiet
	@echo "→ Lemma stack (ruff format --check)…"
	@cd $(STACK_DIR) && uv run ruff format --check . --quiet
	@echo "→ Lemma pod bundle (ruff format --check)…"
	@cd $(POD_DIR) && uv run ruff format --check . --quiet
	@echo "→ TypeScript SDK (prettier --check)…"
	@cd $(TS_DIR) && npm run format:check --silent
	@echo "→ Frontend (prettier --check)…"
	@cd $(FRONTEND_DIR) && npm run format:check --silent
	@$(MAKE) --no-print-directory format-check-desktop

# ── Type check ────────────────────────────────────────────────────────────────
#
# One umbrella target (`make typecheck`) fans out to per-language targets so
# callers don't need to know which checker a package uses:
#
#   typecheck-python     pyright across every Python package
#   typecheck-frontend   tsc --noEmit in lemma-frontend (re-uses `npm run typecheck`,
#                        which the frontend already wires up; see lemma-frontend/package.json)
#   typecheck-typescript tsc --noEmit in lemma-typescript SDK against BOTH tsconfig.json
#                        (production source) and tsconfig.test.json (tests, which the
#                        build tsconfig excludes from dist). Per
#                        https://www.typescriptlang.org/docs/handbook/compiler-options.html
#                        `--noEmit` on the CLI suppresses emit regardless of the config's
#                        own emit-related options, and `-p/--project` selects the
#                        tsconfig to compile against.
#
# Pyright is invoked via `uvx` (not `uv run`) so contributors don't need to
# sync the project's venv to type-check — `uvx pyright` resolves into an
# ephemeral tool environment without touching uv.lock. If you have pyright
# already installed you can swap `uvx pyright` for `pyright` directly.
#
# Each Python package's pyproject.toml carries a [tool.pyright] block that
# `extends` the shared baseline in /pyright.toml (typeCheckingMode = "standard"
# + excludes for generated / vendored / cached dirs). No package-level
# pyrightconfig.json is needed: [tool.pyright] is the cleaner monorepo
# option per Pyright's own config docs.

typecheck: typecheck-python typecheck-frontend typecheck-typescript

typecheck-python:
	@echo "→ Pod bundle (pyright)…"
	@cd $(POD_DIR) && uvx pyright
	@echo "→ Lemma SDK (pyright)…"
	@cd $(PYTHON_DIR) && uvx pyright
	@echo "→ Lemma CLI (pyright)…"
	@cd $(CLI_DIR) && uvx pyright
	@echo "→ Lemma stack (pyright)…"
	@cd $(STACK_DIR) && uvx pyright
	@echo "→ Agentbox (pyright)…"
	@cd $(AGENTBOX_DIR) && uvx pyright
	@echo "→ Agentbox client (pyright)…"
	@cd $(AGENTBOX_CLIENT_DIR) && uvx pyright
	@echo "→ Backend connectors (pyright)…"
	@cd $(BACKEND_DIR)/lemma-connectors && uvx pyright
	@echo "→ Backend (pyright)…"
	@cd $(BACKEND_DIR) && uvx pyright

# lemma-frontend already wires `tsc --noEmit --pretty false --skipLibCheck`
# in package.json (script name: `typecheck`), with a `pretypecheck` hook that
# builds lemma-sdk first. Re-use that script so the front-end and SDK stay in
# sync — invoking `tsc` directly from the Makefile would skip the
# pretypecheck build and let the front-end type-check against a stale SDK.
typecheck-frontend:
	@echo "→ Frontend (tsc --noEmit)…"
	@cd $(FRONTEND_DIR) && npm run typecheck --silent

# lemma-typescript has no `typecheck` script — its `build` script runs the
# full emit pipeline (clean + tsc + bundles), which is the opposite of what
# we want here. Run `npx tsc` directly with `-p` to select the right
# tsconfig and `--noEmit` (CLI flag) to suppress output regardless of what
# the tsconfig itself declares (tsconfig.json has no `noEmit` because
# `npm run build` does want to emit; --noEmit on the CLI overrides that for
# the typecheck run).
typecheck-typescript:
	@echo "→ TypeScript SDK src (tsc --noEmit -p tsconfig.json)…"
	@cd $(TS_DIR) && npx tsc --noEmit -p tsconfig.json
	@echo "→ TypeScript SDK tests (tsc --noEmit -p tsconfig.test.json)…"
	@cd $(TS_DIR) && npx tsc --noEmit -p tsconfig.test.json

# ── Migrations ────────────────────────────────────────────────────────────────

migrate:
	@echo "→ Applying database migrations…"
	@cd $(BACKEND_DIR) && uv run alembic upgrade head
