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

# What the unit lane selects. Every exclusion is an environment-gated suite
# that would otherwise be collected and skipped, and a suite that always skips
# reports the same green as a suite that proves something.
#
# This is the expression CI runs -- the job sets `working-directory:
# lemma-backend`, so `make coverage-backend-unit` resolves here and not to the
# same-named target in the root Makefile. The two had already drifted: the root
# one excluded `local_guest`, this one did not, so the fix went in where CI
# could not see it. `scripts/check_pytest_census.py` reads both and fails if
# they differ again.
UNIT_MARKERS ?= not e2e and not local_guest and not local_host and not desktop_e2e and not provider

# The marker filter a CI shard runs under. Defaults to the standard fast-lane
# exclusions (matches test-e2e-fast); a shard whose module structurally needs
# real sandbox execution to be covered at all (function -- see e2e.yml) can
# override this to drop "not workspace" instead of forking the whole target.
E2E_SHARD_MARKERS ?= e2e and not slow and not workspace and not provider and not local_cli and not indexing and not protected

# Load test settings (override on the command line)
LOAD_TEST_COMPOSE := load_tests/docker-compose.load-test.yml
LOAD_TEST_ENV     := load_tests/.env.load_test
DATASTORE_LOAD_TEST_COMPOSE := -f $(LOAD_TEST_COMPOSE) -f load_tests/docker-compose.datastore.yml
MAX_SUBSCRIBERS   ?= 100
WRITER_VUS        ?= 2
# Full-journey load test (load-test-journey): each VU self-provisions then loops
# the chat message. Uses the in-process mock LLM (E2E_LLM_MODE=mock in compose).
MAX_USERS         ?= 100
THINK_MS          ?= 500
LEMMA_API_URL     ?= http://localhost:8000

BACKEND_COVERAGE_DIR ?= coverage-backend
BACKEND_COVERAGE_FILE ?= $(CURDIR)/.coverage.backend
BACKEND_COVERAGE_RUN_ENV = COVERAGE_PROCESS_START=$(CURDIR)/.coveragerc COVERAGE_FILE=$(BACKEND_COVERAGE_FILE)
BACKEND_COVERAGE_DATA_ENV = COVERAGE_FILE=$(BACKEND_COVERAGE_FILE)

# Full-path function quality benchmark. E2B identifiers are exact immutable
# test inputs; this target never changes templates, aliases, or account state
# beyond creating and exactly deleting its own tracked sandboxes. The E2B API
# key and immutable profile IDs are read from lemma-backend/.env or the
# environment.
FUNCTION_BENCH_CONCURRENCY ?= 5
# 20, not 5: `_percentile` puts p95 at `(n - 1) * 0.95`, so five samples make
# the "p95" budgets the slowest of five. See load_tests/function_execution.py.
FUNCTION_BENCH_INVOCATIONS ?= 20
FUNCTION_BENCH_BATCH_ROWS ?= 1000
FUNCTION_BENCH_POLL_INTERVAL_SECONDS ?= 0.5
FUNCTION_BENCH_TERMINAL_TIMEOUT_SECONDS ?= 300
FUNCTION_BENCH_TUNNEL ?= ngrok
# Signs the per-sandbox runtime credential. The backend provisions sandboxes
# itself, so the benchmark process needs this key; override it to match a
# deployment you are pointing at.
FUNCTION_BENCH_RUNTIME_CREDENTIAL_KEY ?= benchmark-runtime-credential-key-32-bytes
FUNCTION_BENCH_ENV = \
	E2E_REAL=1 \
	DEBUG=false \
	LOG_LEVEL=WARNING \
	WORKSPACE_RUNTIME_CREDENTIAL_KEY=$(FUNCTION_BENCH_RUNTIME_CREDENTIAL_KEY) \
	FUNCTION_BENCH_CONCURRENCY=$(FUNCTION_BENCH_CONCURRENCY) \
	FUNCTION_BENCH_INVOCATIONS=$(FUNCTION_BENCH_INVOCATIONS) \
	FUNCTION_BENCH_BATCH_ROWS=$(FUNCTION_BENCH_BATCH_ROWS) \
	FUNCTION_BENCH_POLL_INTERVAL_SECONDS=$(FUNCTION_BENCH_POLL_INTERVAL_SECONDS) \
	FUNCTION_BENCH_TERMINAL_TIMEOUT_SECONDS=$(FUNCTION_BENCH_TERMINAL_TIMEOUT_SECONDS)
FUNCTION_BENCH_TEST = app/modules/function/tests/perf/test_function_execution_benchmark.py

.PHONY: help test test-unit test-e2e test-e2e-real test-e2e-real-llm test-e2e-fast test-e2e-runtime test-module coverage coverage-unit coverage-e2e coverage-backend-unit coverage-backend-e2e-shard coverage-backend-report coverage-backend-gate coverage-baseline test-collect lint lint-connectors test-connectors lint-async lint-session-scope lint-io-hygiene lint-unbounded lint-import-bound-defaults lint-controller-types lint-swallowed-errors lint-test-doubles lint-e2e-waits lint-import-budget test-e2e-shard-ci test-connection-scope typecheck-critical architecture migrate docker-build docker-build-cloud docker-build-arm64 docker-build-amd64 benchmark-functions-docker benchmark-functions-e2b teams-manifest-zip load-test-build load-test-up load-test-migrate load-test-setup load-test-run load-test-sse load-test-journey load-test-datastore-up load-test-datastore-setup load-test-datastore-files load-test-datastore-stats load-test-datastore-down load-test-monitor load-test-profile load-test-down load-test docker-reap

