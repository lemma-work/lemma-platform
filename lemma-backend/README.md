# Lemma Backend

FastAPI backend platform for building AI-powered connectors around isolated pods. Each pod packages structured data, files, deterministic functions, agents, workflows, assistants, and user-facing apps for one use case.

The backend lives in `lemma-backend/` inside the `lemma-platform` monorepo. It is a normal Python project with its own `pyproject.toml`, `uv.lock`, migrations, scripts, and Docker Compose files.

> Detailed architecture and engineering-style docs are being added under `docs/`
> (and `docs/tests/` for test-specific notes). For now this README is the
> development guide.

## Stack

| Layer | Technology |
|-------|-----------|
| Framework | FastAPI |
| ORM | SQLAlchemy 2.0 async (asyncpg) |
| Database | PostgreSQL + pgvector |
| Auth | SuperTokens cookie-based sessions |
| Message bus | FastStream (Redis streams) |
| Task queue | streaq (Redis) |
| Cache | Redis (`app/core/infrastructure/cache`) |
| Validation | Pydantic v2 |
| Dependency mgmt | uv |
| Python | 3.14 |

## Monorepo dependencies

The repository does not use submodules. Backend code depends on sibling packages in the monorepo:

| Path | Purpose |
|------|---------|
| `../lemma-frontend/` | Next.js frontend used by the local app runner |
| `../lemma-python/` | Python SDK and `lemma` CLI |
| `../lemma-typescript/` | TypeScript SDK used by apps |
| `../lemma-skills/` | Built-in agent skills loaded by the backend and workspace containers |
| `lemma-connectors/` | Backend-local editable Python connector package |
| `../agentbox/` | Workspace sandbox manager + runtime image |

## Development

### Setup

```bash
make init                       # (repo root) generate .env files with local dev defaults + keys
cd lemma-backend && uv sync     # install deps into .venv (uses uv.lock)
```

`make init` writes the backend `.env` (DB/Redis/SuperTokens URLs, encryption keys, AgentBox URL). Re-run it any time to backfill missing keys.

### Run the full stack (recommended)

From the **repo root** — infra in Docker + backend + frontend + AgentBox as hot-reload host processes:

```bash
make dev                # start everything
make dev RELOAD=1       # same, with uvicorn --reload on the backend
make stop               # stop backend/frontend host processes
make stop-all           # also bring down the infra containers
make logs               # tail backend logs
```

- Frontend: `http://localhost:3710`
- API: `http://localhost:8710`
- API docs (Scalar): `http://localhost:8710/scalar`

`make dev` runs Postgres/Redis/SuperTokens/Kreuzberg in Docker and the backend as **one** host process (`uvicorn standalone_app:app`) that combines the FastAPI app, the streaq event worker, and the scheduler — convenient for local dev. It also installs the local `lemma` CLI and registers it as the `local-dev` server:

```bash
lemma servers select local-dev
lemma auth login
```

### Run API / worker / scheduler separately (prod topology)

Production runs the API and the worker as **separate** processes (and the scheduler as a third). To mirror that locally, start infra, then run each process yourself from `lemma-backend/`:

```bash
docker compose up -d                  # infra: postgres, redis, supertokens, kreuzberg
uv run alembic upgrade head           # apply migrations

# API only
uv run uvicorn app.app:app --host 0.0.0.0 --port 8000 --reload

# streaq worker — agent runs, file (re)indexing, surface ingest, datastore cleanup tasks
uv run streaq run app.events:streaq_worker

# scheduler — cron / time / webhook schedules
uv run python -m app.scheduler
```

