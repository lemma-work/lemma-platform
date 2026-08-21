"""Database preparation and schema migrations.

Migrations run as an explicit one-shot container from the backend image, so a
failed migration aborts the start/upgrade instead of crash-looping the app.
"""

from __future__ import annotations

from lemma_stack.output import AdminError, info
from lemma_stack.release.manifest import ReleaseManifest
from lemma_stack.runtime.base import Runtime
from lemma_stack.stack.specs import CONTAINER_PREFIX, NETWORK_NAME

DB_CONTAINER = f"{CONTAINER_PREFIX}-db"
EXTRA_DATABASES = ("supertokens", "lemma_datastore")
VECTOR_DATABASES = ("lemma", "lemma_datastore")
DATABASE_URL = "postgresql+asyncpg://postgres:postgres@db:5432/lemma"


def _psql(runtime: Runtime, database: str, sql: str) -> str:
    proc = runtime.run(
        "exec",
        DB_CONTAINER,
        "psql",
        "-U",
        "postgres",
        "-d",
        database,
        "-v",
        "ON_ERROR_STOP=1",
        "-tAc",
        sql,
    )
    return proc.stdout.strip()


def ensure_databases(runtime: Runtime) -> None:
    for database in EXTRA_DATABASES:
        exists = _psql(
            runtime,
            "postgres",
            f"SELECT 1 FROM pg_database WHERE datname = '{database}'",
        )
        if exists != "1":
            info(f"db: creating database {database}")
            _psql(runtime, "postgres", f"CREATE DATABASE {database}")
    for database in VECTOR_DATABASES:
        _psql(runtime, database, "CREATE EXTENSION IF NOT EXISTS vector")


def run_migrations(runtime: Runtime, manifest: ReleaseManifest) -> None:
    migration_steps = (
        (
            "Lemma",
            ("-e", f"DATABASE_URL={DATABASE_URL}"),
            ("alembic", "upgrade", "head"),
        ),
    )
    for label, environment, command in migration_steps:
        info(f"db: running {label} migrations")
        proc = runtime.run(
            "run",
            "--rm",
            "--network",
            NETWORK_NAME,
            *environment,
            manifest.image("backend").pull_ref,
            *command,
            check=False,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()[-2000:]
            raise AdminError(f"{label} database migration failed; stack left untouched.\n{detail}")