help:
	@echo "Lemma backend:"
	@echo "  make test              # run backend tests"
	@echo "  make test-unit         # run non-e2e tests"
	@echo "  make test-e2e          # run all e2e tests"
	@echo "  make test-e2e-fast     # hermetic API/worker e2e (no slow/workspace/provider/local_cli),"
	@echo "                         #   parallel via pytest-xdist (E2E_WORKERS=$(E2E_WORKERS); auto|0 to override)"
	@echo "  make test-e2e-runtime  # run slow worker/workspace/local/provider e2e tests (serial)"
	@echo "  make test-module MODULE=pod"
	@echo "  make coverage-unit     # unit coverage"
	@echo "  make coverage-e2e      # e2e coverage"
	@echo "  make coverage-backend-unit     # backend unit coverage + markdown"
	@echo "  make coverage-backend-e2e-shard # backend e2e shard coverage + markdown"
	@echo "  make lint              # run ruff checks"
	@echo "  make lint-async        # blocking-call gate for async code"
	@echo "  make lint-session-scope # DB connection held across non-DB work"
	@echo "  make lint-io-hygiene   # unbounded thread offloads / untimed aiohttp"
	@echo "  make lint-unbounded    # unbounded queues/caches, CPU on the loop"
	@echo "  make lint-import-bound-defaults  # defaults a test cannot replace"
	@echo "  make lint-controller-types  # new basedpyright errors in the typed surfaces"
	@echo "  make test-e2e-shard-ci SHARD=sandbox   # one CI shard, exactly as CI runs it"
	@echo "  make lint-swallowed-errors # broad excepts that leave no trace"
	@echo "  make lint-test-doubles     # tests that patch inside their own subject"
	@echo "  make lint-e2e-waits    # hand-rolled e2e poll loops, not waiters.eventually()"
	@echo "  make test-connection-scope # runtime: no connection held across non-DB work"
	@echo "  make typecheck-critical # strict reliability/domain type gate"
	@echo "  make architecture      # dependency/complexity/exception ratchet"
	@echo "  make migrate           # apply database migrations"
	@echo "  make docker-build-cloud  # build the lemma-cloud image"
	@echo "  (full dev workflow: use 'make dev STACK=cloud|oss' from the repo root)"
	@echo "  make teams-manifest-zip OUTPUT=path"
	@echo "                         # package Teams manifest zip"
	@echo "  make docker-build      # build backend image from monorepo root"
	@echo "  make benchmark-functions-docker # no-op + single/batch API/JOB benchmark on Docker"
	@echo "  make benchmark-functions-e2b    # same benchmark on pinned E2B builds"
	@echo ""
	@echo "Load testing (WebSocket capacity on a simulated 2 CPU / 2 GB server):"
	@echo "  make load-test         # full end-to-end: build → up → setup → run → stats"
	@echo "  make load-test-build   # build lemma-backend:load-test Docker image"
	@echo "  make load-test-up      # start infra + API + worker with resource limits"
	@echo "  make load-test-migrate # run alembic migrations inside the API container"
	@echo "  make load-test-setup   # provision test user / pod / table, write .env.load_test"
	@echo "  make load-test-run     # run k6 WebSocket load test (reads .env.load_test)"
	@echo "  make load-test-sse     # run k6 SSE streaming load test"
	@echo "  make load-test-journey # run k6 full chat-journey test (self-provisioning, mock LLM)"
	@echo "  make load-test-datastore-files # upload and fully process 20 fixture PDFs"
	@echo "                         #   see docs/operators/datastore-ingestion-benchmark.md"
	@echo "                         #   knobs: MAX_USERS=100 THINK_MS=500"
	@echo "  make load-test-monitor # monitor DB connections (pg_stat_activity poller)"
	@echo "  make load-test-profile # one run, correlated: k6 latency + memory floor +"
	@echo "                         #   DB connections + loop-stall stacks, one directory"
	@echo "                         #   PROFILE=journey|ws|sse|micro USERS=20"
	@echo "  make load-test-down    # tear down containers and clean up"
	@echo "  Override: MAX_SUBSCRIBERS=200 WRITER_VUS=4 make load-test"
	@echo ""
	@echo "  make docker-reap       # remove stopped lemma.e2e / sandbox / load-test"
	@echo "                         #   Docker containers+volumes past their grace"
	@echo "                         #   period. Safe to run anytime, including next"
	@echo "                         #   to a live test run — never touches anything"
	@echo "                         #   still running. Run this if your machine is"
	@echo "                         #   sluggish from accumulated test leftovers."
	@echo ""
	@echo "Run app-level commands such as setup/start/logs from the repository root."

test:
	uv run pytest

test-unit:
	uv run pytest -m "$(UNIT_MARKERS)"

# Default e2e gate: fast + deterministic. The agent LLM is a mocked
# FunctionModel and workspace tools hit the in-process fake sandbox, so there
# is NO real model call and NO Docker workspace. Parallelized via xdist. This is
# what CI runs (no API keys, no Docker image needed). real_llm/real_sandbox
# tests auto-skip here.
test-e2e:
	uv run pytest $(E2E_PARALLEL) -m e2e -o timeout=120

