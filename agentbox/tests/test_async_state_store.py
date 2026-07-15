from __future__ import annotations

import sqlite3
import time
from inspect import getsource

import pytest

from agentbox.schemas import SandboxEnsureRequest
from agentbox.state_store import AsyncStateStore, create_state_store
from agentbox.state_store.migrations import POSTGRES_MIGRATIONS
from agentbox.state_store.postgres import PostgresStateStore
from agentbox.state_store.sqlite import SQLiteStateStore


@pytest.mark.asyncio
async def test_factory_defaults_to_legacy_sqlite_path_and_sanitizes_env(tmp_path):
    store = await create_state_store(
        database_url=None,
        sqlite_path=str(tmp_path / "state.db"),
    )
    try:
        assert isinstance(store, AsyncStateStore)
        record = await store.upsert_sandbox(
            "sandbox",
            SandboxEnsureRequest(
                env={"LEMMA_BASE_URL": "https://api.example", "USER_TOKEN": "secret"}
            ),
        )
        assert record.env == {"LEMMA_BASE_URL": "https://api.example"}
        assert record.desired_generation == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_versioned_sqlite_migration_preserves_rows_and_unknown_tables(tmp_path):
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE sandboxes (
                sandbox_id TEXT PRIMARY KEY,
                env_json TEXT NOT NULL,
                disk_size_gb INTEGER NOT NULL,
                idle_since_at REAL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            INSERT INTO sandboxes VALUES (
                'old', '{"LEMMA_BASE_URL":"https://api.example","TOKEN":"drop-me"}',
                10, NULL, 1, 1
            );
            CREATE TABLE sessions (
                sandbox_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                cwd TEXT NOT NULL,
                env_keys_json TEXT NOT NULL,
                last_active_at REAL NOT NULL,
                active_operations INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (sandbox_id, session_id)
            );
            INSERT INTO sessions VALUES (
                'old', 'session', '/workspace', '["TOKEN"]', 1, 9, 1, 1
            );
            CREATE TABLE agentbox_workspace_volumes (
                sandbox_id TEXT PRIMARY KEY, volume_id TEXT NOT NULL
            );
            INSERT INTO agentbox_workspace_volumes VALUES ('old', 'volume');
            """
        )
        conn.commit()
    finally:
        conn.close()

    store = await SQLiteStateStore.open(str(path))
    try:
        sandbox = await store.get_sandbox("old")
        session = await store.get_session("old", "session")
        assert sandbox is not None
        assert sandbox.env == {"LEMMA_BASE_URL": "https://api.example"}
        assert sandbox.desired_state == "present"
        assert session is not None
        assert session.env_keys == ["TOKEN"]
        assert session.active_operations == 0

        with store._store._lock:
            versions = store._store._conn.execute(
                "SELECT version FROM agentbox_schema_migrations ORDER BY version"
            ).fetchall()
            volume = store._store._conn.execute(
                "SELECT volume_id FROM agentbox_workspace_volumes WHERE sandbox_id = 'old'"
            ).fetchone()
        assert [row[0] for row in versions] == [1, 2, 3]
        assert volume[0] == "volume"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_observation_is_generation_compare_and_swap(tmp_path):
    store = await SQLiteStateStore.open(str(tmp_path / "state.db"))
    try:
        first = await store.upsert_sandbox("sandbox", SandboxEnsureRequest())
        assert first.desired_generation == 1
        observed = await store.set_sandbox_observation(
            "sandbox",
            provider_name="e2b",
            provider_id="provider-1",
            instance_id="runtime-1",
            observed_generation=1,
        )
        assert observed is not None
        assert observed.observed_generation == 1

        second = await store.upsert_sandbox("sandbox", SandboxEnsureRequest())
        assert second.desired_generation == 2
        deleting = await store.set_sandbox_desired_state("sandbox", "deleted")
        assert deleting is not None
        assert deleting.desired_state == "deleted"
        assert deleting.desired_generation == 3
        stale = await store.set_sandbox_observation(
            "sandbox",
            provider_name="e2b",
            provider_id="stale",
            instance_id="stale",
            observed_generation=1,
        )
        assert stale is None
        current = await store.get_sandbox("sandbox")
        assert current is not None
        assert current.provider_id == "provider-1"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_expiring_activity_lease_protects_idle_session(tmp_path):
    store = await SQLiteStateStore.open(str(tmp_path / "state.db"))
    try:
        await store.upsert_sandbox("sandbox", SandboxEnsureRequest())
        await store.upsert_session("sandbox", "session", cwd="/workspace", env_keys=[])
        with store._store._lock, store._store._conn:
            store._store._conn.execute(
                "UPDATE sessions SET last_active_at = ?", (time.time() - 1000,)
            )
            store._store._conn.execute(
                "UPDATE sandboxes SET last_active_at = ?", (time.time() - 1000,)
            )

        before = (await store.get_sandbox("sandbox")).last_active_at
        assert await store.touch_session("sandbox", "session")
        after = (await store.get_sandbox("sandbox")).last_active_at
        assert before is not None and after is not None and after > before
        with store._store._lock, store._store._conn:
            store._store._conn.execute(
                "UPDATE sessions SET last_active_at = ?", (time.time() - 1000,)
            )

        lease = await store.acquire_activity_lease(
            "sandbox",
            session_id="session",
            operation="python",
            owner="manager-a",
            ttl_seconds=60,
        )
        assert lease is not None
        assert await store.expired_sessions(100) == []

        with store._store._lock, store._store._conn:
            store._store._conn.execute(
                "UPDATE agentbox_activity_leases SET expires_at = ?",
                (time.time() - 1,),
            )
        assert [s.session_id for s in await store.expired_sessions(100)] == ["session"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_lifecycle_claim_is_exclusive_and_orphan_grace_respects_it(tmp_path):
    store = await SQLiteStateStore.open(str(tmp_path / "state.db"))
    try:
        await store.upsert_sandbox("sandbox", SandboxEnsureRequest())
        claim = await store.acquire_lifecycle_claim(
            "sandbox", operation="ensure", owner="manager-a", ttl_seconds=60
        )
        assert claim is not None
        assert (
            await store.acquire_lifecycle_claim(
                "sandbox", operation="delete", owner="manager-b", ttl_seconds=60
            )
            is None
        )

        now = time.time()
        await store.observe_orphan(
            "e2b", "provider-orphan", sandbox_id="sandbox", observed_at=now - 100
        )
        assert await store.expired_orphans(10, inventory_started_at=now) == []

        assert await store.release_lifecycle_claim(claim.claim_id, owner="manager-a")
        candidates = await store.expired_orphans(10, inventory_started_at=now)
        assert [candidate.provider_id for candidate in candidates] == [
            "provider-orphan"
        ]
    finally:
        await store.close()


def test_postgres_migrations_are_additive_and_preserve_private_volume_table():
    sql = "\n".join(
        statement
        for _, _, statements in POSTGRES_MIGRATIONS
        for statement in statements
    ).lower()
    assert "drop table" not in sql
    assert "agentbox_workspace_volumes" not in sql
    assert "add column if not exists" in sql


def test_postgres_session_delete_removes_matching_activity_leases():
    source = getsource(PostgresStateStore.delete_session)
    assert source.index("DELETE FROM agentbox_activity_leases") < source.index(
        "DELETE FROM agentbox_sessions"
    )


@pytest.mark.asyncio
async def test_postgres_open_does_not_expose_credentials(monkeypatch):
    class BrokenPool:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def open(self, **kwargs):
            del kwargs
            raise OSError("connection failed for password=super-secret")

        async def close(self):
            return None

    import psycopg_pool

    monkeypatch.setattr(psycopg_pool, "AsyncConnectionPool", BrokenPool)
    with pytest.raises(RuntimeError) as caught:
        await PostgresStateStore.open(
            "postgresql://agentbox:super-secret@database/agentbox"
        )
    assert "super-secret" not in str(caught.value)
