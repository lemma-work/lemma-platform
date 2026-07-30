from __future__ import annotations

import asyncio
from pathlib import Path
import sqlite3
from uuid import uuid4

from alembic import command
from alembic.config import Config

from agentbox.domain import SandboxKey, SandboxProfileRef, WorkloadKind
from agentbox.persistence.uow import StateDatabase


def _config(database_url: str) -> Config:
    project_root = Path(__file__).resolve().parents[2]
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _columns(database_path: Path, table_name: str) -> set[str]:
    with sqlite3.connect(database_path) as connection:
        return {
            str(row[1])
            for row in connection.execute(f"PRAGMA table_info({table_name})")
        }


def test_alembic_fresh_install_matches_models(tmp_path: Path):
    database_path = tmp_path / "migrated.db"
    database_url = f"sqlite+aiosqlite:///{database_path}"
    config = _config(database_url)

    command.upgrade(config, "head")
    command.check(config)
    with sqlite3.connect(database_path) as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    # The manager no longer maps or writes these tables. They remain for one
    # expand/contract release so the N-1 image keeps working while migrations
    # run before the Container Apps image switch.
    assert {"processes", "sessions", "python_executions"} <= tables
    assert {
        "sandboxes",
        "allocations",
        "allocation_create_attempts",
        "workspace_storage",
        "provider_admission",
    } <= tables

    async def repository_smoke() -> None:
        database = StateDatabase(database_url)
        try:
            key = SandboxKey(WorkloadKind.WORKSPACE, uuid4())
            profile = SandboxProfileRef(
                name="workspace-python-v1", digest=f"sha256:{'a' * 64}"
            )
            async with database.uow() as uow:
                created = await uow.repository.ensure_logical(key, profile)
                await uow.commit()
            assert created.key == key
        finally:
            await database.dispose()

    asyncio.run(repository_smoke())


def test_alembic_adopts_legacy_create_all_schema(tmp_path: Path):
    database_path = tmp_path / "legacy.db"
    database_url = f"sqlite+aiosqlite:///{database_path}"
    config = _config(database_url)

    command.upgrade(config, "24278caff64b")
    with sqlite3.connect(database_path) as connection:
        connection.execute("DELETE FROM alembic_version")
        connection.commit()

    command.upgrade(config, "head")

    assert "resource_generation" in _columns(database_path, "sandboxes")
    assert "resource_generation" in _columns(database_path, "allocations")
    with sqlite3.connect(database_path) as connection:
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
    assert revision == ("6bb3a72395fb",)


def test_alembic_adopts_current_create_all_schema(tmp_path: Path):
    database_path = tmp_path / "current.db"
    database_url = f"sqlite+aiosqlite:///{database_path}"
    config = _config(database_url)

    async def create_current_schema() -> None:
        database = StateDatabase(database_url)
        try:
            await database.create_schema_for_test()
        finally:
            await database.dispose()

    asyncio.run(create_current_schema())
    command.upgrade(config, "head")

    assert "resource_generation" in _columns(database_path, "sandboxes")
    assert "resource_generation" in _columns(database_path, "allocations")
    with sqlite3.connect(database_path) as connection:
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
    assert revision == ("6bb3a72395fb",)