# Real e2e: real model from env (needs LEMMA_OPENAI_API_KEY) + real Docker
# sandbox. Runs the real_llm/real_sandbox tests too. For local/manual/nightly
# verification of the actual model + sandbox. Serial (Docker workspace shares a
# global container namespace).
test-e2e-real:
	E2E_REAL=1 uv run pytest -m e2e

test-e2e-fast:
	uv run pytest $(E2E_PARALLEL) -m "e2e and not slow and not workspace and not provider and not local_cli and not indexing and not protected"

# One CI shard of the fast suite: same marker filter, scoped to the paths (or
# --ignore excludes) passed in E2E_ARGS. CI runs several shards in parallel on
# separate runners, each serial (E2E_WORKERS=1) — parallelism across machines
# instead of xdist workers within one, which avoids the shared-container
# contention that makes -n2 flaky. The union of all shards' E2E_ARGS must equal
# the whole fast suite (the last shard is a catch-all via --ignore).
test-e2e-shard:
	uv run pytest $(E2E_PARALLEL) $(E2E_ARGS) -m "$(E2E_SHARD_MARKERS)" -o timeout=120

# Reproduce one CI shard by name, taking its paths, worker count and marker
# filter from `.github/e2e-shards.json` -- the same file the workflow reads.
#
# Prefer this to `test-e2e-shard` for anything you are checking against CI. That
# target used to hard-code the fast-lane filter and ignore E2E_SHARD_MARKERS
# entirely, so reproducing the `sandbox` shard locally deselected every
# Docker-sandbox test and passed while the shard failed on the pull request.
test-e2e-shard-ci:
	@test -n "$(SHARD)" || (echo "SHARD is required -- one of:" && uv run python ../scripts/run_e2e_shard.py --list && exit 1)
	uv run python ../scripts/run_e2e_shard.py $(SHARD)

# Kreuzberg-backed indexing/search e2e (real document extraction). Split out of
# test-e2e-fast so that job stays fast and never boots the RAM-heavy Kreuzberg
# container; these are still mocked (Kreuzberg only — no model key, no Docker
# sandbox) and run as their own CI job so indexing/authz coverage isn't lost.
test-e2e-indexing:
	uv run pytest $(E2E_PARALLEL) -m "e2e and indexing and not provider and not local_cli" -o timeout=180

# Real runtime suite only (real model + Docker), serial. Subset of test-e2e-real.
# The real-model agent suite. Every `real_llm` test lives here and, until this
# target existed, ran in no lane at all: the PR lane uses the mock, and the
# weekly protected lane sets E2E_REAL=1 *and* E2E_LLM_MODE=mock, which
# conftest reads first -- so they skipped there too.
#
# Reads the key from lemma-backend/.env like every other local target. Runs
# serial: these make real provider calls, and running them concurrently buys
# little while making a rate-limit failure look like a flake.
test-e2e-real-llm:
	E2E_LLM_MODE=real uv run pytest -m "e2e and not slow and not workspace and not local_cli and not indexing" \
		app/modules/agent app/modules/usage/tests/e2e/test_real_model_accounting_e2e.py -o timeout=300

# Byte-identical to the marker filter and environment in
# `.github/workflows/backend-protected-e2e.yml`, and asserted so by
# `test_the_protected_lane_can_be_reproduced_from_the_makefile`. It had drifted:
# this target used to select `provider` where the workflow says `not provider`,
# omit `not mock_sandbox_only`, and leave `E2E_LLM_MODE` unset -- so running it
# locally to reproduce the protected lane failed on tests CI never runs, and
# passed on none it does. That lane once did nothing at all for eleven days and
# blocked every Desktop release; it should be reproducible in one command.
test-e2e-runtime:
	E2E_REAL=1 E2E_LLM_MODE=mock LEMMA_RUN_SURFACE_LIVE_E2E=1 uv run pytest \
		-m "e2e and (slow or workspace or surface_live or indexing or local_cli or protected) and not provider and not mock_sandbox_only" \
		-o timeout=300

test-module:
	@test -n "$(MODULE)" || (echo "MODULE is required, e.g. make test-module MODULE=pod" && exit 1)
	uv run pytest app/modules/$(MODULE)

coverage: coverage-unit coverage-e2e

coverage-unit:
	uv run pytest -m "$(UNIT_MARKERS)" --cov=app --cov-report=term-missing --cov-report=xml:coverage-unit.xml

coverage-e2e:
	uv run pytest -m e2e --cov=app --cov-report=term-missing --cov-report=xml:coverage-e2e.xml

coverage-backend-unit:
	@mkdir -p $(BACKEND_COVERAGE_DIR)
	@rm -f $(BACKEND_COVERAGE_DIR)/junit-*.xml $(BACKEND_COVERAGE_DIR)/junit-*.phases.json $(BACKEND_COVERAGE_DIR)/comment.md $(BACKEND_COVERAGE_DIR)/summary.json $(BACKEND_COVERAGE_DIR)/coverage.json $(BACKEND_COVERAGE_DIR)/coverage.xml
	$(BACKEND_COVERAGE_DATA_ENV) uv run coverage erase
	$(BACKEND_COVERAGE_RUN_ENV) uv run coverage run --parallel-mode -m pytest \
		-m "$(UNIT_MARKERS)" \
		--junitxml=$(BACKEND_COVERAGE_DIR)/junit-unit.xml
	$(MAKE) coverage-backend-report COVERAGE_PHASE=unit

