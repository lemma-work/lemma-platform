from __future__ import annotations

import asyncio
import os
import sqlite3
import time
from inspect import getsource
import uuid

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
async def test_factory_parses_durable_env_csv_as_keys_not_characters(tmp_path):
    store = await create_state_store(
        database_url=None,
        sqlite_path=str(tmp_path / "state.db"),
        durable_env_keys=" LEMMA_BASE_URL, PUBLIC_VALUE,LEMMA_BASE_URL ",
    )
    try:
        record = await store.upsert_sandbox(
            "sandbox",
            SandboxEnsureRequest(
                env={
                    "LEMMA_BASE_URL": "https://api.example",
                    "PUBLIC_VALUE": "kept",
                    "USER_TOKEN": "secret",
                }
            ),
        )
        assert record.env == {
            "LEMMA_BASE_URL": "https://api.example",
            "PUBLIC_VALUE": "kept",
        }
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_orphan_tombstone_insert_is_non_destructive(tmp_path):
    store = await SQLiteStateStore.open(str(tmp_path / "state.db"))
    try:
        tombstone = await store.insert_sandbox_tombstone_if_missing("orphan")
        assert tombstone.desired_state == "deleted"

        existing = await store.upsert_sandbox(
            "present",
            SandboxEnsureRequest(env={"LEMMA_BASE_URL": "https://api.example"}),
        )
        unchanged = await store.insert_sandbox_tombstone_if_missing("present")
        assert unchanged.desired_state == "present"
        assert unchanged.env == existing.env
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
        assert session.active_operations == 9

        with store._store._lock:
            versions = store._store._conn.execute(
                "SELECT version FROM agentbox_schema_migrations ORDER BY version"
            ).fetchall()
            volume = store._store._conn.execute(
                "SELECT volume_id FROM agentbox_workspace_volumes WHERE sandbox_id = 'old'"
            ).fetchone()
        assert [row[0] for row in versions] == [1, 2, 3, 4]
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
        assert second.desired_generation == 1
        changed = await store.upsert_sandbox(
            "sandbox",
            SandboxEnsureRequest(env={"LEMMA_BASE_URL": "https://api.changed"}),
        )
        assert changed.desired_generation == 2
        deleting = await store.set_sandbox_desired_state("sandbox", "deleted")
        assert deleting is not None
        assert deleting.desired_state == "deleted"
        assert deleting.desired_generation == 3
        still_deleting = await store.set_sandbox_desired_state("sandbox", "deleted")
        assert still_deleting is not None
        assert still_deleting.desired_generation == 3
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
        leased_at = (await store.get_sandbox("sandbox")).last_active_at
        assert after is not None and leased_at is not None and leased_at >= after
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
async def test_deleting_expired_session_preserves_sandbox_inactivity_clock(tmp_path):
    store = await SQLiteStateStore.open(str(tmp_path / "state.db"))
    try:
        await store.upsert_sandbox("sandbox", SandboxEnsureRequest())
        await store.upsert_session("sandbox", "session", cwd="/workspace", env_keys=[])
        stale_at = time.time() - 120
        with store._store._lock, store._store._conn:
            store._store._conn.execute(
                "UPDATE sessions SET last_active_at = ? WHERE sandbox_id = ?",
                (stale_at, "sandbox"),
            )
            store._store._conn.execute(
                """
                UPDATE sandboxes SET last_active_at = ?, idle_since_at = NULL
                WHERE sandbox_id = ?
                """,
                (stale_at, "sandbox"),
            )

        assert [row.session_id for row in await store.expired_sessions(60)] == [
            "session"
        ]
        assert await store.delete_session("sandbox", "session") is True

        record = await store.get_sandbox("sandbox")
        assert record is not None
        assert record.idle_since_at == pytest.approx(stale_at)
        assert [row.sandbox_id for row in await store.idle_sandboxes(60)] == ["sandbox"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_missing_heartbeat_returns_false_and_existing_returns_true(tmp_path):
    store = await SQLiteStateStore.open(str(tmp_path / "state.db"))
    try:
        assert await store.mark_sandbox_active("missing") is False
        await store.upsert_sandbox("sandbox", SandboxEnsureRequest())
        assert await store.mark_sandbox_active("sandbox") is True
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_ensure_defaults_is_atomic_without_generation_increment(tmp_path):
    store = await SQLiteStateStore.open(str(tmp_path / "state.db"))
    try:
        records = await asyncio.gather(
            *(store.ensure_sandbox_defaults("sandbox") for _ in range(20))
        )
        assert {record.desired_generation for record in records} == {1}
        current = await store.get_sandbox("sandbox")
        assert current is not None
        assert current.desired_generation == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_suspended_sandbox_is_not_released_twice_and_ensure_resumes_it(tmp_path):
    store = await SQLiteStateStore.open(str(tmp_path / "state.db"))
    try:
        created = await store.upsert_sandbox("sandbox", SandboxEnsureRequest())
        assert created.desired_generation == 1
        with store._store._lock, store._store._conn:
            store._store._conn.execute(
                "UPDATE sandboxes SET idle_since_at = ? WHERE sandbox_id = 'sandbox'",
                (time.time() - 1000,),
            )
        assert [row.sandbox_id for row in await store.idle_sandboxes(60)] == ["sandbox"]

        suspended = await store.mark_pod_stopped("sandbox")
        assert suspended is not None
        assert suspended.desired_state == "suspended"
        assert suspended.desired_generation == 2
        suspended_again = await store.mark_pod_stopped("sandbox")
        assert suspended_again is not None
        assert suspended_again.desired_generation == 2
        assert await store.idle_sandboxes(0) == []

        resumed = await store.ensure_sandbox_defaults("sandbox")
        assert resumed.desired_state == "present"
        assert resumed.desired_generation == 3
        resumed_again = await store.ensure_sandbox_defaults("sandbox")
        assert resumed_again.desired_generation == 3
        assert await store.idle_sandboxes(60) == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_activity_lease_defers_idle_start_until_release(tmp_path):
    store = await SQLiteStateStore.open(str(tmp_path / "state.db"))
    try:
        await store.upsert_sandbox("sandbox", SandboxEnsureRequest())
        lease = await store.acquire_activity_lease(
            "sandbox",
            session_id=None,
            operation="job",
            owner="manager-a",
            ttl_seconds=60,
        )
        assert lease is not None

        await store.mark_idle_if_empty("sandbox")
        with store._store._lock:
            before_release = store._store._conn.execute(
                "SELECT idle_since_at FROM sandboxes WHERE sandbox_id = 'sandbox'"
            ).fetchone()[0]
        assert before_release is None

        release_started_at = time.time()
        assert await store.release_activity_lease(lease.lease_id, owner="manager-a")
        with store._store._lock:
            row = store._store._conn.execute(
                """
                SELECT idle_since_at, last_active_at FROM sandboxes
                WHERE sandbox_id = 'sandbox'
                """
            ).fetchone()
        assert row[0] >= release_started_at
        assert row[1] >= release_started_at
        assert await store.idle_sandboxes(60) == []
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
        assert [
            orphan.provider_id
            for orphan in await store.list_orphans(
                "e2b",
                sandbox_id="sandbox",
            )
        ] == ["provider-orphan"]
        assert await store.expired_orphans(10, inventory_started_at=now) == []

        await store.reconcile_provider_allocations(
            "e2b:test",
            {"provider-during-claim": ("sandbox", None)},
            inventory_started_at=now,
        )
        assert await store.list_provider_allocations("e2b:test") == []

        assert await store.release_lifecycle_claim(claim.claim_id, owner="manager-a")
        await store.reconcile_provider_allocations(
            "e2b:test",
            {"provider-after-claim": ("sandbox", None)},
            inventory_started_at=now + 1,
        )
        assert [
            row.provider_id
            for row in await store.list_provider_allocations("e2b:test")
        ] == ["provider-after-claim"]
        candidates = await store.expired_orphans(10, inventory_started_at=now)
        assert [candidate.provider_id for candidate in candidates] == [
            "provider-orphan"
        ]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_provider_final_slot_is_atomic_across_sqlite_managers(tmp_path):
    path = str(tmp_path / "state.db")
    first = await SQLiteStateStore.open(path)
    second = await SQLiteStateStore.open(path)
    try:
        await first.upsert_sandbox("one", SandboxEnsureRequest())
        await first.upsert_sandbox("two", SandboxEnsureRequest())
        results = await asyncio.gather(
            first.reserve_provider_allocation(
                "e2b:test", "one", owner="manager-a", max_active=1, ttl_seconds=60
            ),
            second.reserve_provider_allocation(
                "e2b:test", "two", owner="manager-b", max_active=1, ttl_seconds=60
            ),
        )
        assert sum(result is not None for result in results) == 1
        assert len(await first.list_provider_allocations("e2b:test")) == 1
    finally:
        await first.close()
        await second.close()


@pytest.mark.asyncio
async def test_provider_reconcile_counts_duplicate_objects_and_adopts_reservation(
    tmp_path,
):
    store = await SQLiteStateStore.open(str(tmp_path / "state.db"))
    try:
        await store.upsert_sandbox("same", SandboxEnsureRequest())
        reservation = await store.reserve_provider_allocation(
            "e2b:test",
            "same",
            owner="manager-a",
            max_active=10,
            ttl_seconds=600,
        )
        assert reservation is not None
        inventory_started_at = time.time() + 0.01
        await store.reconcile_provider_allocations(
            "e2b:test",
            {
                "provider-one": ("same", reservation.allocation_id),
                "provider-two": ("same", None),
            },
            inventory_started_at=inventory_started_at,
        )
        allocations = await store.list_provider_allocations("e2b:test")
        assert {row.provider_id for row in allocations} == {
            "provider-one",
            "provider-two",
        }
        assert all(row.state == "active" for row in allocations)

        # One empty eventually consistent inventory snapshot must not erase
        # either the durable create-attempt token or a reconciler-discovered
        # provider generation. Only exact lifecycle cleanup releases them.
        await store.reconcile_provider_allocations(
            "e2b:test",
            {},
            inventory_started_at=inventory_started_at + 1,
        )
        after_empty_inventory = await store.list_provider_allocations("e2b:test")
        assert {
            (row.allocation_id, row.provider_id) for row in after_empty_inventory
        } == {(row.allocation_id, row.provider_id) for row in allocations}

        await store.upsert_sandbox("other", SandboxEnsureRequest())
        blocked = await store.reserve_provider_allocation(
            "e2b:test",
            "other",
            owner="manager-b",
            max_active=2,
            ttl_seconds=60,
        )
        assert blocked is None

        exact = next(row for row in allocations if row.provider_id == "provider-one")
        assert await store.release_provider_allocation("e2b:test", exact.allocation_id)
        remaining = await store.list_provider_allocations("e2b:test")
        assert [row.provider_id for row in remaining] == ["provider-two"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_activity_lease_is_fenced_by_another_manager_lifecycle_claim(tmp_path):
    store = await SQLiteStateStore.open(str(tmp_path / "state.db"))
    try:
        await store.upsert_sandbox("sandbox", SandboxEnsureRequest())
        await store.upsert_session("sandbox", "session", cwd="/workspace", env_keys=[])
        claim = await store.acquire_lifecycle_claim(
            "sandbox", operation="idle-suspend", owner="manager-a", ttl_seconds=60
        )
        assert claim is not None
        assert (
            await store.acquire_activity_lease(
                "sandbox",
                session_id=None,
                operation="http",
                owner="manager-b",
                ttl_seconds=60,
            )
            is None
        )
        own = await store.acquire_activity_lease(
            "sandbox",
            session_id=None,
            operation="http",
            owner="manager-a",
            ttl_seconds=60,
        )
        assert own is not None
        assert await store.mark_sandbox_active("sandbox", owner="manager-a")
        assert await store.touch_session("sandbox", "session", owner="manager-a")
        assert not await store.mark_sandbox_active("sandbox", owner="manager-b")
        assert not await store.touch_session("sandbox", "session", owner="manager-b")
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
    assert "update agentbox_sessions set active_operations" not in sql


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


@pytest.mark.asyncio
async def test_postgres_legacy_schema_migration_and_store_parity():
    database_url = os.environ.get("AGENTBOX_TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("AGENTBOX_TEST_POSTGRES_URL is not configured")

    from psycopg import AsyncConnection, sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    schema = f"agentbox_test_{uuid.uuid4().hex}"
    admin = await AsyncConnection.connect(database_url, autocommit=True)
    store = None
    try:
        await admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        await admin.execute(
            sql.SQL("SET search_path TO {}").format(sql.Identifier(schema))
        )
        await admin.execute(
            """
            CREATE TABLE agentbox_sandboxes (
                sandbox_id text PRIMARY KEY,
                env jsonb NOT NULL DEFAULT '{}'::jsonb,
                idle_since_at timestamptz,
                last_active_at timestamptz NOT NULL DEFAULT now(),
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            );
            CREATE TABLE agentbox_sessions (
                sandbox_id text NOT NULL REFERENCES agentbox_sandboxes(sandbox_id)
                    ON DELETE CASCADE,
                session_id text NOT NULL,
                cwd text NOT NULL,
                env_keys jsonb NOT NULL DEFAULT '[]'::jsonb,
                last_active_at timestamptz NOT NULL,
                active_operations integer NOT NULL DEFAULT 0
                    CHECK (active_operations >= 0),
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY (sandbox_id, session_id)
            );
            CREATE TABLE agentbox_workspace_volumes (
                sandbox_id text PRIMARY KEY,
                user_id text NOT NULL,
                volume_id text NOT NULL UNIQUE,
                volume_name text NOT NULL UNIQUE,
                last_activity_at timestamptz NOT NULL DEFAULT now(),
                last_observed_bytes bigint NOT NULL DEFAULT 0,
                over_quota boolean NOT NULL DEFAULT false,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            );
            INSERT INTO agentbox_sandboxes (sandbox_id, env)
            VALUES ('legacy', '{"LEMMA_BASE_URL":"https://api.example","TOKEN":"drop"}');
            INSERT INTO agentbox_sessions (
                sandbox_id, session_id, cwd, env_keys, last_active_at,
                active_operations
            ) VALUES ('legacy', 'session', '/workspace', '["TOKEN"]', now(), 9);
            INSERT INTO agentbox_workspace_volumes (
                sandbox_id, user_id, volume_id, volume_name
            ) VALUES ('legacy', 'user', 'volume-id', 'volume-name');
            """
        )

        params = conninfo_to_dict(database_url)
        params["options"] = f"-csearch_path={schema}"
        store = await PostgresStateStore.open(make_conninfo(**params))

        legacy = await store.get_sandbox("legacy")
        session = await store.get_session("legacy", "session")
        assert legacy is not None
        assert legacy.env == {"LEMMA_BASE_URL": "https://api.example"}
        assert session is not None
        assert session.active_operations == 9
        assert await store.mark_sandbox_active("missing") is False
        assert await store.mark_sandbox_active("legacy") is True
        assert await store.touch_session("legacy", "session") is True

        await store.ensure_sandbox_defaults("idle")
        await store.upsert_session("idle", "session", cwd="/workspace", env_keys=[])
        await admin.execute(
            """
            UPDATE agentbox_sessions
            SET last_active_at = now() - interval '120 seconds'
            WHERE sandbox_id = 'idle';
            UPDATE agentbox_sandboxes
            SET last_active_at = now() - interval '120 seconds',
                idle_since_at = NULL
            WHERE sandbox_id = 'idle';
            """
        )
        assert [row.session_id for row in await store.expired_sessions(60)] == [
            "session"
        ]
        assert await store.delete_session("idle", "session") is True
        idle_record = await store.get_sandbox("idle")
        assert idle_record is not None
        assert idle_record.idle_since_at is not None
        assert idle_record.idle_since_at < time.time() - 60
        assert "idle" in {row.sandbox_id for row in await store.idle_sandboxes(60)}
        assert await store.mark_pod_stopped("idle") is not None

        defaults = await asyncio.gather(
            *(store.ensure_sandbox_defaults("new") for _ in range(10))
        )
        assert {record.desired_generation for record in defaults} == {1}
        suspended = await store.mark_pod_stopped("new")
        assert suspended is not None
        assert suspended.desired_state == "suspended"
        assert suspended.desired_generation == 2
        assert await store.idle_sandboxes(0) == []
        resumed = await store.ensure_sandbox_defaults("new")
        assert resumed.desired_state == "present"
        assert resumed.desired_generation == 3

        tombstone = await store.insert_sandbox_tombstone_if_missing("orphan")
        assert tombstone.desired_state == "deleted"

        await store.observe_orphan(
            "e2b",
            "provider-orphan",
            sandbox_id="orphan",
            observed_at=time.time(),
        )
        assert [
            candidate.provider_id
            for candidate in await store.list_orphans(
                "e2b",
                sandbox_id="orphan",
            )
        ] == ["provider-orphan"]

        claim = await store.acquire_lifecycle_claim(
            "new", operation="ensure", owner="manager-a", ttl_seconds=60
        )
        assert claim is not None
        await store.reconcile_provider_allocations(
            "e2b:test",
            {"provider-during-claim": ("new", None)},
            inventory_started_at=time.time(),
        )
        assert await store.list_provider_allocations("e2b:test") == []
        assert await store.release_lifecycle_claim(claim.claim_id, owner="manager-a")
        await store.reconcile_provider_allocations(
            "e2b:test",
            {"provider-after-claim": ("new", None)},
            inventory_started_at=time.time(),
        )
        assert [
            row.provider_id
            for row in await store.list_provider_allocations("e2b:test")
        ] == ["provider-after-claim"]

        await store.reconcile_provider_inventory(
            "e2b:inventory",
            "e2b",
            {"provider-paused": ("new", "attempt-paused", False)},
            inventory_started_at=time.time(),
        )
        assert [
            orphan.provider_id
            for orphan in await store.list_orphans("e2b", sandbox_id="new")
        ] == ["provider-paused"]

        lease = await store.acquire_activity_lease(
            "new",
            session_id=None,
            operation="job",
            owner="manager-a",
            ttl_seconds=60,
        )
        assert lease is not None
        await store.mark_idle_if_empty("new")
        assert await store.idle_sandboxes(0) == []
        assert await store.release_activity_lease(lease.lease_id, owner="manager-a")
        assert await store.idle_sandboxes(60) == []

        volume = await (
            await admin.execute(
                """
                SELECT volume_id FROM agentbox_workspace_volumes
                WHERE sandbox_id = 'legacy'
                """
            )
        ).fetchone()
        assert volume is not None and volume[0] == "volume-id"
    finally:
        if store is not None:
            await store.close()
        await admin.execute(
            sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
        )
        await admin.close()