A Dockerized, resource-capped version of this split (api + worker as separate 1-CPU/2-GB containers) lives in the load-test compose — see [Load testing](#load-testing).

## Testing

Two levels — **unit** and **e2e** — and e2e runs in two modes: a fast **mocked** mode (the default, what CI uses) and a **real** mode (manual/nightly).

### Unit

No containers, no network.

```bash
make test-unit                  # everything not marked `e2e`
make test-module MODULE=pod     # a single module (app/modules/pod)
make test                       # unit + e2e
```

### e2e — mocked (default gate)

Container-backed but with **no external services**: the agent LLM is an in-process pydantic-ai `FunctionModel` (scriptable per conversation), and workspace tools + functions hit an in-process **fake AgentBox** — so **no model API key and no Docker workspace image** are needed. Postgres/Redis/SuperTokens/Kreuzberg are provided per worker by testcontainers.

```bash
make test-e2e         # all mocked e2e (parallel via pytest-xdist)
make test-e2e-fast    # fast API subset (excludes slow/worker/workspace/provider/local_cli)
```

Parallelism is controlled by `E2E_WORKERS` (default **2**). Each xdist worker spins up its **own** isolated container trio + loads an embedding model, so workers are RAM-hungry:

```bash
make test-e2e-fast E2E_WORKERS=auto   # one worker per core (roomy machines)
make test-e2e-fast E2E_WORKERS=1      # serial — most reliable (no inter-worker contention)
```

> The fast suite shares a single Kreuzberg container across workers, so under
> `-n2`+ it can be **contention-flaky** (a different test fails each run, all pass
> standalone/serially). Use `E2E_WORKERS=1` for a deterministic green.

### e2e — real (manual / nightly)

Uses the **real model** (`LEMMA_OPENAI_API_KEY`) + the **real Docker AgentBox**, and runs the `real_llm` / `real_sandbox` tests. Serial.

```bash
make test-e2e-real      # all e2e against the real model + Docker AgentBox
make test-e2e-runtime   # only the slow/worker/workspace/provider/local_cli subset
```

### Markers & modes

Markers: `e2e`, `slow`, `worker` (needs the real streaq worker), `workspace` (needs the Docker workspace image), `provider` (needs the real model), `local_cli`. Mode is selected by env: `E2E_REAL=1`, `E2E_LLM_MODE=real|mock`, `E2E_SANDBOX_MODE=docker|fake` (the mocked gate skips `real_*` tests automatically).

### Pre-merge e2e gate (CI)

e2e is **not** run on every commit (it's expensive). It runs as a separate, opt-in gate (`.github/workflows/e2e.yml`): add the **`run-e2e`** label to a PR, or trigger *Actions → "Backend E2E (mocked)" → Run workflow*. Per-commit CI (`ci.yml`) runs unit + build/lint/SDK checks only.

## Coverage

```bash
make coverage                       # unit + e2e -> coverage-unit.xml + coverage-e2e.xml
make coverage-unit                  # unit only (term-missing + xml)
make coverage-e2e                   # e2e only
make coverage-module MODULE=agent   # per-module, fails under 90%
```

## Lint

```bash
make lint     # ruff check .
```

## Load testing

A prod-shaped stack (1-CPU/2-GB API + worker as separate containers) plus k6 journeys that exercise the full surface — chat, file CRUD, function execute, app load — at 100 concurrent users using the mock LLM + fake AgentBox.

```bash
make load-test-build && make load-test-up && make load-test-migrate
make load-test-journey MAX_USERS=100 THINK_MS=500   # full chat+file+function+app journey
make load-test-monitor                              # poll pg_stat_activity during a run
make load-test-down
```

Latest results: [load_tests/REPORT_full_surface_100u.md](load_tests/REPORT_full_surface_100u.md).
Mocked-e2e design notes: [docs/tests/e2e_fast_mode_plan.md](docs/tests/e2e_fast_mode_plan.md).

## Migrations

```bash
make migrate                                            # alembic upgrade head
uv run alembic revision --autogenerate -m "describe_what_changed"
```

New ORM models must be imported in `migrations/env.py` before autogenerate can detect them.

## Connector app catalog

The connector catalog (apps, operations, and triggers) is managed via
[`scripts/import_connector_catalog.py`](scripts/import_connector_catalog.py).

- **Native (Lemma) apps** are always imported — those in `scripts/lemma_apps_config.json`
  (Slack, Jira, Confluence) and the `lemma-connectors` package (Gmail, Google
  Calendar, Google Drive, …).
- **Composio apps** are imported only when `COMPOSIO_API_KEY` is set (skipped
  gracefully otherwise).

```bash
uv run python scripts/import_connector_catalog.py                  # native + Composio (if key set)
uv run python scripts/import_connector_catalog.py --provider native
uv run python scripts/import_connector_catalog.py --app gmail --app slack
uv run python scripts/import_connector_catalog.py --dry-run        # fetch + log, no commit
uv run python scripts/import_connector_catalog.py --generate-skills  # needs FIREWORKS_API_KEY
```

The curated Composio allowlist is in the script (`DEFAULT_COMPOSIO_CONNECTOR_IDS`); add more with `COMPOSIO_EXTRA_APP_IDS=linear,notion`.

## Secret encryption & key rotation

Secrets at rest (connector credentials, OAuth provider configs, agent runtime
credentials, surface webhook secrets) and short-lived signed tokens (widget
embeds, datastore file URLs) all go through [`app/core/crypto`](app/core/crypto/),
which supports versioned envelopes and **key rotation without data loss**.

**Env (env-only, no KMS in prod for now):**

| Var | Meaning |
|-----|---------|
| `SECRET_ENCRYPTION_KEY` | Primary Fernet key. **Falls back to `CONNECTOR_ENCRYPTION_KEY`** when unset, then to a local dev seed in local/testing. Generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `SECRET_ENCRYPTION_KEYSET` | Optional JSON `[{"kid","key","primary"}]` for rotation (primary encrypts new writes; retired keys still decrypt) |
| `SECRET_KEY_PROVIDER` | `auto` (default) → `static` env keys. (`gcp_kms` / `gcp_secret_manager` / `keychain` also available.) |

**Rotation** is keyset-driven via `SECRET_ENCRYPTION_KEYSET` (a JSON list with one
`primary` that encrypts new writes; retired keys still decrypt) — add a new
primary, let writes re-envelope over time, then drop the old key once nothing
references it. The old `CONNECTOR_ENCRYPTION_KEY` is also still read, so releasing
onto an existing DB needs no key change: old `fernet-json-v1` values decrypt and
new writes use the v2 envelope. Apply migrations first (`make migrate`) — they
widen `agent_surfaces.webhook_secret` to Text for the v2 envelope.

## Docker images

```bash
make docker-build         # backend image (from the monorepo root context)
make agentbox-build       # local AgentBox manager + runtime images
```

Release images are published to GitHub Container Registry (`ghcr.io/lemma-work/*`)
by `.github/workflows/release-local-images.yml`.