coverage-backend-e2e-shard:
	@test -n "$(E2E_ARGS)" || (echo "E2E_ARGS is required" && exit 1)
	@test -n "$(COVERAGE_PHASE)" || (echo "COVERAGE_PHASE is required, e.g. e2e-surfaces" && exit 1)
	@mkdir -p $(BACKEND_COVERAGE_DIR)
	@rm -f $(BACKEND_COVERAGE_DIR)/junit-*.xml $(BACKEND_COVERAGE_DIR)/junit-*.phases.json $(BACKEND_COVERAGE_DIR)/comment.md $(BACKEND_COVERAGE_DIR)/summary.json $(BACKEND_COVERAGE_DIR)/coverage.json $(BACKEND_COVERAGE_DIR)/coverage.xml
	$(BACKEND_COVERAGE_DATA_ENV) uv run coverage erase
	$(BACKEND_COVERAGE_RUN_ENV) uv run coverage run --parallel-mode -m pytest \
		$(E2E_PARALLEL) $(E2E_ARGS) \
		-m "$(E2E_SHARD_MARKERS)" \
		-o timeout=120 \
		--junitxml=$(BACKEND_COVERAGE_DIR)/junit-$(COVERAGE_PHASE).xml
	$(MAKE) coverage-backend-report COVERAGE_PHASE=$(COVERAGE_PHASE)

coverage-backend-report:
	@test -n "$(COVERAGE_PHASE)" || (echo "COVERAGE_PHASE is required" && exit 1)
	@mkdir -p $(BACKEND_COVERAGE_DIR)
	$(BACKEND_COVERAGE_DATA_ENV) uv run coverage combine
	$(BACKEND_COVERAGE_DATA_ENV) uv run coverage report --skip-covered --sort=cover
	$(BACKEND_COVERAGE_DATA_ENV) uv run coverage json -o $(BACKEND_COVERAGE_DIR)/coverage.json
	$(BACKEND_COVERAGE_DATA_ENV) uv run coverage xml -o $(BACKEND_COVERAGE_DIR)/coverage.xml
# Opt-in: 1248 annotated HTML files that nothing reads. It ran here on the
# unit job *and* on all eight e2e shards, ~10s of each shard's critical path
# and ~15s of the unit required check, purely to be uploaded and browsed --
# no gate, script or workflow consumes the directory. The artifacts still
# carry `.coverage.backend`, so anyone who wants the report runs
# `COVERAGE_HTML=1 make coverage-backend-report` against it in ~8 seconds.
	@if [ "$(COVERAGE_HTML)" = "1" ]; then \
		$(BACKEND_COVERAGE_DATA_ENV) uv run coverage html -d $(BACKEND_COVERAGE_DIR)/html; \
	else \
		echo "coverage html skipped (COVERAGE_HTML=1 to generate)"; \
	fi
	uv run python scripts/backend_coverage_report.py \
		--phase $(COVERAGE_PHASE) \
		--coverage-json $(BACKEND_COVERAGE_DIR)/coverage.json \
		--junit-glob "$(BACKEND_COVERAGE_DIR)/junit-*.xml" \
		--markdown-out $(BACKEND_COVERAGE_DIR)/comment.md \
		--summary-json-out $(BACKEND_COVERAGE_DIR)/summary.json

coverage-backend-gate:
	$(BACKEND_COVERAGE_DATA_ENV) uv run coverage combine
	$(BACKEND_COVERAGE_DATA_ENV) uv run coverage json -o $(BACKEND_COVERAGE_DIR)/coverage.json
	$(BACKEND_COVERAGE_DATA_ENV) uv run coverage xml -o $(BACKEND_COVERAGE_DIR)/coverage.xml
	uv run python scripts/check_coverage_thresholds.py \
		$(BACKEND_COVERAGE_DIR)/coverage.json \
		--lane combined --min-total 70
	uv run diff-cover $(BACKEND_COVERAGE_DIR)/coverage.xml \
		--compare-branch origin/main --fail-under=85

# Refresh the recorded floors from a run's reports. Only raises, so it locks in
# a gain and cannot be used to clear a failure.
coverage-baseline:
	uv run python scripts/check_coverage_thresholds.py \
		$(BACKEND_COVERAGE_DIR)/coverage.json --lane combined --write
	uv run python scripts/check_coverage_thresholds.py \
		$(BACKEND_COVERAGE_DIR)/e2e-union.json --lane e2e_union --write

coverage-module:
	@test -n "$(MODULE)" || (echo "MODULE is required, e.g. make coverage-module MODULE=agent" && exit 1)
	uv run pytest app/modules/$(MODULE) \
		--cov=app/modules/$(MODULE) \
		--cov-report=term-missing \
		--cov-fail-under=90

test-collect:
	uv run pytest --collect-only -q

lint:
	uv run ruff check app migrations scripts

