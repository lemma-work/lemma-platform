# Lemma Backend

FastAPI backend platform for building AI-powered connectors around isolated pods. Each pod packages structured data, files, deterministic functions, agents, workflows, assistants, and user-facing apps for one use case.

The backend lives in `lemma-backend/` inside the `lemma-platform` monorepo. It is a normal Python project with its own `pyproject.toml`, `uv.lock`, migrations, scripts, Docker Compose files, and backend docs.

## Documentation

| Doc | Contents |
|-----|----------|
| [docs/product_doc.md](docs/product_doc.md) | Product overview: pods, resources, workflows, agents |
| [docs/architecture.md](docs/architecture.md) | DDD module structure, UoW, domain events, repositories, API layer |
| [docs/development.md](docs/development.md) | Local setup, running, testing, migrations, environment variables |
| [docs/development_rules.md](docs/development_rules.md) | Engineering standards, refactor checklist, anti-patterns |
| [docs/authorization.md](docs/authorization.md) | Role model, pod permissions, delegation tokens, error contract |
| [docs/flow_document.md](docs/flow_document.md) | Workflow engine: node types, data flow, input mapping, API |
| [docs/workspace-server.md](docs/workspace-server.md) | Workspace containers and monorepo SDK/skills packages |
| [CLAUDE.md](CLAUDE.md) | Quick reference for AI-assisted development |

## Monorepo Dependencies

The repository does not use submodules. Backend code depends on sibling packages in the monorepo:

| Path | Purpose |
|------|---------|
| `../lemma-frontend/` | Next.js frontend used by the local app runner |
| `../lemma-python/` | Python SDK and `lemma` CLI |
| `../lemma-typescript/` | TypeScript SDK used by apps |
| `../lemma-skills/` | Built-in agent skills loaded by the backend and workspace containers |
| `lemma-connectors/` | Backend-local editable Python connector package |

## Quick Start

From the repository root, run the dev stack with hot reload:

```bash
make dev
lemma servers select local-dev
lemma auth login
```

- Frontend: `http://localhost:3710`
- API: `http://localhost:8710`
- Scalar docs: `http://localhost:8710/scalar`

`make dev` runs app infrastructure (Postgres, Redis, SuperTokens, Kreuzberg) in Docker/Podman and runs the backend, frontend, and agentbox as host processes with hot reload. The backend is one process combining the FastAPI app, event worker, and scheduler. It also installs the local `lemma-python` CLI and registers it as the `local-dev` server.

To install a self-contained local stack instead, use the separate `lemma-admin` tool (`./install.sh`), which runs on 3711/8711.

Backend-only commands:

```bash
make test
make lint
make migrate
make docker-build
```

## Stack

| Layer | Technology |
|-------|-----------|
| Framework | FastAPI |
| ORM | SQLAlchemy 2.0 async |
| Database | PostgreSQL + pgvector |
| Auth | SuperTokens cookie-based sessions |
| Message Bus | FastStream Redis |
| Job Queue | ARQ Redis |
| Validation | Pydantic v2 |
| Dependency mgmt | uv |
| Python | 3.14 |

## Connector App Catalog

The connector catalog (apps, operations, and triggers) is managed via
[`scripts/import_connector_catalog.py`](scripts/import_connector_catalog.py).

### How it works

- **Native (Lemma) apps** are always imported. These include apps defined in
  `scripts/lemma_apps_config.json` (Slack, Jira, Confluence) and the
  `lemma-connectors` package (Gmail, Google Calendar, Google Drive, etc.).
- **Composio apps** are imported only when `COMPOSIO_API_KEY` is set. Without
  it, the Composio portion is skipped gracefully.

### Common operations

```bash
# Import everything (native + Composio if key is set, native-only otherwise)
uv run python scripts/import_connector_catalog.py

# Native apps only
uv run python scripts/import_connector_catalog.py --provider native

# Composio apps only (requires COMPOSIO_API_KEY)
uv run python scripts/import_connector_catalog.py --provider composio

# Import specific apps only
uv run python scripts/import_connector_catalog.py --app gmail --app slack

# Dry run — fetch and log without committing to the database
uv run python scripts/import_connector_catalog.py --dry-run

# Generate skill docs after syncing (requires FIREWORKS_API_KEY)
uv run python scripts/import_connector_catalog.py --generate-skills
```

### Adding Composio apps

Set `COMPOSIO_API_KEY` in your `.env` and run the import. The curated allowlist
of Composio apps is defined in the script (`DEFAULT_COMPOSIO_CONNECTOR_IDS`).
To add extra apps, set the `COMPOSIO_EXTRA_APP_IDS` env var:

```bash
COMPOSIO_EXTRA_APP_IDS=linear,notion uv run python scripts/import_connector_catalog.py
```

## Migrations

```bash
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "describe_what_changed"
```

New ORM models must be imported in `migrations/env.py` before autogenerate can detect them.

## Secret encryption & key rotation

Secrets at rest (connector credentials, OAuth provider configs, agent runtime
credentials, surface webhook secrets) and short-lived signed tokens (widget
embeds, datastore file URLs) go through one facility: [`app/core/crypto`](app/core/crypto/).
It supports versioned envelopes and **key rotation without data loss**. Full
guide: [docs/secret-encryption-and-rotation.md](docs/secret-encryption-and-rotation.md).

**Env (env-only, no KMS in prod for now):**

| Var | Meaning |
|-----|---------|
| `SECRET_ENCRYPTION_KEY` | Primary Fernet key. **Falls back to `CONNECTOR_ENCRYPTION_KEY`** when unset, then to a local dev seed in local/testing. Generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `SECRET_ENCRYPTION_KEYSET` | Optional JSON `[{"kid","key","primary"}]` for rotation (primary encrypts new writes; retired keys still decrypt) |
| `SECRET_KEY_PROVIDER` | `auto` (default) → `static` env keys. (`gcp_kms` / `gcp_secret_manager` / `keychain` also available.) |

**Releasing onto an existing DB (old data keeps working):**

The old `CONNECTOR_ENCRYPTION_KEY` is still read, so the safest path needs no key
changes — old `fernet-json-v1` data decrypts, new writes use the v2 envelope under
the same key:

```bash
# 1. apply migrations (widens agent_surfaces.webhook_secret to Text)
uv run alembic upgrade head
# 2. deploy the new code keeping CONNECTOR_ENCRYPTION_KEY set
# 3. normalise existing rows to v2 (idempotent; --dry-run to preview first)
PYTHONPATH=. CONNECTOR_ENCRYPTION_KEY=<key> \
  uv run python scripts/upgrade_secret_encryption.py --dry-run
PYTHONPATH=. CONNECTOR_ENCRYPTION_KEY=<key> \
  uv run python scripts/upgrade_secret_encryption.py
```

To rotate to a **new** key value at the same time, set both and the script reads
old / writes new:

```bash
PYTHONPATH=. CONNECTOR_ENCRYPTION_KEY=<old> SECRET_ENCRYPTION_KEY=<new> \
  uv run python scripts/upgrade_secret_encryption.py
```

Ongoing rotation later uses [`scripts/reencrypt_secrets.py`](scripts/reencrypt_secrets.py).
Inspect a single value with [`scripts/decrypt_fernet_json.py`](scripts/decrypt_fernet_json.py).
