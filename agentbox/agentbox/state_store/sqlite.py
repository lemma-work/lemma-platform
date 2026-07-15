from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from collections.abc import Callable
from typing import Any, TypeVar

from agentbox.schemas import SandboxEnsureRequest
from agentbox.state import AgentBoxStateStore

from .migrations import SQLITE_MIGRATIONS
from .models import (
    ActivityLease,
    DesiredSandboxState,
    LifecycleClaim,
    OrphanCandidate,
    ProviderAllocation,
    SandboxRecord,
    SessionRecord,
)

T = TypeVar("T")
_LEGACY_OPERATION_STALE_SECONDS = 2 * 60 * 60


class SQLiteStateStore:
    """Async SQLite state store preserving the original on-disk schema.

    sqlite3 calls are serialized by the compatibility store and dispatched to
    worker threads so manager request handlers never block the event loop.
    """

    def __init__(
        self,
        path: str,
        *,
        durable_env_keys: frozenset[str] = frozenset({"LEMMA_BASE_URL"}),
    ) -> None:
        self.path = path
        self.durable_env_keys = durable_env_keys
        self._legacy: AgentBoxStateStore | None = None

    @classmethod
    async def open(
        cls,
        path: str,
        *,
        durable_env_keys: frozenset[str] = frozenset({"LEMMA_BASE_URL"}),
    ) -> SQLiteStateStore:
        self = cls(path, durable_env_keys=durable_env_keys)
        self._legacy = await asyncio.to_thread(AgentBoxStateStore, path)
        await self._run(self._apply_migrations)
        return self

    @property
    def _store(self) -> AgentBoxStateStore:
        if self._legacy is None:
            raise RuntimeError("state store is not open")
        return self._legacy

    async def _run(self, callback: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        return await asyncio.to_thread(callback, *args, **kwargs)

    def _apply_migrations(self) -> None:
        store = self._store
        with store._lock:
            store._conn.execute("BEGIN IMMEDIATE")
            try:
                store._conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS agentbox_schema_migrations (
                        version INTEGER PRIMARY KEY,
                        name TEXT NOT NULL,
                        applied_at REAL NOT NULL
                    )
                    """
                )
                applied = {
                    int(row[0])
                    for row in store._conn.execute(
                        "SELECT version FROM agentbox_schema_migrations"
                    ).fetchall()
                }
                for version, name, statements in SQLITE_MIGRATIONS:
                    if version in applied:
                        continue
                    existing_sandbox_columns = {
                        str(row["name"])
                        for row in store._conn.execute(
                            "PRAGMA table_info(sandboxes)"
                        ).fetchall()
                    }
                    for statement in statements:
                        if version == 2:
                            column_name = (
                                statement.split("ADD COLUMN", 1)[1].strip().split()[0]
                            )
                            if column_name in existing_sandbox_columns:
                                continue
                        store._conn.execute(statement)
                    store._conn.execute(
                        """
                        INSERT INTO agentbox_schema_migrations (version, name, applied_at)
                        VALUES (?, ?, ?)
                        """,
                        (version, name, time.time()),
                    )
                rows = store._conn.execute(
                    "SELECT sandbox_id, env_json FROM sandboxes"
                ).fetchall()
                for row in rows:
                    try:
                        env = json.loads(row["env_json"])
                    except (TypeError, json.JSONDecodeError):
                        env = {}
                    sanitized = {
                        key: str(value)
                        for key, value in env.items()
                        if key in self.durable_env_keys
                    }
                    store._conn.execute(
                        "UPDATE sandboxes SET env_json = ? WHERE sandbox_id = ?",
                        (json.dumps(sanitized, sort_keys=True), row["sandbox_id"]),
                    )
                store._conn.execute(
                    """
                    UPDATE sandboxes
                    SET last_active_at = COALESCE(last_active_at, updated_at, created_at)
                    WHERE last_active_at IS NULL
                    """
                )
            except BaseException:
                store._conn.rollback()
                raise
            else:
                store._conn.commit()

    def _upsert_sandbox(
        self, sandbox_id: str, request: SandboxEnsureRequest
    ) -> SandboxRecord:
        store = self._store
        now = time.time()
        env = {
            key: value
            for key, value in request.env.items()
            if key in self.durable_env_keys
        }
        with store._lock, store._conn:
            store._conn.execute(
                """
                INSERT INTO sandboxes (
                    sandbox_id, env_json, idle_since_at, created_at, updated_at,
                    desired_state, desired_generation, last_active_at
                ) VALUES (?, ?, NULL, ?, ?, 'present', 1, ?)
                ON CONFLICT(sandbox_id) DO UPDATE SET
                    env_json = excluded.env_json,
                    idle_since_at = NULL,
                    desired_state = 'present',
                    desired_generation = CASE
                        WHEN sandboxes.env_json <> excluded.env_json
                          OR sandboxes.desired_state <> 'present'
                        THEN sandboxes.desired_generation + 1
                        ELSE sandboxes.desired_generation
                    END,
                    last_active_at = excluded.last_active_at,
                    updated_at = excluded.updated_at
                """,
                (sandbox_id, json.dumps(env, sort_keys=True), now, now, now),
            )
            row = store._conn.execute(
                "SELECT * FROM sandboxes WHERE sandbox_id = ?", (sandbox_id,)
            ).fetchone()
        if row is None:
            raise RuntimeError("failed to read newly upserted sandbox")
        return store._record_from_row(row)

    async def upsert_sandbox(
        self, sandbox_id: str, request: SandboxEnsureRequest
    ) -> SandboxRecord:
        return await self._run(self._upsert_sandbox, sandbox_id, request)

    async def insert_sandbox_if_missing(self, sandbox_id: str) -> SandboxRecord:
        def insert() -> SandboxRecord:
            store = self._store
            now = time.time()
            with store._lock, store._conn:
                store._conn.execute(
                    """
                    INSERT INTO sandboxes (
                        sandbox_id, env_json, idle_since_at, created_at, updated_at,
                        desired_state, desired_generation, last_active_at
                    ) VALUES (?, '{}', NULL, ?, ?, 'present', 1, ?)
                    ON CONFLICT(sandbox_id) DO NOTHING
                    """,
                    (sandbox_id, now, now, now),
                )
                row = store._conn.execute(
                    "SELECT * FROM sandboxes WHERE sandbox_id = ?", (sandbox_id,)
                ).fetchone()
            if row is None:
                raise RuntimeError("failed to insert sandbox defaults")
            return store._record_from_row(row)

        return await self._run(insert)

    async def insert_sandbox_tombstone_if_missing(
        self, sandbox_id: str
    ) -> SandboxRecord:
        """Create a deletion fence without changing an existing sandbox row."""

        def insert() -> SandboxRecord:
            store = self._store
            now = time.time()
            with store._lock, store._conn:
                store._conn.execute(
                    """
                    INSERT INTO sandboxes (
                        sandbox_id, env_json, idle_since_at, created_at, updated_at,
                        desired_state, desired_generation, last_active_at
                    ) VALUES (?, '{}', NULL, ?, ?, 'deleted', 1, ?)
                    ON CONFLICT(sandbox_id) DO NOTHING
                    """,
                    (sandbox_id, now, now, now),
                )
                row = store._conn.execute(
                    "SELECT * FROM sandboxes WHERE sandbox_id = ?", (sandbox_id,)
                ).fetchone()
            if row is None:
                raise RuntimeError("failed to insert sandbox tombstone")
            return store._record_from_row(row)

        return await self._run(insert)

    async def ensure_sandbox_defaults(self, sandbox_id: str) -> SandboxRecord:
        def ensure() -> SandboxRecord:
            store = self._store
            now = time.time()
            with store._lock, store._conn:
                store._conn.execute(
                    """
                    INSERT INTO sandboxes (
                        sandbox_id, env_json, idle_since_at, created_at, updated_at,
                        desired_state, desired_generation, last_active_at
                    ) VALUES (?, '{}', NULL, ?, ?, 'present', 1, ?)
                    ON CONFLICT(sandbox_id) DO UPDATE SET
                        idle_since_at = NULL,
                        desired_state = 'present',
                        desired_generation = CASE
                            WHEN sandboxes.desired_state <> 'present'
                            THEN sandboxes.desired_generation + 1
                            ELSE sandboxes.desired_generation
                        END,
                        last_active_at = excluded.last_active_at,
                        updated_at = excluded.updated_at
                    """,
                    (sandbox_id, now, now, now),
                )
                row = store._conn.execute(
                    "SELECT * FROM sandboxes WHERE sandbox_id = ?", (sandbox_id,)
                ).fetchone()
            if row is None:
                raise RuntimeError("failed to ensure sandbox defaults")
            return store._record_from_row(row)

        return await self._run(ensure)

    async def get_sandbox(self, sandbox_id: str) -> SandboxRecord | None:
        return await self._run(self._store.get_sandbox, sandbox_id)

    def _list_sandboxes(self) -> list[SandboxRecord]:
        store = self._store
        with store._lock:
            rows = store._conn.execute(
                "SELECT * FROM sandboxes ORDER BY sandbox_id"
            ).fetchall()
        return [store._record_from_row(row) for row in rows]

    async def list_sandboxes(self) -> list[SandboxRecord]:
        return await self._run(self._list_sandboxes)

    def _delete_sandbox(self, sandbox_id: str) -> None:
        store = self._store
        with store._lock, store._conn:
            store._conn.execute(
                "DELETE FROM agentbox_activity_leases WHERE sandbox_id = ?",
                (sandbox_id,),
            )
            store._conn.execute(
                "DELETE FROM agentbox_lifecycle_claims WHERE sandbox_id = ?",
                (sandbox_id,),
            )
            store._conn.execute(
                "DELETE FROM sessions WHERE sandbox_id = ?", (sandbox_id,)
            )
            store._conn.execute(
                "DELETE FROM sandboxes WHERE sandbox_id = ?", (sandbox_id,)
            )

    async def delete_sandbox(self, sandbox_id: str) -> None:
        await self._run(self._delete_sandbox, sandbox_id)

    def _set_sandbox_desired_state(
        self, sandbox_id: str, desired_state: DesiredSandboxState
    ) -> SandboxRecord | None:
        store = self._store
        now = time.time()
        with store._lock, store._conn:
            row = store._conn.execute(
                """
                UPDATE sandboxes SET desired_state = ?,
                    desired_generation = CASE
                        WHEN desired_state <> ? THEN desired_generation + 1
                        ELSE desired_generation
                    END,
                    updated_at = ?
                WHERE sandbox_id = ? RETURNING *
                """,
                (desired_state, desired_state, now, sandbox_id),
            ).fetchone()
        return store._record_from_row(row) if row else None

    async def set_sandbox_desired_state(
        self, sandbox_id: str, desired_state: DesiredSandboxState
    ) -> SandboxRecord | None:
        if desired_state not in {"present", "suspended", "deleted"}:
            raise ValueError(
                "desired_state must be 'present', 'suspended', or 'deleted'"
            )
        return await self._run(
            self._set_sandbox_desired_state, sandbox_id, desired_state
        )

    def _set_sandbox_observation(
        self,
        sandbox_id: str,
        provider_name: str,
        provider_id: str,
        instance_id: str | None,
        observed_generation: int,
    ) -> SandboxRecord | None:
        store = self._store
        now = time.time()
        with store._lock, store._conn:
            row = store._conn.execute(
                """
                UPDATE sandboxes SET provider_name = ?, provider_id = ?,
                    instance_id = ?, observed_generation = ?,
                    last_observed_at = ?, updated_at = ?
                WHERE sandbox_id = ? AND desired_generation = ?
                RETURNING *
                """,
                (
                    provider_name,
                    provider_id,
                    instance_id,
                    observed_generation,
                    now,
                    now,
                    sandbox_id,
                    observed_generation,
                ),
            ).fetchone()
        return store._record_from_row(row) if row else None

    async def set_sandbox_observation(
        self,
        sandbox_id: str,
        *,
        provider_name: str,
        provider_id: str,
        instance_id: str | None,
        observed_generation: int,
    ) -> SandboxRecord | None:
        return await self._run(
            self._set_sandbox_observation,
            sandbox_id,
            provider_name,
            provider_id,
            instance_id,
            observed_generation,
        )

    def _set_sandbox_provider_identity(
        self,
        sandbox_id: str,
        provider_name: str,
        provider_id: str,
        instance_id: str | None,
        desired_generation: int,
    ) -> SandboxRecord | None:
        store = self._store
        now = time.time()
        with store._lock, store._conn:
            row = store._conn.execute(
                """
                UPDATE sandboxes SET provider_name = ?, provider_id = ?,
                    instance_id = ?, last_observed_at = ?, updated_at = ?
                WHERE sandbox_id = ? AND desired_state = 'present'
                  AND desired_generation = ?
                  AND (provider_id IS NULL OR provider_id = ?)
                RETURNING *
                """,
                (
                    provider_name,
                    provider_id,
                    instance_id,
                    now,
                    now,
                    sandbox_id,
                    desired_generation,
                    provider_id,
                ),
            ).fetchone()
        return store._record_from_row(row) if row else None

    async def set_sandbox_provider_identity(
        self,
        sandbox_id: str,
        *,
        provider_name: str,
        provider_id: str,
        instance_id: str | None,
        desired_generation: int,
    ) -> SandboxRecord | None:
        return await self._run(
            self._set_sandbox_provider_identity,
            sandbox_id,
            provider_name,
            provider_id,
            instance_id,
            desired_generation,
        )

    def _clear_sandbox_provider_identity(
        self,
        sandbox_id: str,
        provider_id: str,
        desired_generation: int,
    ) -> SandboxRecord | None:
        store = self._store
        now = time.time()
        with store._lock, store._conn:
            row = store._conn.execute(
                """
                UPDATE sandboxes SET provider_name = NULL, provider_id = NULL,
                    instance_id = NULL, observed_generation = 0,
                    last_observed_at = NULL, updated_at = ?
                WHERE sandbox_id = ? AND provider_id = ?
                  AND desired_generation = ?
                RETURNING *
                """,
                (now, sandbox_id, provider_id, desired_generation),
            ).fetchone()
        return store._record_from_row(row) if row else None

    async def clear_sandbox_provider_identity(
        self,
        sandbox_id: str,
        *,
        provider_id: str,
        desired_generation: int,
    ) -> SandboxRecord | None:
        return await self._run(
            self._clear_sandbox_provider_identity,
            sandbox_id,
            provider_id,
            desired_generation,
        )

    async def upsert_session(
        self,
        sandbox_id: str,
        session_id: str,
        *,
        cwd: str,
        env_keys: list[str],
    ) -> SessionRecord:
        record = await self._run(
            self._store.upsert_session,
            sandbox_id,
            session_id,
            cwd=cwd,
            env_keys=env_keys,
        )
        await self._touch_sandbox_activity(sandbox_id)
        return record

    async def _touch_sandbox_activity(
        self, sandbox_id: str, owner: str | None = None
    ) -> bool:
        def touch() -> bool:
            store = self._store
            now = time.time()
            with store._lock, store._conn:
                cursor = store._conn.execute(
                    """
                    UPDATE sandboxes SET idle_since_at = NULL,
                        last_active_at = ?, updated_at = ?
                    WHERE sandbox_id = ? AND desired_state = 'present'
                      AND NOT EXISTS (
                        SELECT 1 FROM agentbox_lifecycle_claims c
                        WHERE c.sandbox_id = sandboxes.sandbox_id
                          AND c.expires_at > ?
                          AND (? IS NULL OR c.owner <> ?)
                      )
                    """,
                    (now, now, sandbox_id, now, owner, owner),
                )
                return bool(cursor.rowcount)

        return await self._run(touch)

    async def touch_session(
        self, sandbox_id: str, session_id: str, *, owner: str | None = None
    ) -> bool:
        touched = await self._run(
            self._touch_session_fenced,
            sandbox_id,
            session_id,
            owner,
        )
        if touched:
            await self._touch_sandbox_activity(sandbox_id, owner)
        return touched

    def _touch_session_fenced(
        self, sandbox_id: str, session_id: str, owner: str | None
    ) -> bool:
        store = self._store
        now = time.time()
        with store._lock, store._conn:
            cursor = store._conn.execute(
                """
                UPDATE sessions SET last_active_at = ?, updated_at = ?
                WHERE sandbox_id = ? AND session_id = ?
                  AND NOT EXISTS (
                    SELECT 1 FROM agentbox_lifecycle_claims c
                    WHERE c.sandbox_id = sessions.sandbox_id
                      AND c.expires_at > ?
                      AND (? IS NULL OR c.owner <> ?)
                  )
                """,
                (now, now, sandbox_id, session_id, now, owner, owner),
            )
            return bool(cursor.rowcount)

    async def get_session(
        self, sandbox_id: str, session_id: str
    ) -> SessionRecord | None:
        return await self._run(self._store.get_session, sandbox_id, session_id)

    def _delete_session(self, sandbox_id: str, session_id: str) -> bool:
        store = self._store
        with store._lock, store._conn:
            store._conn.execute(
                """
                DELETE FROM agentbox_activity_leases
                WHERE sandbox_id = ? AND session_id = ?
                """,
                (sandbox_id, session_id),
            )
            cursor = store._conn.execute(
                "DELETE FROM sessions WHERE sandbox_id = ? AND session_id = ?",
                (sandbox_id, session_id),
            )
            self._mark_idle_if_empty_locked(sandbox_id)
            return bool(cursor.rowcount)

    async def delete_session(self, sandbox_id: str, session_id: str) -> bool:
        return await self._run(self._delete_session, sandbox_id, session_id)

    def _delete_sandbox_sessions(self, sandbox_id: str) -> int:
        store = self._store
        with store._lock, store._conn:
            store._conn.execute(
                "DELETE FROM agentbox_activity_leases WHERE sandbox_id = ?",
                (sandbox_id,),
            )
            cursor = store._conn.execute(
                "DELETE FROM sessions WHERE sandbox_id = ?", (sandbox_id,)
            )
            self._mark_idle_if_empty_locked(sandbox_id)
            return int(cursor.rowcount)

    async def delete_sandbox_sessions(self, sandbox_id: str) -> int:
        return await self._run(self._delete_sandbox_sessions, sandbox_id)

    def _expired_sessions(self, idle_timeout_seconds: int) -> list[SessionRecord]:
        store = self._store
        now = time.time()
        cutoff = now - idle_timeout_seconds
        with store._lock, store._conn:
            store._conn.execute(
                "DELETE FROM agentbox_activity_leases WHERE expires_at <= ?", (now,)
            )
            store._conn.execute(
                """
                UPDATE sessions SET active_operations = 0, updated_at = ?
                WHERE active_operations > 0 AND updated_at < ?
                  AND NOT EXISTS (
                    SELECT 1 FROM agentbox_activity_leases l
                    WHERE l.sandbox_id = sessions.sandbox_id
                      AND (l.session_id IS NULL OR l.session_id = sessions.session_id)
                      AND l.expires_at > ?
                  )
                """,
                (now, now - _LEGACY_OPERATION_STALE_SECONDS, now),
            )
            rows = store._conn.execute(
                """
                SELECT s.* FROM sessions s
                WHERE s.last_active_at < ? AND s.active_operations = 0
                  AND NOT EXISTS (
                    SELECT 1 FROM agentbox_activity_leases l
                    WHERE l.sandbox_id = s.sandbox_id
                      AND (l.session_id IS NULL OR l.session_id = s.session_id)
                      AND l.expires_at > ?
                  )
                ORDER BY s.last_active_at ASC
                """,
                (cutoff, now),
            ).fetchall()
        return [store._session_from_row(row) for row in rows]

    async def expired_sessions(self, idle_timeout_seconds: int) -> list[SessionRecord]:
        return await self._run(self._expired_sessions, idle_timeout_seconds)

    def _idle_sandboxes(self, idle_timeout_seconds: int) -> list[SandboxRecord]:
        store = self._store
        now = time.time()
        cutoff = now - idle_timeout_seconds
        with store._lock, store._conn:
            store._conn.execute(
                "DELETE FROM agentbox_activity_leases WHERE expires_at <= ?", (now,)
            )
            store._conn.execute(
                """
                UPDATE sandboxes SET idle_since_at = COALESCE(last_active_at, ?)
                WHERE idle_since_at IS NULL
                  AND desired_state = 'present'
                  AND NOT EXISTS (
                    SELECT 1 FROM sessions
                    WHERE sessions.sandbox_id = sandboxes.sandbox_id
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM agentbox_activity_leases l
                    WHERE l.sandbox_id = sandboxes.sandbox_id AND l.expires_at > ?
                  )
                """,
                (now, now),
            )
            rows = store._conn.execute(
                """
                SELECT s.* FROM sandboxes s
                WHERE s.desired_state = 'present'
                  AND s.idle_since_at IS NOT NULL AND s.idle_since_at < ?
                  AND NOT EXISTS (
                    SELECT 1 FROM agentbox_activity_leases l
                    WHERE l.sandbox_id = s.sandbox_id AND l.expires_at > ?
                  )
                ORDER BY s.idle_since_at ASC
                """,
                (cutoff, now),
            ).fetchall()
        return [store._record_from_row(row) for row in rows]

    async def idle_sandboxes(self, idle_timeout_seconds: int) -> list[SandboxRecord]:
        return await self._run(self._idle_sandboxes, idle_timeout_seconds)

    async def mark_sandbox_active(
        self, sandbox_id: str, *, owner: str | None = None
    ) -> bool:
        return await self._touch_sandbox_activity(sandbox_id, owner)

    def _mark_pod_stopped(self, sandbox_id: str) -> SandboxRecord | None:
        store = self._store
        now = time.time()
        with store._lock, store._conn:
            row = store._conn.execute(
                """
                UPDATE sandboxes SET idle_since_at = ?,
                    desired_state = 'suspended',
                    desired_generation = CASE
                        WHEN desired_state <> 'suspended'
                        THEN desired_generation + 1
                        ELSE desired_generation
                    END,
                    updated_at = ?
                WHERE sandbox_id = ? RETURNING *
                """,
                (now, now, sandbox_id),
            ).fetchone()
        return store._record_from_row(row) if row else None

    async def mark_pod_stopped(self, sandbox_id: str) -> SandboxRecord | None:
        return await self._run(self._mark_pod_stopped, sandbox_id)

    def _mark_idle_if_empty_locked(
        self, sandbox_id: str, now: float | None = None
    ) -> None:
        now = now if now is not None else time.time()
        self._store._conn.execute(
            """
            UPDATE sandboxes SET idle_since_at = COALESCE(idle_since_at, ?),
                updated_at = ?
            WHERE sandbox_id = ?
              AND desired_state = 'present'
              AND NOT EXISTS (
                  SELECT 1 FROM sessions
                  WHERE sessions.sandbox_id = sandboxes.sandbox_id
              )
              AND NOT EXISTS (
                  SELECT 1 FROM agentbox_activity_leases l
                  WHERE l.sandbox_id = sandboxes.sandbox_id AND l.expires_at > ?
              )
            """,
            (now, now, sandbox_id, now),
        )

    def _mark_idle_if_empty(self, sandbox_id: str) -> None:
        store = self._store
        with store._lock, store._conn:
            self._mark_idle_if_empty_locked(sandbox_id)

    async def mark_idle_if_empty(self, sandbox_id: str) -> None:
        await self._run(self._mark_idle_if_empty, sandbox_id)

    def _acquire_activity_lease(
        self,
        sandbox_id: str,
        session_id: str | None,
        operation: str,
        owner: str,
        ttl_seconds: float,
    ) -> ActivityLease | None:
        store = self._store
        now = time.time()
        lease_id = str(uuid.uuid4())
        expires_at = now + ttl_seconds
        with store._lock, store._conn:
            if not store._conn.execute(
                "SELECT 1 FROM sandboxes WHERE sandbox_id = ?", (sandbox_id,)
            ).fetchone():
                return None
            if session_id is None:
                exists = store._conn.execute(
                    """
                    SELECT 1 FROM sandboxes
                    WHERE sandbox_id = ? AND desired_state = 'present'
                      AND NOT EXISTS (
                        SELECT 1 FROM agentbox_lifecycle_claims c
                        WHERE c.sandbox_id = sandboxes.sandbox_id
                          AND c.expires_at > ? AND c.owner <> ?
                      )
                    """,
                    (sandbox_id, now, owner),
                ).fetchone()
            else:
                exists = store._conn.execute(
                    """
                    SELECT 1 FROM sessions x JOIN sandboxes s
                      ON s.sandbox_id = x.sandbox_id
                    WHERE x.sandbox_id = ? AND x.session_id = ?
                      AND s.desired_state = 'present'
                      AND NOT EXISTS (
                        SELECT 1 FROM agentbox_lifecycle_claims c
                        WHERE c.sandbox_id = s.sandbox_id AND c.expires_at > ?
                          AND c.owner <> ?
                      )
                    """,
                    (sandbox_id, session_id, now, owner),
                ).fetchone()
            if not exists:
                return None
            store._conn.execute(
                """
                INSERT INTO agentbox_activity_leases (
                    lease_id, sandbox_id, session_id, operation, owner,
                    expires_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lease_id,
                    sandbox_id,
                    session_id,
                    operation,
                    owner,
                    expires_at,
                    now,
                    now,
                ),
            )
            store._conn.execute(
                """
                UPDATE sandboxes SET idle_since_at = NULL,
                    last_active_at = ?, updated_at = ?
                WHERE sandbox_id = ?
                """,
                (now, now, sandbox_id),
            )
        return ActivityLease(
            lease_id, sandbox_id, session_id, operation, owner, expires_at
        )

    async def acquire_activity_lease(
        self,
        sandbox_id: str,
        *,
        session_id: str | None,
        operation: str,
        owner: str,
        ttl_seconds: float,
    ) -> ActivityLease | None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        return await self._run(
            self._acquire_activity_lease,
            sandbox_id,
            session_id,
            operation,
            owner,
            ttl_seconds,
        )

    def _renew_activity_lease(
        self, lease_id: str, owner: str, ttl_seconds: float
    ) -> ActivityLease | None:
        store = self._store
        now = time.time()
        expires_at = now + ttl_seconds
        with store._lock, store._conn:
            row = store._conn.execute(
                """
                UPDATE agentbox_activity_leases
                SET expires_at = ?, updated_at = ?
                WHERE lease_id = ? AND owner = ? AND expires_at > ?
                RETURNING *
                """,
                (expires_at, now, lease_id, owner, now),
            ).fetchone()
        return self._activity_lease(row) if row else None

    async def renew_activity_lease(
        self, lease_id: str, *, owner: str, ttl_seconds: float
    ) -> ActivityLease | None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        return await self._run(self._renew_activity_lease, lease_id, owner, ttl_seconds)

    def _release_activity_lease(self, lease_id: str, owner: str) -> bool:
        store = self._store
        now = time.time()
        with store._lock, store._conn:
            row = store._conn.execute(
                """
                DELETE FROM agentbox_activity_leases
                WHERE lease_id = ? AND owner = ?
                RETURNING sandbox_id, session_id
                """,
                (lease_id, owner),
            ).fetchone()
            if row is None:
                return False
            sandbox_id = str(row["sandbox_id"])
            session_id = row["session_id"]
            if session_id is not None:
                store._conn.execute(
                    """
                    UPDATE sessions SET last_active_at = ?, updated_at = ?
                    WHERE sandbox_id = ? AND session_id = ?
                    """,
                    (now, now, sandbox_id, session_id),
                )
            store._conn.execute(
                """
                UPDATE sandboxes SET idle_since_at = NULL,
                    last_active_at = ?, updated_at = ?
                WHERE sandbox_id = ?
                """,
                (now, now, sandbox_id),
            )
            self._mark_idle_if_empty_locked(sandbox_id, now)
            return True

    async def release_activity_lease(self, lease_id: str, *, owner: str) -> bool:
        return await self._run(self._release_activity_lease, lease_id, owner)

    def _prune_expired_activity_leases(self) -> int:
        store = self._store
        with store._lock, store._conn:
            cursor = store._conn.execute(
                "DELETE FROM agentbox_activity_leases WHERE expires_at <= ?",
                (time.time(),),
            )
            return int(cursor.rowcount)

    async def prune_expired_activity_leases(self) -> int:
        return await self._run(self._prune_expired_activity_leases)

    def _has_active_activity_lease(
        self,
        sandbox_id: str,
        session_id: str | None,
    ) -> bool:
        store = self._store
        session_filter = "" if session_id is None else "AND session_id = ?"
        params = (
            (sandbox_id, time.time())
            if session_id is None
            else (sandbox_id, time.time(), session_id)
        )
        with store._lock:
            row = store._conn.execute(
                f"""
                SELECT 1 FROM agentbox_activity_leases
                WHERE sandbox_id = ? AND expires_at > ?
                  {session_filter}
                LIMIT 1
                """,
                params,
            ).fetchone()
        return bool(row)

    async def has_active_activity_lease(
        self,
        sandbox_id: str,
        *,
        session_id: str | None = None,
    ) -> bool:
        return await self._run(
            self._has_active_activity_lease,
            sandbox_id,
            session_id,
        )

    def _acquire_lifecycle_claim(
        self,
        sandbox_id: str,
        operation: str,
        owner: str,
        ttl_seconds: float,
    ) -> LifecycleClaim | None:
        store = self._store
        now = time.time()
        claim_id = str(uuid.uuid4())
        expires_at = now + ttl_seconds
        with store._lock, store._conn:
            if not store._conn.execute(
                "SELECT 1 FROM sandboxes WHERE sandbox_id = ?", (sandbox_id,)
            ).fetchone():
                return None
            store._conn.execute(
                "DELETE FROM agentbox_lifecycle_claims WHERE sandbox_id = ? AND expires_at <= ?",
                (sandbox_id, now),
            )
            try:
                store._conn.execute(
                    """
                    INSERT INTO agentbox_lifecycle_claims (
                        sandbox_id, claim_id, operation, owner, expires_at,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (sandbox_id, claim_id, operation, owner, expires_at, now, now),
                )
            except sqlite3.IntegrityError:
                return None
        return LifecycleClaim(claim_id, sandbox_id, operation, owner, expires_at)

    async def acquire_lifecycle_claim(
        self,
        sandbox_id: str,
        *,
        operation: str,
        owner: str,
        ttl_seconds: float,
    ) -> LifecycleClaim | None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        return await self._run(
            self._acquire_lifecycle_claim,
            sandbox_id,
            operation,
            owner,
            ttl_seconds,
        )

    def _renew_lifecycle_claim(
        self, claim_id: str, owner: str, ttl_seconds: float
    ) -> LifecycleClaim | None:
        store = self._store
        now = time.time()
        expires_at = now + ttl_seconds
        with store._lock, store._conn:
            row = store._conn.execute(
                """
                UPDATE agentbox_lifecycle_claims
                SET expires_at = ?, updated_at = ?
                WHERE claim_id = ? AND owner = ? AND expires_at > ?
                RETURNING *
                """,
                (expires_at, now, claim_id, owner, now),
            ).fetchone()
        return self._lifecycle_claim(row) if row else None

    async def renew_lifecycle_claim(
        self, claim_id: str, *, owner: str, ttl_seconds: float
    ) -> LifecycleClaim | None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        return await self._run(
            self._renew_lifecycle_claim, claim_id, owner, ttl_seconds
        )

    def _release_lifecycle_claim(self, claim_id: str, owner: str) -> bool:
        store = self._store
        with store._lock, store._conn:
            cursor = store._conn.execute(
                "DELETE FROM agentbox_lifecycle_claims WHERE claim_id = ? AND owner = ?",
                (claim_id, owner),
            )
            return bool(cursor.rowcount)

    async def release_lifecycle_claim(self, claim_id: str, *, owner: str) -> bool:
        return await self._run(self._release_lifecycle_claim, claim_id, owner)

    def _observe_orphan(
        self,
        provider_name: str,
        provider_id: str,
        sandbox_id: str | None,
        observed_at: float,
    ) -> OrphanCandidate:
        store = self._store
        with store._lock, store._conn:
            row = store._conn.execute(
                """
                INSERT INTO agentbox_orphan_candidates (
                    provider_name, provider_id, sandbox_id,
                    first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(provider_name, provider_id) DO UPDATE SET
                    sandbox_id = COALESCE(excluded.sandbox_id, sandbox_id),
                    last_seen_at = excluded.last_seen_at
                RETURNING *
                """,
                (provider_name, provider_id, sandbox_id, observed_at, observed_at),
            ).fetchone()
        return self._orphan(row)

    async def observe_orphan(
        self,
        provider_name: str,
        provider_id: str,
        *,
        sandbox_id: str | None,
        observed_at: float | None = None,
    ) -> OrphanCandidate:
        return await self._run(
            self._observe_orphan,
            provider_name,
            provider_id,
            sandbox_id,
            observed_at if observed_at is not None else time.time(),
        )

    def _expired_orphans(
        self, grace_seconds: float, inventory_started_at: float
    ) -> list[OrphanCandidate]:
        store = self._store
        cutoff = inventory_started_at - grace_seconds
        with store._lock, store._conn:
            rows = store._conn.execute(
                """
                SELECT o.* FROM agentbox_orphan_candidates o
                WHERE o.first_seen_at <= ? AND o.last_seen_at <= ?
                  AND NOT EXISTS (
                    SELECT 1 FROM agentbox_lifecycle_claims c
                    WHERE c.sandbox_id = o.sandbox_id AND c.expires_at > ?
                  )
                ORDER BY o.first_seen_at
                """,
                (cutoff, inventory_started_at, time.time()),
            ).fetchall()
        return [self._orphan(row) for row in rows]

    async def expired_orphans(
        self,
        grace_seconds: float,
        *,
        inventory_started_at: float,
    ) -> list[OrphanCandidate]:
        if grace_seconds < 0:
            raise ValueError("grace_seconds cannot be negative")
        return await self._run(
            self._expired_orphans, grace_seconds, inventory_started_at
        )

    def _list_orphans(
        self,
        provider_name: str,
        sandbox_id: str | None,
    ) -> list[OrphanCandidate]:
        store = self._store
        sandbox_filter = "" if sandbox_id is None else "AND sandbox_id = ?"
        params = (
            (provider_name,)
            if sandbox_id is None
            else (provider_name, sandbox_id)
        )
        with store._lock, store._conn:
            rows = store._conn.execute(
                f"""
                SELECT * FROM agentbox_orphan_candidates
                WHERE provider_name = ? {sandbox_filter}
                ORDER BY first_seen_at, provider_id
                """,
                params,
            ).fetchall()
        return [self._orphan(row) for row in rows]

    async def list_orphans(
        self,
        provider_name: str,
        *,
        sandbox_id: str | None = None,
    ) -> list[OrphanCandidate]:
        return await self._run(self._list_orphans, provider_name, sandbox_id)

    def _clear_orphan(self, provider_name: str, provider_id: str) -> bool:
        store = self._store
        with store._lock, store._conn:
            cursor = store._conn.execute(
                """
                DELETE FROM agentbox_orphan_candidates
                WHERE provider_name = ? AND provider_id = ?
                """,
                (provider_name, provider_id),
            )
            return bool(cursor.rowcount)

    async def clear_orphan(self, provider_name: str, provider_id: str) -> bool:
        return await self._run(self._clear_orphan, provider_name, provider_id)

    def _reserve_provider_allocation(
        self,
        provider_scope: str,
        sandbox_id: str,
        owner: str,
        max_active: int,
        ttl_seconds: float,
    ) -> ProviderAllocation | None:
        store = self._store
        now = time.time()
        expires_at = now + ttl_seconds
        with store._lock:
            store._conn.execute("BEGIN IMMEDIATE")
            try:
                result = self._reserve_provider_allocation_locked(
                    provider_scope,
                    sandbox_id,
                    owner,
                    max_active,
                    now,
                    expires_at,
                )
                store._conn.commit()
                return result
            except BaseException:
                store._conn.rollback()
                raise

    def _reserve_provider_allocation_locked(
        self,
        provider_scope: str,
        sandbox_id: str,
        owner: str,
        max_active: int,
        now: float,
        expires_at: float,
    ) -> ProviderAllocation | None:
        store = self._store
        if not store._conn.execute(
            "SELECT 1 FROM sandboxes WHERE sandbox_id = ?", (sandbox_id,)
        ).fetchone():
            return None
        store._conn.execute(
            """
            DELETE FROM agentbox_provider_allocations
            WHERE provider_scope = ? AND state = 'reserved'
              AND expires_at <= ?
            """,
            (provider_scope, now),
        )
        active = store._conn.execute(
            """
            SELECT * FROM agentbox_provider_allocations
            WHERE provider_scope = ? AND sandbox_id = ? AND state = 'active'
            ORDER BY updated_at DESC LIMIT 1
            """,
            (provider_scope, sandbox_id),
        ).fetchone()
        if active is not None:
            return self._provider_allocation(active)
        row = store._conn.execute(
            """
            SELECT * FROM agentbox_provider_allocations
            WHERE provider_scope = ? AND sandbox_id = ? AND state = 'reserved'
            """,
            (provider_scope, sandbox_id),
        ).fetchone()
        if row is not None:
            if row["owner"] != owner and float(row["expires_at"]) > now:
                return None
            row = store._conn.execute(
                """
                UPDATE agentbox_provider_allocations
                SET owner = ?, expires_at = ?, updated_at = ?
                WHERE provider_scope = ? AND allocation_id = ?
                RETURNING *
                """,
                (owner, expires_at, now, provider_scope, row["allocation_id"]),
            ).fetchone()
            return self._provider_allocation(row)
        count = int(
            store._conn.execute(
                """
                SELECT count(*) FROM agentbox_provider_allocations
                WHERE provider_scope = ? AND (
                    state = 'active' OR (state = 'reserved' AND expires_at > ?)
                )
                """,
                (provider_scope, now),
            ).fetchone()[0]
        )
        if count >= max_active:
            return None
        allocation_id = str(uuid.uuid4())
        row = store._conn.execute(
            """
            INSERT INTO agentbox_provider_allocations (
                allocation_id, provider_scope, sandbox_id, owner, state,
                provider_id, expires_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'reserved', NULL, ?, ?, ?)
            RETURNING *
            """,
            (
                allocation_id,
                provider_scope,
                sandbox_id,
                owner,
                expires_at,
                now,
                now,
            ),
        ).fetchone()
        return self._provider_allocation(row)

    async def reserve_provider_allocation(
        self,
        provider_scope: str,
        sandbox_id: str,
        *,
        owner: str,
        max_active: int,
        ttl_seconds: float,
    ) -> ProviderAllocation | None:
        if max_active < 1 or ttl_seconds <= 0:
            raise ValueError("provider allocation limits must be positive")
        return await self._run(
            self._reserve_provider_allocation,
            provider_scope,
            sandbox_id,
            owner,
            max_active,
            ttl_seconds,
        )

    def _activate_provider_allocation(
        self,
        provider_scope: str,
        allocation_id: str,
        owner: str,
        provider_id: str,
    ) -> ProviderAllocation | None:
        store = self._store
        now = time.time()
        with store._lock, store._conn:
            row = store._conn.execute(
                """
                UPDATE agentbox_provider_allocations
                SET state = 'active', provider_id = ?, expires_at = NULL,
                    updated_at = ?
                WHERE provider_scope = ? AND allocation_id = ?
                  AND ((state = 'reserved' AND owner = ?)
                    OR (state = 'active' AND provider_id = ?))
                RETURNING *
                """,
                (
                    provider_id,
                    now,
                    provider_scope,
                    allocation_id,
                    owner,
                    provider_id,
                ),
            ).fetchone()
        return self._provider_allocation(row) if row else None

    async def activate_provider_allocation(
        self,
        provider_scope: str,
        allocation_id: str,
        *,
        owner: str,
        provider_id: str,
    ) -> ProviderAllocation | None:
        return await self._run(
            self._activate_provider_allocation,
            provider_scope,
            allocation_id,
            owner,
            provider_id,
        )

    def _hold_provider_allocation(
        self,
        provider_scope: str,
        allocation_id: str,
        owner: str,
    ) -> ProviderAllocation | None:
        store = self._store
        now = time.time()
        with store._lock, store._conn:
            row = store._conn.execute(
                """
                UPDATE agentbox_provider_allocations
                SET owner = ?, expires_at = ?, updated_at = ?
                WHERE provider_scope = ? AND allocation_id = ?
                  AND state = 'reserved'
                RETURNING *
                """,
                (
                    owner,
                    253402300799.0,
                    now,
                    provider_scope,
                    allocation_id,
                ),
            ).fetchone()
        return self._provider_allocation(row) if row else None

    async def hold_provider_allocation(
        self,
        provider_scope: str,
        allocation_id: str,
        *,
        owner: str,
    ) -> ProviderAllocation | None:
        return await self._run(
            self._hold_provider_allocation,
            provider_scope,
            allocation_id,
            owner,
        )

    def _release_provider_allocation(
        self, provider_scope: str, allocation_id: str
    ) -> bool:
        store = self._store
        with store._lock, store._conn:
            cursor = store._conn.execute(
                """
                DELETE FROM agentbox_provider_allocations
                WHERE provider_scope = ? AND allocation_id = ?
                """,
                (provider_scope, allocation_id),
            )
            return bool(cursor.rowcount)

    async def release_provider_allocation(
        self, provider_scope: str, allocation_id: str
    ) -> bool:
        return await self._run(
            self._release_provider_allocation, provider_scope, allocation_id
        )

    def _list_provider_allocations(
        self, provider_scope: str
    ) -> list[ProviderAllocation]:
        store = self._store
        with store._lock:
            rows = store._conn.execute(
                """
                SELECT * FROM agentbox_provider_allocations
                WHERE provider_scope = ? ORDER BY sandbox_id, allocation_id
                """,
                (provider_scope,),
            ).fetchall()
        return [self._provider_allocation(row) for row in rows]

    async def list_provider_allocations(
        self, provider_scope: str
    ) -> list[ProviderAllocation]:
        return await self._run(self._list_provider_allocations, provider_scope)

    def _reconcile_provider_allocations(
        self,
        provider_scope: str,
        active_provider_objects: dict[str, tuple[str, str | None]],
        inventory_started_at: float,
    ) -> None:
        store = self._store
        now = time.time()
        with store._lock:
            store._conn.execute("BEGIN IMMEDIATE")
            try:
                store._conn.execute(
                    """
                    DELETE FROM agentbox_provider_allocations
                    WHERE provider_scope = ? AND state = 'reserved'
                      AND expires_at <= ?
                    """,
                    (provider_scope, now),
                )
                for provider_id, (
                    sandbox_id,
                    generation_token,
                ) in active_provider_objects.items():
                    claimed = store._conn.execute(
                        """
                        SELECT 1 FROM agentbox_lifecycle_claims
                        WHERE sandbox_id = ? AND expires_at > ?
                        """,
                        (sandbox_id, now),
                    ).fetchone()
                    if claimed is not None:
                        # Lifecycle mutation owns this logical sandbox. Avoid
                        # publishing a competing discovered generation between
                        # its conflict check and exact provider operation.
                        continue
                    reservation = None
                    if generation_token:
                        reservation = store._conn.execute(
                            """
                            SELECT allocation_id
                            FROM agentbox_provider_allocations
                            WHERE provider_scope = ? AND allocation_id = ?
                              AND sandbox_id = ? AND state = 'reserved'
                            """,
                            (provider_scope, generation_token, sandbox_id),
                        ).fetchone()
                    if reservation is None:
                        reservation = store._conn.execute(
                            """
                            SELECT a.allocation_id
                            FROM agentbox_provider_allocations a
                            JOIN sandboxes s ON s.sandbox_id = a.sandbox_id
                            WHERE a.provider_scope = ? AND a.sandbox_id = ?
                              AND a.state = 'reserved' AND s.provider_id = ?
                            """,
                            (provider_scope, sandbox_id, provider_id),
                        ).fetchone()
                    row = store._conn.execute(
                        """
                        SELECT allocation_id FROM agentbox_provider_allocations
                        WHERE provider_scope = ? AND provider_id = ?
                        """,
                        (provider_scope, provider_id),
                    ).fetchone()
                    if reservation is not None:
                        store._conn.execute(
                            """
                            DELETE FROM agentbox_provider_allocations
                            WHERE provider_scope = ? AND provider_id = ?
                              AND allocation_id <> ?
                            """,
                            (
                                provider_scope,
                                provider_id,
                                reservation["allocation_id"],
                            ),
                        )
                        store._conn.execute(
                            """
                            UPDATE agentbox_provider_allocations
                            SET state = 'active', provider_id = ?,
                                expires_at = NULL, updated_at = ?
                            WHERE provider_scope = ? AND allocation_id = ?
                            """,
                            (
                                provider_id,
                                inventory_started_at,
                                provider_scope,
                                reservation["allocation_id"],
                            ),
                        )
                    elif row is None:
                        store._conn.execute(
                            """
                            INSERT INTO agentbox_provider_allocations (
                                allocation_id, provider_scope, sandbox_id, owner,
                                state, provider_id, expires_at, created_at, updated_at
                            ) VALUES (
                                ?, ?, ?, 'reconciler', 'active', ?, NULL, ?, ?
                            )
                            """,
                            (
                                f"provider:{provider_id}",
                                provider_scope,
                                sandbox_id,
                                provider_id,
                                inventory_started_at,
                                inventory_started_at,
                            ),
                        )
                    else:
                        store._conn.execute(
                            """
                            UPDATE agentbox_provider_allocations
                            SET sandbox_id = ?, state = 'active', expires_at = NULL,
                                updated_at = ?
                            WHERE provider_scope = ? AND allocation_id = ?
                            """,
                            (
                                sandbox_id,
                                inventory_started_at,
                                provider_scope,
                                row["allocation_id"],
                            ),
                        )
                # Never delete a durable active allocation merely because one
                # eventually consistent inventory snapshot omitted it. Exact
                # suspend/delete/purge paths release allocations explicitly.
                store._conn.commit()
            except BaseException:
                store._conn.rollback()
                raise

    async def reconcile_provider_allocations(
        self,
        provider_scope: str,
        active_provider_objects: dict[str, tuple[str, str | None]],
        *,
        inventory_started_at: float,
    ) -> None:
        await self._run(
            self._reconcile_provider_allocations,
            provider_scope,
            active_provider_objects,
            inventory_started_at,
        )

    def _reconcile_provider_inventory(
        self,
        provider_scope: str,
        provider_name: str,
        provider_objects: dict[str, tuple[str, str | None, bool]],
        inventory_started_at: float,
    ) -> None:
        store = self._store
        now = time.time()
        with store._lock:
            store._conn.execute("BEGIN IMMEDIATE")
            try:
                store._conn.execute(
                    """
                    DELETE FROM agentbox_provider_allocations
                    WHERE provider_scope = ? AND state = 'reserved'
                      AND expires_at <= ?
                    """,
                    (provider_scope, now),
                )
                sandbox_ids = sorted(
                    {sandbox_id for sandbox_id, _, _ in provider_objects.values()}
                )
                for sandbox_id in sandbox_ids:
                    claimed = store._conn.execute(
                        """
                        SELECT 1 FROM agentbox_lifecycle_claims
                        WHERE sandbox_id = ? AND expires_at > ?
                        """,
                        (sandbox_id, now),
                    ).fetchone()
                    if claimed is not None:
                        # The lifecycle owner publishes its own exact identity.
                        # Never publish a stale inventory snapshot behind it.
                        continue

                    items = [
                        (provider_id, generation_token, active)
                        for provider_id, (
                            item_sandbox_id,
                            generation_token,
                            active,
                        ) in provider_objects.items()
                        if item_sandbox_id == sandbox_id
                    ]
                    for provider_id, generation_token, active in items:
                        if not active:
                            continue
                        reservation = None
                        if generation_token:
                            reservation = store._conn.execute(
                                """
                                SELECT allocation_id
                                FROM agentbox_provider_allocations
                                WHERE provider_scope = ? AND allocation_id = ?
                                  AND sandbox_id = ? AND state = 'reserved'
                                """,
                                (provider_scope, generation_token, sandbox_id),
                            ).fetchone()
                        if reservation is None:
                            reservation = store._conn.execute(
                                """
                                SELECT a.allocation_id
                                FROM agentbox_provider_allocations a
                                JOIN sandboxes s ON s.sandbox_id = a.sandbox_id
                                WHERE a.provider_scope = ? AND a.sandbox_id = ?
                                  AND a.state = 'reserved' AND s.provider_id = ?
                                """,
                                (provider_scope, sandbox_id, provider_id),
                            ).fetchone()
                        existing = store._conn.execute(
                            """
                            SELECT allocation_id FROM agentbox_provider_allocations
                            WHERE provider_scope = ? AND provider_id = ?
                            """,
                            (provider_scope, provider_id),
                        ).fetchone()
                        if reservation is not None:
                            store._conn.execute(
                                """
                                DELETE FROM agentbox_provider_allocations
                                WHERE provider_scope = ? AND provider_id = ?
                                  AND allocation_id <> ?
                                """,
                                (
                                    provider_scope,
                                    provider_id,
                                    reservation["allocation_id"],
                                ),
                            )
                            store._conn.execute(
                                """
                                UPDATE agentbox_provider_allocations
                                SET state = 'active', provider_id = ?,
                                    expires_at = NULL, updated_at = ?
                                WHERE provider_scope = ? AND allocation_id = ?
                                """,
                                (
                                    provider_id,
                                    inventory_started_at,
                                    provider_scope,
                                    reservation["allocation_id"],
                                ),
                            )
                        elif existing is not None:
                            store._conn.execute(
                                """
                                UPDATE agentbox_provider_allocations
                                SET sandbox_id = ?, state = 'active',
                                    expires_at = NULL, updated_at = ?
                                WHERE provider_scope = ? AND allocation_id = ?
                                """,
                                (
                                    sandbox_id,
                                    inventory_started_at,
                                    provider_scope,
                                    existing["allocation_id"],
                                ),
                            )
                        else:
                            store._conn.execute(
                                """
                                INSERT INTO agentbox_provider_allocations (
                                    allocation_id, provider_scope, sandbox_id,
                                    owner, state, provider_id, expires_at,
                                    created_at, updated_at
                                ) VALUES (
                                    ?, ?, ?, 'reconciler', 'active', ?, NULL, ?, ?
                                )
                                """,
                                (
                                    f"provider:{provider_id}",
                                    provider_scope,
                                    sandbox_id,
                                    provider_id,
                                    inventory_started_at,
                                    inventory_started_at,
                                ),
                            )

                    record = store._conn.execute(
                        "SELECT provider_id FROM sandboxes WHERE sandbox_id = ?",
                        (sandbox_id,),
                    ).fetchone()
                    known_exact_ids = {
                        str(row["provider_id"])
                        for row in store._conn.execute(
                            """
                            SELECT provider_id
                            FROM agentbox_provider_allocations
                            WHERE provider_scope = ? AND sandbox_id = ?
                              AND provider_id IS NOT NULL
                              AND allocation_id NOT LIKE 'provider:%'
                            """,
                            (provider_scope, sandbox_id),
                        ).fetchall()
                    }
                    if record is not None and record["provider_id"] is not None:
                        known_exact_ids.add(str(record["provider_id"]))

                    for provider_id, _, _ in items:
                        if provider_id in known_exact_ids:
                            store._conn.execute(
                                """
                                DELETE FROM agentbox_orphan_candidates
                                WHERE provider_name = ? AND provider_id = ?
                                """,
                                (provider_name, provider_id),
                            )
                        else:
                            # A generation token identifies a create attempt,
                            # not every object that happens to carry it. Only a
                            # persisted exact provider ID authorizes clearing.
                            store._conn.execute(
                                """
                                INSERT INTO agentbox_orphan_candidates (
                                    provider_name, provider_id, sandbox_id,
                                    first_seen_at, last_seen_at
                                ) VALUES (?, ?, ?, ?, ?)
                                ON CONFLICT(provider_name, provider_id) DO UPDATE SET
                                    sandbox_id = COALESCE(
                                        excluded.sandbox_id, sandbox_id
                                    ),
                                    last_seen_at = excluded.last_seen_at
                                """,
                                (
                                    provider_name,
                                    provider_id,
                                    sandbox_id,
                                    inventory_started_at,
                                    inventory_started_at,
                                ),
                            )
                store._conn.commit()
            except BaseException:
                store._conn.rollback()
                raise

    async def reconcile_provider_inventory(
        self,
        provider_scope: str,
        provider_name: str,
        provider_objects: dict[str, tuple[str, str | None, bool]],
        *,
        inventory_started_at: float,
    ) -> None:
        await self._run(
            self._reconcile_provider_inventory,
            provider_scope,
            provider_name,
            provider_objects,
            inventory_started_at,
        )

    async def close(self) -> None:
        if self._legacy is not None:
            store, self._legacy = self._legacy, None
            await asyncio.to_thread(store.close)

    @staticmethod
    def _activity_lease(row: sqlite3.Row) -> ActivityLease:
        return ActivityLease(
            lease_id=str(row["lease_id"]),
            sandbox_id=str(row["sandbox_id"]),
            session_id=str(row["session_id"]) if row["session_id"] else None,
            operation=str(row["operation"]),
            owner=str(row["owner"]),
            expires_at=float(row["expires_at"]),
        )

    @staticmethod
    def _lifecycle_claim(row: sqlite3.Row) -> LifecycleClaim:
        return LifecycleClaim(
            claim_id=str(row["claim_id"]),
            sandbox_id=str(row["sandbox_id"]),
            operation=str(row["operation"]),
            owner=str(row["owner"]),
            expires_at=float(row["expires_at"]),
        )

    @staticmethod
    def _orphan(row: sqlite3.Row) -> OrphanCandidate:
        return OrphanCandidate(
            provider_name=str(row["provider_name"]),
            provider_id=str(row["provider_id"]),
            sandbox_id=str(row["sandbox_id"]) if row["sandbox_id"] else None,
            first_seen_at=float(row["first_seen_at"]),
            last_seen_at=float(row["last_seen_at"]),
        )

    @staticmethod
    def _provider_allocation(row: sqlite3.Row) -> ProviderAllocation:
        return ProviderAllocation(
            allocation_id=str(row["allocation_id"]),
            provider_scope=str(row["provider_scope"]),
            sandbox_id=str(row["sandbox_id"]),
            owner=str(row["owner"]),
            state=str(row["state"]),
            provider_id=str(row["provider_id"]) if row["provider_id"] else None,
            expires_at=float(row["expires_at"])
            if row["expires_at"] is not None
            else None,
            updated_at=float(row["updated_at"]),
        )