# Formatting, over first-party code only. The exclusion of `lemma-connectors/`
# here is deliberately broader than `lint-connectors` below: only
# `src/lemma_connectors/*/generated/**` comes from provider specs, but the whole
# package is left alone by the formatter because reformatting the generated half
# would be undone by the next generation run and read as codegen drift. Linting
# is the finer instrument -- the package's own `[tool.ruff]` scopes past the
# generated clients so the hand-written half stays enforceable.
FORMAT_PATHS = app migrations scripts sandbox_runtime load_tests standalone_app.py sitecustomize.py

format:
	uv run ruff format $(FORMAT_PATHS)

format-check:
	uv run ruff format --check $(FORMAT_PATHS)

# lemma-connectors is vendored here but was outside every gate: not in `lint`'s
# paths, excluded from basedpyright, and its tests are outside pytest.ini's
# testpaths. Its own pyproject scopes ruff past the generated clients, so the
# hand-written half is enforceable -- see the comment there.
lint-connectors:
	uv run ruff check lemma-connectors/src lemma-connectors/tests lemma-connectors/scripts

test-connectors:
	uv run pytest lemma-connectors/tests -q

# `app/modules/agent/api/dependencies.py` is in the list for one line it holds:
# it hands the concrete `ConversationRepository` to a parameter typed as the
# port, which is the only place basedpyright checks the two against each other.
# Without it the port can drift *ahead* of its implementation and nothing fails
# -- the mirror image of the drift the services directory now catches.
#
# `app/modules/connectors/api/dependencies.py` earns its place the same way, and
# the cost of its absence was measured: adding `code_verifier` to the auth port
# left `ComposioAuthProvider` behind, and because nothing type-checked the line
# handing it to a `dict[str, AuthProviderPort]`, every Composio OAuth connect
# raised TypeError into a broad except and returned 502. Structural, not a test:
# no amount of test writing catches a port that drifts from an implementation
# nobody exercises.
#
# `app/modules/agent_surfaces/api/controllers` is the same argument one level
# out: not a port, but a call site whose only reader is the type checker. A
# rename of `publish_home_view`'s `channel_routes` swept up a call to
# `surface_config_from_input` -- a different function that kept the parameter --
# and `POST /surfaces/telegram/managed` raised `TypeError` before it reached
# Telegram. A controller is exactly where that goes unnoticed: it is the layer
# tests reach through a client rather than by calling, so a keyword nobody
# passes in a test is a keyword nobody checks.
# Standard type checking accepts absent annotations. Keep accounting boundaries
# and their test doubles explicit so deleting a type cannot make this gate pass.
AGENT_HARNESS_TYPED_PATHS := \
	app/modules/agent/capabilities/assembler.py \
	app/modules/agent/capabilities/deferred_hint.py \
	app/modules/agent/capabilities/instructed_toolset.py \
	app/modules/agent/capabilities/todo.py \
	app/modules/agent/domain/ports.py \
	app/modules/agent/domain/value_objects.py \
	app/modules/agent/infrastructure/harnesses/agent_host/dispatch.py \
	app/modules/agent/infrastructure/harnesses/agent_host/harness.py \
	app/modules/agent/infrastructure/harnesses/agent_host/run_config.py \
	app/modules/agent/infrastructure/harnesses/history.py \
	app/modules/agent/infrastructure/harnesses/pydantic_ai.py \
	app/modules/agent/infrastructure/harnesses/pydantic_ai_node_loop.py \
	app/modules/agent/infrastructure/harnesses/remote_payload.py \
	app/modules/agent/services/agent_runner_service.py \
	app/modules/agent/services/conversation_title_service.py \
	app/modules/agent/services/runtime_model_factory.py \
	app/modules/agent/services/runtime_profile_service.py \
	app/modules/agent/services/summarization_model.py \
	app/modules/agent/services/vision_service.py \
	app/modules/agent/tools/graceful_toolset.py \
	app/modules/agent/tools/registry.py \
	app/modules/agent/tools/tool_assembler.py \
	app/modules/agent_surfaces/contracts/egress.py \
	app/modules/agent_surfaces/infrastructure/adapters/platform_tool_factory.py

USAGE_ANNOTATED_PATHS := \
	app/modules/agent/infrastructure/harnesses/pydantic_ai.py \
	app/modules/agent/infrastructure/harnesses/history.py \
	app/modules/agent/services/agent_runner_service.py \
	app/modules/agent/services/conversation_title_service.py \
	app/modules/agent/services/run_usage_recorder.py \
	app/modules/agent/services/runtime_model_factory.py \
	app/modules/agent/services/runtime_profile_service.py \
	app/modules/agent/services/summarization_model.py \
	app/modules/agent/services/vision_service.py \
	app/modules/agent/tests/unit/test_runtime_model_settings.py \
	app/modules/usage/api/controllers.py \
	app/modules/usage/config.py \
	app/modules/usage/contracts/metering.py \
	app/modules/usage/domain/accounting.py \
	app/modules/usage/domain/budget_windows.py \
	app/modules/usage/domain/ports.py \
	app/modules/usage/domain/query_types.py \
	app/modules/usage/infrastructure/allocation_models.py \
	app/modules/usage/infrastructure/cost_expressions.py \
	app/modules/usage/infrastructure/usage_limit_reads.py \
	app/modules/usage/infrastructure/allocation_repository.py \
	app/modules/usage/infrastructure/metered_model.py \
	app/modules/usage/infrastructure/models.py \
	app/modules/usage/infrastructure/price_catalog.py \
	app/modules/usage/infrastructure/repositories.py \
	app/modules/usage/services/accounting_gateway.py \
	app/modules/usage/services/batch_meter.py \
	app/modules/usage/services/metering_scope.py \
	app/modules/usage/services/pricing.py \
	app/modules/usage/services/run_receipts.py \
	app/modules/usage/services/usage_service.py \
	app/modules/usage/services/usage_service_factory.py \
	app/modules/usage/tests/e2e \
	app/modules/usage/tests/fakes.py \
	app/modules/usage/tests/unit/test_accounting_money.py \
	app/modules/usage/tests/unit/test_batch_meter.py \
	app/modules/usage/tests/unit/test_price_catalog.py

typecheck-critical:
	uv run ruff check --select ANN $(USAGE_ANNOTATED_PATHS) $(AGENT_HARNESS_TYPED_PATHS)
	uv run basedpyright --project pyright-usage-tests.json
	uv run basedpyright --level error $(AGENT_HARNESS_TYPED_PATHS) \
		app/core/domain/events.py \
		app/core/domain/errors.py \
		app/core/api/schemas.py \
		app/core/api/uploads.py \
		app/core/infrastructure/events \
		app/modules/agent/api/dependencies.py \
		app/modules/agent/domain/ports.py \
		app/modules/agent/services \
		app/modules/agent_surfaces/api/controllers \
		app/modules/agent_surfaces/domain/ports.py \
		app/modules/connectors/domain/ports.py \
		app/modules/connectors/api/dependencies.py \
		app/modules/connectors/services/install_service_seam.py \
		app/modules/connectors/services/install_provisioning.py \
		app/modules/connectors/services/install_update.py \
		app/modules/connectors/services/account_credential_rotation.py \
		app/modules/pod/domain \
		app/modules/pod_bundle/domain \
		app/modules/schedule/domain \
		app/modules/usage \
		app/modules/workspace/services/local_sandbox_client.py \
		app/modules/workspace/services/port_access.py \
		app/modules/workspace/services/sandbox_service.py \
		app/modules/workspace/services/sandbox_sweeper.py

architecture:
	uv run python scripts/check_architecture.py
	uv run python scripts/check_settings_attrs.py
	uv run python scripts/generate_route_inventory.py --check

# Async-safety gate (enforced in CI). Catches blocking calls newly introduced
# into async production code. Scoped to app/ with the noisy/stylistic ASYNC
# rules disabled: ASYNC109 (timeout param), ASYNC110 (sleep-in-while — valid for
# poll loops), ASYNC240 (pathlib/os.path — mostly fast metadata + intentional
# disk writes). Keeps the high-signal ones: ASYNC210 (sync HTTP), ASYNC22x
# (subprocess), ASYNC230 (blocking open), ASYNC251 (time.sleep).
lint-async:
	uv run ruff check --select ASYNC --ignore ASYNC109,ASYNC110,ASYNC240 app/

# Connection-scope gate (enforced in CI). The ASYNC rules above stop the event
# loop being blocked; this one stops a pooled Postgres connection being held
# across work that isn't a query — an LLM call, an HTTP request, a sandbox
# operation, a thread offload. Together they are what make it safe to size
# worker concurrency from RAM rather than from `db_pool_size`.
# Ratcheted against session-scope-baseline.json: the baseline may shrink freely,
# and anything new fails the build.
lint-session-scope:
	uv run python scripts/check_session_scope.py

# I/O hygiene gate (enforced in CI). Blocking work must go through
# run_blocking's named limiters rather than asyncio's unbounded default
# executor, and every aiohttp session must carry a timeout — aiohttp's default
# is five minutes, long enough to pin a pooled connection with it.
# Ratcheted against io-hygiene-baseline.json.
lint-io-hygiene:
	uv run python scripts/check_io_hygiene.py

# Unbounded queues and caches, and CPU-heavy work on the event loop.
# Ratcheted against unbounded-baseline.json.
lint-unbounded:
	uv run python scripts/check_unbounded.py

# A default argument naming something tests replace by patching a module
# attribute. Defaults bind at import, so the patch never reaches them -- and
# the test usually still passes. No baseline: the rule is quiet by
# construction, and somewhere to hide a finding is the one thing it must not
# have.
lint-import-bound-defaults:
	uv run python scripts/check_import_bound_defaults.py

# Where a signature meets a caller: every module's HTTP controllers, plus
# `agent_surfaces/services`, the most entangled service layer in the backend.
# #620 shipped a keyword argument that stopped matching its callee straight to
# CI; `typecheck-critical` names paths by hand and did not name these.
lint-controller-types:
	uv run python scripts/check_controller_types.py

# Swallowed-error gate (enforced in CI). A broad `except` must leave a trace of
# what it caught: production runs at INFO, so a debug record with no exc_info
# is emitted nowhere, and a handler that also stops the exception propagating
# makes the job report success. That combination is why 2,146 conversation
# title jobs reported `succeeded` while the LLM path was failing. Re-raising,
# recording a DependencyIncident, or logging above debug all satisfy it.
# Ratcheted against swallowed-errors-baseline.json.
lint-swallowed-errors:
	uv run python scripts/check_swallowed_errors.py

# In-subject test doubles (enforced in CI). A double in front of a collaborator
# isolates the unit; one *inside* it certifies the half you did not write, so a
# rename behind the patched name lands green in the test named after it.
# Ratcheted against test-doubles-baseline.json — the number only goes down.
lint-test-doubles:
	uv run python scripts/check_test_doubles.py

# e2e wait-pattern gate (enforced in CI). A test waiting for async state to
# settle must go through waiters.eventually()/wait_for_status(), not a
# hand-rolled for/while-plus-sleep loop — that shared helper is what keeps the
# suite's ~50 poll sites at one real timeout, one real interval, and one real
# failure message apiece instead of quietly regrowing a duplicate.
# Ratcheted against e2e-wait-patterns-baseline.json.
lint-e2e-waits:
	uv run python scripts/check_e2e_wait_patterns.py

# Import-budget gate (enforced in CI). Importing the api loads ~4,600 modules
# before it can serve a request, and the worker is larger still. Much of that is
# structural — fastapi, sqlalchemy and supertokens_python are all needed before
# the first authenticated request — so this does not ask anyone to shrink it. It
# asks that growth be a decision rather than a side effect of a new dependency.
# Ratcheted against import-budget-baseline.json.
#
# This used to say openai arrived structurally through pydantic_ai and so could
# not be deferred. That was measured and is wrong: it arrived through composio,
# from one module-scope line of ours, and deferring that line removed 993
# modules. A note in a gate saying "not worth looking at" is worse than no note,
# because it is believed.
lint-import-budget:
	uv run python scripts/check_import_budget.py

# Runtime proof that the pool discipline holds, not just that the AST looks
# right. Small and hermetic by design: this is the one connection/loop suite
# that gates merges, so it has to stay inside a lint-job time budget. The full
# e2e suite still runs (advisory) in its own workflow.
test-connection-scope:
	uv run pytest -m "connection_scope" -o timeout=300 -p no:randomly

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

benchmark-functions-docker:
	$(FUNCTION_BENCH_ENV) \
		FUNCTION_BENCH_PROVIDER=docker \
		uv run pytest $(FUNCTION_BENCH_TEST) -q -s -o timeout=1800

benchmark-functions-e2b:
	$(FUNCTION_BENCH_ENV) \
		FUNCTION_BENCH_PROVIDER=e2b \
		FUNCTION_BENCH_TUNNEL=$(FUNCTION_BENCH_TUNNEL) \
		E2B_WORKSPACE_TEMPLATE=$(E2B_WORKSPACE_TEMPLATE) \
		E2B_FUNCTION_TEMPLATE=$(E2B_FUNCTION_TEMPLATE) \
		uv run pytest $(FUNCTION_BENCH_TEST) -q -s -o timeout=1800

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

load-test-datastore-files:
	uv run python load_tests/datastore_ingestion.py \
		--count "$${DATASTORE_BENCH_COUNT:-20}" \
		--upload-concurrency "$${DATASTORE_BENCH_CONCURRENCY:-5}" \
		--processing-timeout "$${DATASTORE_BENCH_TIMEOUT:-3600}" \
		--report "$${DATASTORE_BENCH_REPORT:-/tmp/lemma-datastore-ingestion-report.json}"

load-test-datastore-up:
	docker compose $(DATASTORE_LOAD_TEST_COMPOSE) up -d
	@until docker compose $(DATASTORE_LOAD_TEST_COMPOSE) exec -T api python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" > /dev/null 2>&1; do \
		printf '.'; sleep 3; \
	done
	@until docker compose $(DATASTORE_LOAD_TEST_COMPOSE) logs worker 2>&1 | grep -q "Local embedding model is ready"; do \
		test -n "$$(docker compose $(DATASTORE_LOAD_TEST_COMPOSE) ps -q worker)" || { \
			echo " Datastore worker stopped during startup."; \
			docker compose $(DATASTORE_LOAD_TEST_COMPOSE) logs --tail=100 worker; \
			exit 1; \
		}; \
		printf '.'; sleep 3; \
	done
	@echo " Datastore benchmark stack is ready."

load-test-datastore-setup:
	docker compose $(DATASTORE_LOAD_TEST_COMPOSE) exec -T api alembic upgrade head
	uv run python load_tests/setup.py --api-url $(LEMMA_API_URL)

load-test-datastore-stats:
	docker stats lemma-load-api lemma-load-worker lemma-load-kreuzberg --no-stream

load-test-datastore-down:
	docker compose $(DATASTORE_LOAD_TEST_COMPOSE) down -v

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

load-test-journey:
	@echo "Running k6 full-journey load test (MAX_USERS=$(MAX_USERS), THINK_MS=$(THINK_MS))…"
	@echo "Each VU self-provisions (signup→org→pod→TODO agent→conversation) then loops the message."
	docker run --rm --network host \
		-e LEMMA_API_URL=$(LEMMA_API_URL) \
		-e MAX_USERS=$(MAX_USERS) \
		-e THINK_MS=$(THINK_MS) \
		-v "$$(pwd)/load_tests:/scripts" \
		grafana/k6:latest run /scripts/journey.js
	@echo ""
	@echo "Container resource snapshot at test end:"
	docker stats lemma-load-api lemma-load-worker --no-stream --format \
		"table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}"

load-test-monitor:
	@echo "Monitoring DB connections (Ctrl+C to stop)…"
	uv run python load_tests/db_monitor.py --interval 5

# One run, everything on the same clock. k6 gives latency, docker_stats gives the
# memory floor, db_monitor gives connections, and the container logs give the
# loop-stall and memory samplers — including the stack frame of whatever blocked
# the loop. Previously these were four separate commands and a `--no-stream`
# snapshot taken after the test, which cannot show a trend.
#   PROFILE=journey|ws|sse|micro  USERS=20  make load-test-profile
load-test-profile:
	uv run python load_tests/profile_run.py \
		--profile "$${PROFILE:-journey}" \
		--users "$${USERS:-10}" \
		--think-ms "$${THINK_MS:-1000}" \
		--api-url "$(LEMMA_API_URL)"

load-test-down:
	docker compose -f $(LOAD_TEST_COMPOSE) down -v
	rm -f $(LOAD_TEST_ENV)

load-test: load-test-build load-test-up load-test-migrate load-test-setup load-test-run
	@echo ""
	@echo "Done. Run 'make load-test-down' to remove containers and credentials."

# Age-gated, so safe to run any time — including while another test/sandbox/
# load-test session is active on the same Docker daemon. Only ever touches
# containers that are already STOPPED and volumes with zero references, past
# a generous grace period per resource kind (see test_utils.py). This is the
# same sweep that already runs automatically around e2e sessions; this target
# is for manual/periodic cleanup (e.g. a crashed run from yesterday, or a
# machine that's accumulated cruft across many worktrees).
docker-reap:
	uv run python -m app.core.test_utils

# ── Sandbox images ────────────────────────────────────────────────────────────
# The two images the workspace module provisions. They live here because the
# module owns them: the runtime inside them is `sandbox_runtime/`, and its
# protocol is the one this backend speaks.
#
# The build context is the monorepo root -- the images embed lemma-python,
# lemma-cli and lemma-skills, which are siblings of this directory.

SANDBOX_REGISTRY  ?= ghcr.io/lemma-work
SANDBOX_TAG       ?= dev
SANDBOX_PLATFORM  ?= linux/amd64
SANDBOX_PLATFORMS ?= linux/amd64,linux/arm64
# Function artifacts target the canonical x86-64 runtime ABI. Do not publish an
# arm64 function image until the artifact builder emits an arm64 ABI.
SANDBOX_FUNCTION_PLATFORMS ?= linux/amd64
SANDBOX_PYTHON_IMAGE ?= python:3.14.6-slim
SANDBOX_NODE_IMAGE   ?= node:24.18.0-slim

WORKSPACE_IMAGE := $(SANDBOX_REGISTRY)/lemma-workspace
FUNCTION_IMAGE  := $(SANDBOX_REGISTRY)/lemma-function
# A client looks these up by their manifest key and gets whatever ref the key
# points at, so the published image name can move; the keys cannot.

IMAGES_DIR := sandbox-images

.PHONY: sandbox-images sandbox-image-workspace sandbox-image-function \
        push-sandbox-images push-sandbox-workspace push-sandbox-function \
        print-sandbox-images

print-sandbox-images:
	@echo "workspace: $(WORKSPACE_IMAGE):$(SANDBOX_TAG)"
	@echo "function:  $(FUNCTION_IMAGE):$(SANDBOX_TAG)"

sandbox-images: sandbox-image-workspace sandbox-image-function

sandbox-image-workspace:
	docker build \
		--platform $(SANDBOX_PLATFORM) \
		-f $(IMAGES_DIR)/Dockerfile.workspace \
		--build-arg PYTHON_IMAGE=$(SANDBOX_PYTHON_IMAGE) \
		--build-arg NODE_IMAGE=$(SANDBOX_NODE_IMAGE) \
		-t $(WORKSPACE_IMAGE):$(SANDBOX_TAG) \
		..

sandbox-image-function:
	docker build \
		--platform $(SANDBOX_PLATFORM) \
		-f $(IMAGES_DIR)/Dockerfile.function \
		--build-arg PYTHON_IMAGE=$(SANDBOX_PYTHON_IMAGE) \
		-t $(FUNCTION_IMAGE):$(SANDBOX_TAG) \
		..

push-sandbox-images: push-sandbox-workspace push-sandbox-function

push-sandbox-workspace:
	docker buildx build \
		--platform $(SANDBOX_PLATFORMS) \
		-f $(IMAGES_DIR)/Dockerfile.workspace \
		--build-arg PYTHON_IMAGE=$(SANDBOX_PYTHON_IMAGE) \
		--build-arg NODE_IMAGE=$(SANDBOX_NODE_IMAGE) \
		-t $(WORKSPACE_IMAGE):$(SANDBOX_TAG) \
		--push \
		..

push-sandbox-function:
	docker buildx build \
		--platform $(SANDBOX_FUNCTION_PLATFORMS) \
		-f $(IMAGES_DIR)/Dockerfile.function \
		--build-arg PYTHON_IMAGE=$(SANDBOX_PYTHON_IMAGE) \
		-t $(FUNCTION_IMAGE):$(SANDBOX_TAG) \
		--push \
		..
