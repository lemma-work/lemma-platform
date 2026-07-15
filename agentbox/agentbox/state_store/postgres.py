from __future__ import annotations

import time
import uuid
from typing import Any

from agentbox.schemas import SandboxEnsureRequest

from .migrations import POSTGRES_MIGRATIONS
from .models import (
    ActivityLease,
    DesiredSandboxState,
    LifecycleClaim,
    OrphanCandidate,
    ProviderAllocation,
    SandboxRecord,
    SessionRecord,
)


_LEGACY_OPERATION_STALE_SECONDS = 2 * 60 * 60


class PostgresStateStore:
    """Async PostgreSQL store compatible with lemma-app's private schema."""

    def __init__(
        self,
        database_url: str,
        *,
        durable_env_keys: frozenset[str] = frozenset({"LEMMA_BASE_URL"}),
    ) -> None:
        del database_url
        self.durable_env_keys = durable_env_keys
        self._pool: Any = None
        self._jsonb: Any = None

    @classmethod
    async def open(
        cls,
        database_url: str,
        *,
        durable_env_keys: frozenset[str] = frozenset({"LEMMA_BASE_URL"}),
    ) -> PostgresStateStore:
        self = cls(database_url, durable_env_keys=durable_env_keys)
        try:
            from psycopg.rows import dict_row
            from psycopg.types.json import Jsonb
            from psycopg_pool import AsyncConnectionPool
        except ImportError as exc:
            raise RuntimeError(
                "PostgreSQL AgentBox state requires the 'agentbox[postgres]' extra"
            ) from exc
        self._jsonb = Jsonb
        try:
            self._pool = AsyncConnectionPool(
                database_url,
                min_size=1,
                max_size=5,
                open=False,
                kwargs={"row_factory": dict_row},
            )
            await self._pool.open(wait=True)
            await self._apply_migrations()
        except Exception:
            if self._pool is not None:
                await self._pool.close()
            raise RuntimeError(
                "Failed to initialize PostgreSQL AgentBox state store"
            ) from None
        return self

    async def _apply_migrations(self) -> None:
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext('agentbox-schema-migrations'))"
                )
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS agentbox_schema_migrations (
                        version integer PRIMARY KEY,
                        name text NOT NULL,
                        applied_at timestamptz NOT NULL DEFAULT now()
                    )
                    """
                )
                rows = await (
                    await conn.execute("SELECT version FROM agentbox_schema_migrations")
                ).fetchall()
                applied = {int(row["version"]) for row in rows}
                for version, name, statements in POSTGRES_MIGRATIONS:
                    if version in applied:
                        continue
                    for statement in statements:
                        await conn.execute(statement)
                    await conn.execute(
                        """
                        INSERT INTO agentbox_schema_migrations (version, name)
                        VALUES (%s, %s)
                        """,
                        (version, name),
                    )

                rows = await (
                    await conn.execute("SELECT sandbox_id, env FROM agentbox_sandboxes")
                ).fetchall()
                for row in rows:
                    env = dict(row["env"] or {})
                    sanitized = {
                        key: str(value)
                        for key, value in env.items()
                        if key in self.durable_env_keys
                    }
                    if sanitized != env:
                        await conn.execute(
                            "UPDATE agentbox_sandboxes SET env = %s WHERE sandbox_id = %s",
                            (self._jsonb(sanitized), row["sandbox_id"]),
                        )

    @staticmethod
    def _sandbox(row: Any) -> SandboxRecord:
        return SandboxRecord(
            sandbox_id=str(row["sandbox_id"]),
            env=dict(row["env"] or {}),
            desired_state=str(row.get("desired_state") or "present"),
            desired_generation=int(row.get("desired_generation") or 1),
            observed_generation=int(row.get("observed_generation") or 0),
            provider_name=row.get("provider_name"),
            provider_id=row.get("provider_id"),
            instance_id=row.get("instance_id"),
            idle_since_at=(
                float(row["idle_since_at"])
                if row.get("idle_since_at") is not None
                else None
            ),
            last_active_at=(
                float(row["last_active_at"])
                if row.get("last_active_at") is not None
                else None
            ),
            last_observed_at=(
                float(row["last_observed_at"])
                if row.get("last_observed_at") is not None
                else None
            ),
        )

    @staticmethod
    def _session(row: Any) -> SessionRecord:
        return SessionRecord(
            sandbox_id=str(row["sandbox_id"]),
            session_id=str(row["session_id"]),
            cwd=str(row["cwd"]),
            env_keys=list(row["env_keys"] or []),
            last_active_at=float(row["last_active_at"]),
            active_operations=int(row["active_operations"]),
        )

    @staticmethod
    def _activity_lease(row: Any) -> ActivityLease:
        return ActivityLease(
            lease_id=str(row["lease_id"]),
            sandbox_id=str(row["sandbox_id"]),
            session_id=str(row["session_id"]) if row["session_id"] else None,
            operation=str(row["operation"]),
            owner=str(row["owner"]),
            expires_at=float(row["expires_at"]),
        )

    @staticmethod
    def _lifecycle_claim(row: Any) -> LifecycleClaim:
        return LifecycleClaim(
            claim_id=str(row["claim_id"]),
            sandbox_id=str(row["sandbox_id"]),
            operation=str(row["operation"]),
            owner=str(row["owner"]),
            expires_at=float(row["expires_at"]),
        )

    @staticmethod
    def _orphan(row: Any) -> OrphanCandidate:
        return OrphanCandidate(
            provider_name=str(row["provider_name"]),
            provider_id=str(row["provider_id"]),
            sandbox_id=str(row["sandbox_id"]) if row["sandbox_id"] else None,
            first_seen_at=float(row["first_seen_at"]),
            last_seen_at=float(row["last_seen_at"]),
        )

    @staticmethod
    def _sandbox_columns() -> str:
        return """
            sandbox_id, env, desired_state, desired_generation,
            observed_generation, provider_name, provider_id, instance_id,
            extract(epoch FROM idle_since_at) AS idle_since_at,
            extract(epoch FROM last_active_at) AS last_active_at,
            extract(epoch FROM last_observed_at) AS last_observed_at
        """

    async def upsert_sandbox(
        self, sandbox_id: str, request: SandboxEnsureRequest
    ) -> SandboxRecord:
        env = {
            key: value
            for key, value in request.env.items()
            if key in self.durable_env_keys
        }
        async with self._pool.connection() as conn:
            row = await (
                await conn.execute(
                    f"""
                    INSERT INTO agentbox_sandboxes (
                        sandbox_id, env, idle_since_at, last_active_at,
                        desired_state, desired_generation
                    ) VALUES (%s, %s, NULL, now(), 'present', 1)
                    ON CONFLICT (sandbox_id) DO UPDATE SET
                        env = EXCLUDED.env,
                        idle_since_at = NULL,
                        last_active_at = now(),
                        desired_state = 'present',
                        desired_generation = CASE
                            WHEN agentbox_sandboxes.env IS DISTINCT FROM EXCLUDED.env
                              OR agentbox_sandboxes.desired_state <> 'present'
                            THEN agentbox_sandboxes.desired_generation + 1
                            ELSE agentbox_sandboxes.desired_generation
                        END,
                        updated_at = now()
                    RETURNING {self._sandbox_columns()}
                    """,
                    (sandbox_id, self._jsonb(env)),
                )
            ).fetchone()
        return self._sandbox(row)

    async def insert_sandbox_if_missing(self, sandbox_id: str) -> SandboxRecord:
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO agentbox_sandboxes (
                        sandbox_id, env, idle_since_at, last_active_at,
                        desired_state, desired_generation
                    ) VALUES (%s, '{}'::jsonb, NULL, now(), 'present', 1)
                    ON CONFLICT (sandbox_id) DO NOTHING
                    """,
                    (sandbox_id,),
                )
                row = await (
                    await conn.execute(
                        f"""
                        SELECT {self._sandbox_columns()} FROM agentbox_sandboxes
                        WHERE sandbox_id = %s
                        """,
                        (sandbox_id,),
                    )
                ).fetchone()
        if row is None:
            raise RuntimeError("failed to insert sandbox defaults")
        return self._sandbox(row)

    async def insert_sandbox_tombstone_if_missing(
        self, sandbox_id: str
    ) -> SandboxRecord:
        """Create a deletion fence without changing an existing sandbox row."""

        async with self._pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO agentbox_sandboxes (
                        sandbox_id, env, idle_since_at, last_active_at,
                        desired_state, desired_generation
                    ) VALUES (%s, '{}'::jsonb, NULL, now(), 'deleted', 1)
                    ON CONFLICT (sandbox_id) DO NOTHING
                    """,
                    (sandbox_id,),
                )
                row = await (
                    await conn.execute(
                        f"""
                        SELECT {self._sandbox_columns()} FROM agentbox_sandboxes
                        WHERE sandbox_id = %s
                        """,
                        (sandbox_id,),
                    )
                ).fetchone()
        if row is None:
            raise RuntimeError("failed to insert sandbox tombstone")
        return self._sandbox(row)

    async def ensure_sandbox_defaults(self, sandbox_id: str) -> SandboxRecord:
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO agentbox_sandboxes (
                        sandbox_id, env, idle_since_at, last_active_at,
                        desired_state, desired_generation
                    ) VALUES (%s, '{}'::jsonb, NULL, now(), 'present', 1)
                    ON CONFLICT (sandbox_id) DO UPDATE SET
                        idle_since_at = NULL,
                        last_active_at = now(),
                        desired_state = 'present',
                        desired_generation = CASE
                            WHEN agentbox_sandboxes.desired_state <> 'present'
                            THEN agentbox_sandboxes.desired_generation + 1
                            ELSE agentbox_sandboxes.desired_generation
                        END,
                        updated_at = now()
                    """,
                    (sandbox_id,),
                )
                row = await (
                    await conn.execute(
                        f"""
                        SELECT {self._sandbox_columns()} FROM agentbox_sandboxes
                        WHERE sandbox_id = %s
                        """,
                        (sandbox_id,),
                    )
                ).fetchone()
        if row is None:
            raise RuntimeError("failed to ensure sandbox defaults")
        return self._sandbox(row)

    async def get_sandbox(self, sandbox_id: str) -> SandboxRecord | None:
        async with self._pool.connection() as conn:
            row = await (
                await conn.execute(
                    f"""
                    SELECT {self._sandbox_columns()} FROM agentbox_sandboxes
                    WHERE sandbox_id = %s
                    """,
                    (sandbox_id,),
                )
            ).fetchone()
        return self._sandbox(row) if row else None

    async def list_sandboxes(self) -> list[SandboxRecord]:
        async with self._pool.connection() as conn:
            rows = await (
                await conn.execute(
                    f"""
                    SELECT {self._sandbox_columns()} FROM agentbox_sandboxes
                    ORDER BY sandbox_id
                    """
                )
            ).fetchall()
        return [self._sandbox(row) for row in rows]

    async def delete_sandbox(self, sandbox_id: str) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "DELETE FROM agentbox_sandboxes WHERE sandbox_id = %s", (sandbox_id,)
            )

    async def set_sandbox_desired_state(
        self, sandbox_id: str, desired_state: DesiredSandboxState
    ) -> SandboxRecord | None:
        if desired_state not in {"present", "suspended", "deleted"}:
            raise ValueError(
                "desired_state must be 'present', 'suspended', or 'deleted'"
            )
        async with self._pool.connection() as conn:
            row = await (
                await conn.execute(
                    f"""
                    UPDATE agentbox_sandboxes SET desired_state = %s,
                        desired_generation = CASE
                            WHEN desired_state <> %s THEN desired_generation + 1
                            ELSE desired_generation
                        END,
                        updated_at = now()
                    WHERE sandbox_id = %s
                    RETURNING {self._sandbox_columns()}
                    """,
                    (desired_state, desired_state, sandbox_id),
                )
            ).fetchone()
        return self._sandbox(row) if row else None

    async def set_sandbox_observation(
        self,
        sandbox_id: str,
        *,
        provider_name: str,
        provider_id: str,
        instance_id: str | None,
        observed_generation: int,
    ) -> SandboxRecord | None:
        async with self._pool.connection() as conn:
            row = await (
                await conn.execute(
                    f"""
                    UPDATE agentbox_sandboxes SET provider_name = %s,
                        provider_id = %s, instance_id = %s,
                        observed_generation = %s, last_observed_at = now(),
                        updated_at = now()
                    WHERE sandbox_id = %s AND desired_generation = %s
                    RETURNING {self._sandbox_columns()}
                    """,
                    (
                        provider_name,
                        provider_id,
                        instance_id,
                        observed_generation,
                        sandbox_id,
                        observed_generation,
                    ),
                )
            ).fetchone()
        return self._sandbox(row) if row else None

    async def set_sandbox_provider_identity(
        self,
        sandbox_id: str,
        *,
        provider_name: str,
        provider_id: str,
        instance_id: str | None,
        desired_generation: int,
    ) -> SandboxRecord | None:
        async with self._pool.connection() as conn:
            row = await (
                await conn.execute(
                    f"""
                    UPDATE agentbox_sandboxes SET provider_name = %s,
                        provider_id = %s, instance_id = %s,
                        last_observed_at = now(), updated_at = now()
                    WHERE sandbox_id = %s AND desired_state = 'present'
                      AND desired_generation = %s
                      AND (provider_id IS NULL OR provider_id = %s)
                    RETURNING {self._sandbox_columns()}
                    """,
                    (
                        provider_name,
                        provider_id,
                        instance_id,
                        sandbox_id,
                        desired_generation,
                        provider_id,
                    ),
                )
            ).fetchone()
        return self._sandbox(row) if row else None

    async def clear_sandbox_provider_identity(
        self,
        sandbox_id: str,
        *,
        provider_id: str,
        desired_generation: int,
    ) -> SandboxRecord | None:
        async with self._pool.connection() as conn:
            row = await (
                await conn.execute(
                    f"""
                    UPDATE agentbox_sandboxes SET provider_name = NULL,
                        provider_id = NULL, instance_id = NULL,
                        observed_generation = 0, last_observed_at = NULL,
                        updated_at = now()
                    WHERE sandbox_id = %s AND provider_id = %s
                      AND desired_generation = %s
                    RETURNING {self._sandbox_columns()}
                    """,
                    (sandbox_id, provider_id, desired_generation),
                )
            ).fetchone()
        return self._sandbox(row) if row else None

    async def upsert_session(
        self,
        sandbox_id: str,
        session_id: str,
        *,
        cwd: str,
        env_keys: list[str],
    ) -> SessionRecord:
        async with self._pool.connection() as conn:
            row = await (
                await conn.execute(
                    """
                    INSERT INTO agentbox_sessions (
                        sandbox_id, session_id, cwd, env_keys, last_active_at
                    ) VALUES (%s, %s, %s, %s, now())
                    ON CONFLICT (sandbox_id, session_id) DO UPDATE SET
                        cwd = EXCLUDED.cwd, env_keys = EXCLUDED.env_keys,
                        last_active_at = now(), updated_at = now()
                    RETURNING sandbox_id, session_id, cwd, env_keys,
                        extract(epoch FROM last_active_at) AS last_active_at,
                        active_operations
                    """,
                    (sandbox_id, session_id, cwd, self._jsonb(sorted(env_keys))),
                )
            ).fetchone()
            await conn.execute(
                """
                UPDATE agentbox_sandboxes SET idle_since_at = NULL,
                    last_active_at = now(), updated_at = now()
                WHERE sandbox_id = %s
                """,
                (sandbox_id,),
            )
        return self._session(row)

    async def touch_session(
        self, sandbox_id: str, session_id: str, *, owner: str | None = None
    ) -> bool:
        # Do not bind a bare NULL solely to ``%s IS NULL``: PostgreSQL cannot
        # infer its type.  Omitting the owner predicate also expresses the
        # intended fence directly -- an anonymous caller is blocked by any
        # live lifecycle claim, while an owner may pass its own claim.
        owner_fence = "" if owner is None else "AND c.owner <> %s"
        owner_params: tuple[str, ...] = () if owner is None else (owner,)
        async with self._pool.connection() as conn:
            row = await (
                await conn.execute(
                    f"""
                    UPDATE agentbox_sessions SET last_active_at = now(), updated_at = now()
                    WHERE sandbox_id = %s AND session_id = %s
                      AND NOT EXISTS (
                        SELECT 1 FROM agentbox_lifecycle_claims c
                        WHERE c.sandbox_id = agentbox_sessions.sandbox_id
                          AND c.expires_at > now()
                          {owner_fence}
                      )
                    RETURNING 1
                    """,
                    (sandbox_id, session_id, *owner_params),
                )
            ).fetchone()
            if row:
                await conn.execute(
                    """
                    UPDATE agentbox_sandboxes SET idle_since_at = NULL,
                        last_active_at = now(), updated_at = now()
                    WHERE sandbox_id = %s
                    """,
                    (sandbox_id,),
                )
        return bool(row)

    async def get_session(
        self, sandbox_id: str, session_id: str
    ) -> SessionRecord | None:
        async with self._pool.connection() as conn:
            row = await (
                await conn.execute(
                    """
                    SELECT sandbox_id, session_id, cwd, env_keys,
                        extract(epoch FROM last_active_at) AS last_active_at,
                        active_operations
                    FROM agentbox_sessions WHERE sandbox_id = %s AND session_id = %s
                    """,
                    (sandbox_id, session_id),
                )
            ).fetchone()
        return self._session(row) if row else None

    async def delete_session(self, sandbox_id: str, session_id: str) -> bool:
        async with self._pool.connection() as conn:
            await conn.execute(
                """
                DELETE FROM agentbox_activity_leases
                WHERE sandbox_id = %s AND session_id = %s
                """,
                (sandbox_id, session_id),
            )
            row = await (
                await conn.execute(
                    """
                    DELETE FROM agentbox_sessions
                    WHERE sandbox_id = %s AND session_id = %s RETURNING 1
                    """,
                    (sandbox_id, session_id),
                )
            ).fetchone()
            await conn.execute(
                """
                UPDATE agentbox_sandboxes s
                SET idle_since_at = coalesce(idle_since_at, now()), updated_at = now()
                WHERE sandbox_id = %s AND desired_state = 'present'
                  AND NOT EXISTS (
                    SELECT 1 FROM agentbox_sessions x WHERE x.sandbox_id = s.sandbox_id
                ) AND NOT EXISTS (
                    SELECT 1 FROM agentbox_activity_leases l
                    WHERE l.sandbox_id = s.sandbox_id AND l.expires_at > now()
                )
                """,
                (sandbox_id,),
            )
        return bool(row)

    async def delete_sandbox_sessions(self, sandbox_id: str) -> int:
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM agentbox_activity_leases WHERE sandbox_id = %s",
                    (sandbox_id,),
                )
                cursor = await conn.execute(
                    "DELETE FROM agentbox_sessions WHERE sandbox_id = %s",
                    (sandbox_id,),
                )
                await conn.execute(
                    """
                    UPDATE agentbox_sandboxes
                    SET idle_since_at = coalesce(idle_since_at, now()),
                        updated_at = now()
                    WHERE sandbox_id = %s AND desired_state = 'present'
                    """,
                    (sandbox_id,),
                )
        return int(cursor.rowcount or 0)

    async def expired_sessions(self, idle_timeout_seconds: int) -> list[SessionRecord]:
        async with self._pool.connection() as conn:
            await conn.execute(
                "DELETE FROM agentbox_activity_leases WHERE expires_at <= now()"
            )
            await conn.execute(
                """
                UPDATE agentbox_sessions x
                SET active_operations = 0, updated_at = now()
                WHERE active_operations > 0
                  AND updated_at < now() - make_interval(secs => %s)
                  AND NOT EXISTS (
                    SELECT 1 FROM agentbox_activity_leases l
                    WHERE l.sandbox_id = x.sandbox_id
                      AND (l.session_id IS NULL OR l.session_id = x.session_id)
                      AND l.expires_at > now()
                  )
                """,
                (_LEGACY_OPERATION_STALE_SECONDS,),
            )
            rows = await (
                await conn.execute(
                    """
                    SELECT s.sandbox_id, s.session_id, s.cwd, s.env_keys,
                        extract(epoch FROM s.last_active_at) AS last_active_at,
                        s.active_operations
                    FROM agentbox_sessions s
                    WHERE s.last_active_at < now() - make_interval(secs => %s)
                      AND s.active_operations = 0
                      AND NOT EXISTS (
                        SELECT 1 FROM agentbox_activity_leases l
                        WHERE l.sandbox_id = s.sandbox_id
                          AND (l.session_id IS NULL OR l.session_id = s.session_id)
                          AND l.expires_at > now()
                      )
                    ORDER BY s.last_active_at
                    """,
                    (idle_timeout_seconds,),
                )
            ).fetchall()
        return [self._session(row) for row in rows]

    async def idle_sandboxes(self, idle_timeout_seconds: int) -> list[SandboxRecord]:
        async with self._pool.connection() as conn:
            await conn.execute(
                "DELETE FROM agentbox_activity_leases WHERE expires_at <= now()"
            )
            await conn.execute(
                """
                UPDATE agentbox_sandboxes s
                SET idle_since_at = coalesce(last_active_at, now())
                WHERE idle_since_at IS NULL
                  AND desired_state = 'present'
                  AND NOT EXISTS (
                    SELECT 1 FROM agentbox_sessions x WHERE x.sandbox_id = s.sandbox_id
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM agentbox_activity_leases l
                    WHERE l.sandbox_id = s.sandbox_id AND l.expires_at > now()
                  )
                """
            )
            rows = await (
                await conn.execute(
                    f"""
                    SELECT {self._sandbox_columns()} FROM agentbox_sandboxes s
                    WHERE desired_state = 'present'
                      AND idle_since_at < now() - make_interval(secs => %s)
                      AND NOT EXISTS (
                        SELECT 1 FROM agentbox_activity_leases l
                        WHERE l.sandbox_id = s.sandbox_id AND l.expires_at > now()
                      )
                    ORDER BY idle_since_at
                    """,
                    (idle_timeout_seconds,),
                )
            ).fetchall()
        return [self._sandbox(row) for row in rows]

    async def mark_sandbox_active(
        self, sandbox_id: str, *, owner: str | None = None
    ) -> bool:
        owner_fence = "" if owner is None else "AND c.owner <> %s"
        owner_params: tuple[str, ...] = () if owner is None else (owner,)
        async with self._pool.connection() as conn:
            row = await (
                await conn.execute(
                    f"""
                    UPDATE agentbox_sandboxes SET idle_since_at = NULL,
                        last_active_at = now(), updated_at = now()
                    WHERE sandbox_id = %s AND desired_state = 'present'
                      AND NOT EXISTS (
                        SELECT 1 FROM agentbox_lifecycle_claims c
                        WHERE c.sandbox_id = agentbox_sandboxes.sandbox_id
                          AND c.expires_at > now()
                          {owner_fence}
                      )
                    RETURNING 1
                    """,
                    (sandbox_id, *owner_params),
                )
            ).fetchone()
        return bool(row)

    async def mark_pod_stopped(self, sandbox_id: str) -> SandboxRecord | None:
        async with self._pool.connection() as conn:
            row = await (
                await conn.execute(
                    f"""
                    UPDATE agentbox_sandboxes SET idle_since_at = now(),
                        desired_state = 'suspended',
                        desired_generation = CASE
                            WHEN desired_state <> 'suspended'
                            THEN desired_generation + 1
                            ELSE desired_generation
                        END,
                        updated_at = now()
                    WHERE sandbox_id = %s
                    RETURNING {self._sandbox_columns()}
                    """,
                    (sandbox_id,),
                )
            ).fetchone()
        return self._sandbox(row) if row else None

    async def mark_idle_if_empty(self, sandbox_id: str) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                """
                UPDATE agentbox_sandboxes s
                SET idle_since_at = coalesce(idle_since_at, now()), updated_at = now()
                WHERE sandbox_id = %s AND NOT EXISTS (
                    SELECT 1 FROM agentbox_sessions x WHERE x.sandbox_id = s.sandbox_id
                ) AND NOT EXISTS (
                    SELECT 1 FROM agentbox_activity_leases l
                    WHERE l.sandbox_id = s.sandbox_id AND l.expires_at > now()
                )
                  AND desired_state = 'present'
                """,
                (sandbox_id,),
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
        lease_id = uuid.uuid4()
        # As above, branch instead of sending an untyped NULL to ``%s IS
        # NULL``.  A sandbox-wide lease needs no session lookup; a
        # session-scoped lease must reference an existing session.
        session_fence = (
            ""
            if session_id is None
            else """
                        AND EXISTS (
                            SELECT 1 FROM agentbox_sessions x
                            WHERE x.sandbox_id = s.sandbox_id
                              AND x.session_id = %s
                        )
            """
        )
        session_params: tuple[str, ...] = () if session_id is None else (session_id,)
        async with self._pool.connection() as conn:
            row = await (
                await conn.execute(
                    f"""
                    INSERT INTO agentbox_activity_leases (
                        lease_id, sandbox_id, session_id, operation, owner, expires_at
                    )
                    SELECT %s, s.sandbox_id, %s, %s, %s,
                        now() + make_interval(secs => %s)
                    FROM agentbox_sandboxes s
                    WHERE s.sandbox_id = %s AND s.desired_state = 'present'
                      {session_fence}
                      AND NOT EXISTS (
                        SELECT 1 FROM agentbox_lifecycle_claims c
                        WHERE c.sandbox_id = s.sandbox_id AND c.expires_at > now()
                          AND c.owner <> %s
                      )
                    RETURNING lease_id, sandbox_id, session_id, operation, owner,
                        extract(epoch FROM expires_at) AS expires_at
                    """,
                    (
                        lease_id,
                        session_id,
                        operation,
                        owner,
                        ttl_seconds,
                        sandbox_id,
                        *session_params,
                        owner,
                    ),
                )
            ).fetchone()
            if row:
                await conn.execute(
                    """
                    UPDATE agentbox_sandboxes SET idle_since_at = NULL,
                        last_active_at = now(), updated_at = now()
                    WHERE sandbox_id = %s
                    """,
                    (sandbox_id,),
                )
        return self._activity_lease(row) if row else None

    async def renew_activity_lease(
        self, lease_id: str, *, owner: str, ttl_seconds: float
    ) -> ActivityLease | None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        async with self._pool.connection() as conn:
            row = await (
                await conn.execute(
                    """
                    UPDATE agentbox_activity_leases
                    SET expires_at = now() + make_interval(secs => %s), updated_at = now()
                    WHERE lease_id = %s AND owner = %s AND expires_at > now()
                    RETURNING lease_id, sandbox_id, session_id, operation, owner,
                        extract(epoch FROM expires_at) AS expires_at
                    """,
                    (ttl_seconds, lease_id, owner),
                )
            ).fetchone()
        return self._activity_lease(row) if row else None

    async def release_activity_lease(self, lease_id: str, *, owner: str) -> bool:
        async with self._pool.connection() as conn:
            async with conn.transaction():
                row = await (
                    await conn.execute(
                        """
                        DELETE FROM agentbox_activity_leases
                        WHERE lease_id = %s AND owner = %s
                        RETURNING sandbox_id, session_id
                        """,
                        (lease_id, owner),
                    )
                ).fetchone()
                if row:
                    sandbox_id = str(row["sandbox_id"])
                    session_id = row["session_id"]
                    if session_id is not None:
                        await conn.execute(
                            """
                            UPDATE agentbox_sessions
                            SET last_active_at = now(), updated_at = now()
                            WHERE sandbox_id = %s AND session_id = %s
                            """,
                            (sandbox_id, session_id),
                        )
                    await conn.execute(
                        """
                        UPDATE agentbox_sandboxes s
                        SET last_active_at = now(), updated_at = now(),
                            idle_since_at = CASE
                                WHEN NOT EXISTS (
                                    SELECT 1 FROM agentbox_sessions x
                                    WHERE x.sandbox_id = s.sandbox_id
                                ) AND NOT EXISTS (
                                    SELECT 1 FROM agentbox_activity_leases l
                                    WHERE l.sandbox_id = s.sandbox_id
                                      AND l.expires_at > now()
                                ) THEN now()
                                ELSE NULL
                            END
                        WHERE sandbox_id = %s
                        """,
                        (sandbox_id,),
                    )
        return bool(row)

    async def prune_expired_activity_leases(self) -> int:
        async with self._pool.connection() as conn:
            cursor = await conn.execute(
                "DELETE FROM agentbox_activity_leases WHERE expires_at <= now()"
            )
        return int(cursor.rowcount or 0)

    async def has_active_activity_lease(
        self,
        sandbox_id: str,
        *,
        session_id: str | None = None,
    ) -> bool:
        session_filter = "" if session_id is None else "AND session_id = %s"
        params = (sandbox_id,) if session_id is None else (sandbox_id, session_id)
        async with self._pool.connection() as conn:
            row = await (
                await conn.execute(
                    f"""
                    SELECT 1 FROM agentbox_activity_leases
                    WHERE sandbox_id = %s AND expires_at > now()
                      {session_filter}
                    LIMIT 1
                    """,
                    params,
                )
            ).fetchone()
        return bool(row)

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
        claim_id = uuid.uuid4()
        async with self._pool.connection() as conn:
            async with conn.transaction():
                sandbox = await (
                    await conn.execute(
                        """
                        SELECT 1 FROM agentbox_sandboxes
                        WHERE sandbox_id = %s FOR UPDATE
                        """,
                        (sandbox_id,),
                    )
                ).fetchone()
                if not sandbox:
                    return None
                # Share this transaction-scoped per-sandbox fence with
                # allocation reconciliation. If reconciliation started first,
                # it publishes any discovered provider generation before the
                # lifecycle operation snapshots allocations/inventory. If the
                # lifecycle operation started first, reconciliation observes
                # the live claim and skips this sandbox.
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    (f"agentbox:lifecycle:{sandbox_id}",),
                )
                await conn.execute(
                    """
                    DELETE FROM agentbox_lifecycle_claims
                    WHERE sandbox_id = %s AND expires_at <= now()
                    """,
                    (sandbox_id,),
                )
                row = await (
                    await conn.execute(
                        """
                        INSERT INTO agentbox_lifecycle_claims (
                            sandbox_id, claim_id, operation, owner, expires_at
                        ) VALUES (
                            %s, %s, %s, %s, now() + make_interval(secs => %s)
                        ) ON CONFLICT (sandbox_id) DO NOTHING
                        RETURNING claim_id, sandbox_id, operation, owner,
                            extract(epoch FROM expires_at) AS expires_at
                        """,
                        (sandbox_id, claim_id, operation, owner, ttl_seconds),
                    )
                ).fetchone()
        return self._lifecycle_claim(row) if row else None

    async def renew_lifecycle_claim(
        self, claim_id: str, *, owner: str, ttl_seconds: float
    ) -> LifecycleClaim | None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        async with self._pool.connection() as conn:
            row = await (
                await conn.execute(
                    """
                    UPDATE agentbox_lifecycle_claims
                    SET expires_at = now() + make_interval(secs => %s), updated_at = now()
                    WHERE claim_id = %s AND owner = %s AND expires_at > now()
                    RETURNING claim_id, sandbox_id, operation, owner,
                        extract(epoch FROM expires_at) AS expires_at
                    """,
                    (ttl_seconds, claim_id, owner),
                )
            ).fetchone()
        return self._lifecycle_claim(row) if row else None

    async def release_lifecycle_claim(self, claim_id: str, *, owner: str) -> bool:
        async with self._pool.connection() as conn:
            row = await (
                await conn.execute(
                    """
                    DELETE FROM agentbox_lifecycle_claims
                    WHERE claim_id = %s AND owner = %s RETURNING 1
                    """,
                    (claim_id, owner),
                )
            ).fetchone()
        return bool(row)

    async def observe_orphan(
        self,
        provider_name: str,
        provider_id: str,
        *,
        sandbox_id: str | None,
        observed_at: float | None = None,
    ) -> OrphanCandidate:
        observed_at = observed_at if observed_at is not None else time.time()
        async with self._pool.connection() as conn:
            row = await (
                await conn.execute(
                    """
                    INSERT INTO agentbox_orphan_candidates (
                        provider_name, provider_id, sandbox_id,
                        first_seen_at, last_seen_at
                    ) VALUES (
                        %s, %s, %s, to_timestamp(%s), to_timestamp(%s)
                    ) ON CONFLICT (provider_name, provider_id) DO UPDATE SET
                        sandbox_id = coalesce(EXCLUDED.sandbox_id,
                            agentbox_orphan_candidates.sandbox_id),
                        last_seen_at = EXCLUDED.last_seen_at
                    RETURNING provider_name, provider_id, sandbox_id,
                        extract(epoch FROM first_seen_at) AS first_seen_at,
                        extract(epoch FROM last_seen_at) AS last_seen_at
                    """,
                    (
                        provider_name,
                        provider_id,
                        sandbox_id,
                        observed_at,
                        observed_at,
                    ),
                )
            ).fetchone()
        return self._orphan(row)

    async def expired_orphans(
        self,
        grace_seconds: float,
        *,
        inventory_started_at: float,
    ) -> list[OrphanCandidate]:
        if grace_seconds < 0:
            raise ValueError("grace_seconds cannot be negative")
        async with self._pool.connection() as conn:
            rows = await (
                await conn.execute(
                    """
                    SELECT o.provider_name, o.provider_id, o.sandbox_id,
                        extract(epoch FROM o.first_seen_at) AS first_seen_at,
                        extract(epoch FROM o.last_seen_at) AS last_seen_at
                    FROM agentbox_orphan_candidates o
                    WHERE o.first_seen_at <= to_timestamp(%s) - make_interval(secs => %s)
                      AND o.last_seen_at <= to_timestamp(%s)
                      AND NOT EXISTS (
                        SELECT 1 FROM agentbox_lifecycle_claims c
                        WHERE c.sandbox_id = o.sandbox_id AND c.expires_at > now()
                      )
                    ORDER BY o.first_seen_at
                    """,
                    (inventory_started_at, grace_seconds, inventory_started_at),
                )
            ).fetchall()
        return [self._orphan(row) for row in rows]

    async def list_orphans(
        self,
        provider_name: str,
        *,
        sandbox_id: str | None = None,
    ) -> list[OrphanCandidate]:
        sandbox_filter = "" if sandbox_id is None else "AND sandbox_id = %s"
        params = (
            (provider_name,)
            if sandbox_id is None
            else (provider_name, sandbox_id)
        )
        async with self._pool.connection() as conn:
            rows = await (
                await conn.execute(
                    f"""
                    SELECT provider_name, provider_id, sandbox_id,
                        extract(epoch FROM first_seen_at) AS first_seen_at,
                        extract(epoch FROM last_seen_at) AS last_seen_at
                    FROM agentbox_orphan_candidates
                    WHERE provider_name = %s {sandbox_filter}
                    ORDER BY first_seen_at, provider_id
                    """,
                    params,
                )
            ).fetchall()
        return [self._orphan(row) for row in rows]

    async def clear_orphan(self, provider_name: str, provider_id: str) -> bool:
        async with self._pool.connection() as conn:
            row = await (
                await conn.execute(
                    """
                    DELETE FROM agentbox_orphan_candidates
                    WHERE provider_name = %s AND provider_id = %s RETURNING 1
                    """,
                    (provider_name, provider_id),
                )
            ).fetchone()
        return bool(row)

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
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    (provider_scope,),
                )
                sandbox_exists = await (
                    await conn.execute(
                        "SELECT 1 FROM agentbox_sandboxes WHERE sandbox_id = %s",
                        (sandbox_id,),
                    )
                ).fetchone()
                if not sandbox_exists:
                    return None
                await conn.execute(
                    """
                    DELETE FROM agentbox_provider_allocations
                    WHERE provider_scope = %s AND state = 'reserved'
                      AND expires_at <= now()
                    """,
                    (provider_scope,),
                )
                row = await (
                    await conn.execute(
                        """
                        SELECT allocation_id, provider_scope, sandbox_id, owner,
                            state, provider_id,
                            extract(epoch FROM expires_at) AS expires_at,
                            extract(epoch FROM updated_at) AS updated_at
                        FROM agentbox_provider_allocations
                        WHERE provider_scope = %s AND sandbox_id = %s
                          AND state = 'active'
                        ORDER BY updated_at DESC LIMIT 1
                        FOR UPDATE
                        """,
                        (provider_scope, sandbox_id),
                    )
                ).fetchone()
                if row:
                    return self._provider_allocation(row)
                row = await (
                    await conn.execute(
                        """
                        SELECT allocation_id, provider_scope, sandbox_id, owner,
                            state, provider_id,
                            extract(epoch FROM expires_at) AS expires_at,
                            extract(epoch FROM updated_at) AS updated_at
                        FROM agentbox_provider_allocations
                        WHERE provider_scope = %s AND sandbox_id = %s
                          AND state = 'reserved'
                        FOR UPDATE
                        """,
                        (provider_scope, sandbox_id),
                    )
                ).fetchone()
                if row:
                    if row["owner"] != owner and float(row["expires_at"]) > time.time():
                        return None
                    row = await (
                        await conn.execute(
                            """
                            UPDATE agentbox_provider_allocations
                            SET owner = %s,
                                expires_at = now() + make_interval(secs => %s),
                                updated_at = now()
                            WHERE provider_scope = %s AND allocation_id = %s
                            RETURNING allocation_id, provider_scope, sandbox_id,
                                owner, state, provider_id,
                                extract(epoch FROM expires_at) AS expires_at,
                                extract(epoch FROM updated_at) AS updated_at
                            """,
                            (
                                owner,
                                ttl_seconds,
                                provider_scope,
                                row["allocation_id"],
                            ),
                        )
                    ).fetchone()
                    return self._provider_allocation(row)
                count = int(
                    (
                        await (
                            await conn.execute(
                                """
                                SELECT count(*) AS count
                                FROM agentbox_provider_allocations
                                WHERE provider_scope = %s AND (
                                    state = 'active' OR (
                                        state = 'reserved' AND expires_at > now()
                                    )
                                )
                                """,
                                (provider_scope,),
                            )
                        ).fetchone()
                    )["count"]
                )
                if count >= max_active:
                    return None
                row = await (
                    await conn.execute(
                        """
                        INSERT INTO agentbox_provider_allocations (
                            allocation_id, provider_scope, sandbox_id, owner,
                            state, expires_at
                        ) VALUES (
                            %s, %s, %s, %s, 'reserved',
                            now() + make_interval(secs => %s)
                        )
                        RETURNING allocation_id, provider_scope, sandbox_id,
                            owner, state, provider_id,
                            extract(epoch FROM expires_at) AS expires_at,
                            extract(epoch FROM updated_at) AS updated_at
                        """,
                        (
                            uuid.uuid4(),
                            provider_scope,
                            sandbox_id,
                            owner,
                            ttl_seconds,
                        ),
                    )
                ).fetchone()
        return self._provider_allocation(row)

    async def activate_provider_allocation(
        self,
        provider_scope: str,
        allocation_id: str,
        *,
        owner: str,
        provider_id: str,
    ) -> ProviderAllocation | None:
        async with self._pool.connection() as conn:
            row = await (
                await conn.execute(
                    """
                    UPDATE agentbox_provider_allocations
                    SET state = 'active', provider_id = %s,
                        expires_at = NULL, updated_at = now()
                    WHERE provider_scope = %s AND allocation_id = %s
                      AND ((state = 'reserved' AND owner = %s)
                        OR (state = 'active' AND provider_id = %s))
                    RETURNING allocation_id, provider_scope, sandbox_id, owner,
                        state, provider_id,
                        extract(epoch FROM expires_at) AS expires_at,
                        extract(epoch FROM updated_at) AS updated_at
                    """,
                    (
                        provider_id,
                        provider_scope,
                        allocation_id,
                        owner,
                        provider_id,
                    ),
                )
            ).fetchone()
        return self._provider_allocation(row) if row else None

    async def hold_provider_allocation(
        self,
        provider_scope: str,
        allocation_id: str,
        *,
        owner: str,
    ) -> ProviderAllocation | None:
        async with self._pool.connection() as conn:
            row = await (
                await conn.execute(
                    """
                    UPDATE agentbox_provider_allocations
                    SET owner = %s, expires_at = 'infinity'::timestamptz,
                        updated_at = now()
                    WHERE provider_scope = %s AND allocation_id = %s
                      AND state = 'reserved'
                    RETURNING allocation_id, provider_scope, sandbox_id, owner,
                        state, provider_id,
                        extract(epoch FROM expires_at) AS expires_at,
                        extract(epoch FROM updated_at) AS updated_at
                    """,
                    (owner, provider_scope, allocation_id),
                )
            ).fetchone()
        return self._provider_allocation(row) if row else None

    async def release_provider_allocation(
        self, provider_scope: str, allocation_id: str
    ) -> bool:
        async with self._pool.connection() as conn:
            row = await (
                await conn.execute(
                    """
                    DELETE FROM agentbox_provider_allocations
                    WHERE provider_scope = %s AND allocation_id = %s RETURNING 1
                    """,
                    (provider_scope, allocation_id),
                )
            ).fetchone()
        return bool(row)

    async def list_provider_allocations(
        self, provider_scope: str
    ) -> list[ProviderAllocation]:
        async with self._pool.connection() as conn:
            rows = await (
                await conn.execute(
                    """
                    SELECT allocation_id, provider_scope, sandbox_id, owner,
                        state, provider_id,
                        extract(epoch FROM expires_at) AS expires_at,
                        extract(epoch FROM updated_at) AS updated_at
                    FROM agentbox_provider_allocations
                    WHERE provider_scope = %s ORDER BY sandbox_id, allocation_id
                    """,
                    (provider_scope,),
                )
            ).fetchall()
        return [self._provider_allocation(row) for row in rows]

    async def reconcile_provider_allocations(
        self,
        provider_scope: str,
        active_provider_objects: dict[str, tuple[str, str | None]],
        *,
        inventory_started_at: float,
    ) -> None:
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    (provider_scope,),
                )
                await conn.execute(
                    """
                    DELETE FROM agentbox_provider_allocations
                    WHERE provider_scope = %s AND state = 'reserved'
                      AND expires_at <= now()
                    """,
                    (provider_scope,),
                )
                for provider_id, (
                    sandbox_id,
                    generation_token,
                ) in active_provider_objects.items():
                    await conn.execute(
                        "SELECT pg_advisory_xact_lock(hashtext(%s))",
                        (f"agentbox:lifecycle:{sandbox_id}",),
                    )
                    claimed = await (
                        await conn.execute(
                            """
                            SELECT 1 FROM agentbox_lifecycle_claims
                            WHERE sandbox_id = %s AND expires_at > now()
                            """,
                            (sandbox_id,),
                        )
                    ).fetchone()
                    if claimed:
                        # ENSURE/DELETE/env replacement owns this sandbox. Let
                        # that exact lifecycle operation publish its provider
                        # identity; a later inventory pass may reconcile it.
                        continue
                    reservation = None
                    if generation_token:
                        reservation = await (
                            await conn.execute(
                                """
                                SELECT allocation_id
                                FROM agentbox_provider_allocations
                                WHERE provider_scope = %s AND allocation_id = %s
                                  AND sandbox_id = %s AND state = 'reserved'
                                FOR UPDATE
                                """,
                                (provider_scope, generation_token, sandbox_id),
                            )
                        ).fetchone()
                    if reservation is None:
                        reservation = await (
                            await conn.execute(
                                """
                                SELECT a.allocation_id
                                FROM agentbox_provider_allocations a
                                JOIN agentbox_sandboxes s
                                  ON s.sandbox_id = a.sandbox_id
                                WHERE a.provider_scope = %s
                                  AND a.sandbox_id = %s
                                  AND a.state = 'reserved'
                                  AND s.provider_id = %s
                                FOR UPDATE OF a
                                """,
                                (provider_scope, sandbox_id, provider_id),
                            )
                        ).fetchone()
                    existing = await (
                        await conn.execute(
                            """
                            SELECT allocation_id FROM agentbox_provider_allocations
                            WHERE provider_scope = %s AND provider_id = %s
                            FOR UPDATE
                            """,
                            (provider_scope, provider_id),
                        )
                    ).fetchone()
                    if reservation:
                        await conn.execute(
                            """
                            DELETE FROM agentbox_provider_allocations
                            WHERE provider_scope = %s AND provider_id = %s
                              AND allocation_id <> %s
                            """,
                            (
                                provider_scope,
                                provider_id,
                                reservation["allocation_id"],
                            ),
                        )
                        await conn.execute(
                            """
                            UPDATE agentbox_provider_allocations
                            SET state = 'active', provider_id = %s,
                                expires_at = NULL, updated_at = to_timestamp(%s)
                            WHERE provider_scope = %s AND allocation_id = %s
                            """,
                            (
                                provider_id,
                                inventory_started_at,
                                provider_scope,
                                reservation["allocation_id"],
                            ),
                        )
                    elif existing:
                        await conn.execute(
                            """
                            UPDATE agentbox_provider_allocations
                            SET sandbox_id = %s, state = 'active', expires_at = NULL,
                                updated_at = to_timestamp(%s)
                            WHERE provider_scope = %s AND allocation_id = %s
                            """,
                            (
                                sandbox_id,
                                inventory_started_at,
                                provider_scope,
                                existing["allocation_id"],
                            ),
                        )
                    else:
                        await conn.execute(
                            """
                            INSERT INTO agentbox_provider_allocations (
                                allocation_id, provider_scope, sandbox_id, owner,
                                state, provider_id, expires_at, created_at, updated_at
                            ) VALUES (
                                %s, %s, %s, 'reconciler', 'active', %s, NULL,
                                to_timestamp(%s), to_timestamp(%s)
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
                # Never delete a durable active allocation merely because one
                # eventually consistent inventory snapshot omitted it. Exact
                # suspend/delete/purge paths release allocations explicitly.

    async def reconcile_provider_inventory(
        self,
        provider_scope: str,
        provider_name: str,
        provider_objects: dict[str, tuple[str, str | None, bool]],
        *,
        inventory_started_at: float,
    ) -> None:
        async with self._pool.connection() as conn:
            async with conn.transaction():
                # All inventory publication uses one lock order. Reservations
                # take only the scope lock and lifecycle claims take only their
                # sandbox lock, so neither can form a reverse-order cycle.
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    (provider_scope,),
                )
                sandbox_ids = sorted(
                    {sandbox_id for sandbox_id, _, _ in provider_objects.values()}
                )
                for sandbox_id in sandbox_ids:
                    await conn.execute(
                        "SELECT pg_advisory_xact_lock(hashtext(%s))",
                        (f"agentbox:lifecycle:{sandbox_id}",),
                    )

                await conn.execute(
                    """
                    DELETE FROM agentbox_provider_allocations
                    WHERE provider_scope = %s AND state = 'reserved'
                      AND expires_at <= now()
                    """,
                    (provider_scope,),
                )
                for sandbox_id in sandbox_ids:
                    claimed = await (
                        await conn.execute(
                            """
                            SELECT 1 FROM agentbox_lifecycle_claims
                            WHERE sandbox_id = %s AND expires_at > now()
                            """,
                            (sandbox_id,),
                        )
                    ).fetchone()
                    if claimed:
                        # Do not publish a snapshot captured before a live
                        # ENSURE/DELETE/env replacement completed.
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
                            reservation = await (
                                await conn.execute(
                                    """
                                    SELECT allocation_id
                                    FROM agentbox_provider_allocations
                                    WHERE provider_scope = %s
                                      AND allocation_id = %s
                                      AND sandbox_id = %s
                                      AND state = 'reserved'
                                    FOR UPDATE
                                    """,
                                    (
                                        provider_scope,
                                        generation_token,
                                        sandbox_id,
                                    ),
                                )
                            ).fetchone()
                        if reservation is None:
                            reservation = await (
                                await conn.execute(
                                    """
                                    SELECT a.allocation_id
                                    FROM agentbox_provider_allocations a
                                    JOIN agentbox_sandboxes s
                                      ON s.sandbox_id = a.sandbox_id
                                    WHERE a.provider_scope = %s
                                      AND a.sandbox_id = %s
                                      AND a.state = 'reserved'
                                      AND s.provider_id = %s
                                    FOR UPDATE OF a
                                    """,
                                    (provider_scope, sandbox_id, provider_id),
                                )
                            ).fetchone()
                        existing = await (
                            await conn.execute(
                                """
                                SELECT allocation_id
                                FROM agentbox_provider_allocations
                                WHERE provider_scope = %s AND provider_id = %s
                                FOR UPDATE
                                """,
                                (provider_scope, provider_id),
                            )
                        ).fetchone()
                        if reservation:
                            await conn.execute(
                                """
                                DELETE FROM agentbox_provider_allocations
                                WHERE provider_scope = %s AND provider_id = %s
                                  AND allocation_id <> %s
                                """,
                                (
                                    provider_scope,
                                    provider_id,
                                    reservation["allocation_id"],
                                ),
                            )
                            await conn.execute(
                                """
                                UPDATE agentbox_provider_allocations
                                SET state = 'active', provider_id = %s,
                                    expires_at = NULL,
                                    updated_at = to_timestamp(%s)
                                WHERE provider_scope = %s AND allocation_id = %s
                                """,
                                (
                                    provider_id,
                                    inventory_started_at,
                                    provider_scope,
                                    reservation["allocation_id"],
                                ),
                            )
                        elif existing:
                            await conn.execute(
                                """
                                UPDATE agentbox_provider_allocations
                                SET sandbox_id = %s, state = 'active',
                                    expires_at = NULL,
                                    updated_at = to_timestamp(%s)
                                WHERE provider_scope = %s AND allocation_id = %s
                                """,
                                (
                                    sandbox_id,
                                    inventory_started_at,
                                    provider_scope,
                                    existing["allocation_id"],
                                ),
                            )
                        else:
                            await conn.execute(
                                """
                                INSERT INTO agentbox_provider_allocations (
                                    allocation_id, provider_scope, sandbox_id,
                                    owner, state, provider_id, expires_at,
                                    created_at, updated_at
                                ) VALUES (
                                    %s, %s, %s, 'reconciler', 'active', %s,
                                    NULL, to_timestamp(%s), to_timestamp(%s)
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

                    record = await (
                        await conn.execute(
                            """
                            SELECT provider_id FROM agentbox_sandboxes
                            WHERE sandbox_id = %s
                            """,
                            (sandbox_id,),
                        )
                    ).fetchone()
                    allocation_rows = await (
                        await conn.execute(
                            """
                            SELECT provider_id
                            FROM agentbox_provider_allocations
                            WHERE provider_scope = %s AND sandbox_id = %s
                              AND provider_id IS NOT NULL
                              AND allocation_id NOT LIKE 'provider:%%'
                            """,
                            (provider_scope, sandbox_id),
                        )
                    ).fetchall()
                    known_exact_ids = {
                        str(row["provider_id"]) for row in allocation_rows
                    }
                    if record and record["provider_id"] is not None:
                        known_exact_ids.add(str(record["provider_id"]))

                    for provider_id, _, _ in items:
                        if provider_id in known_exact_ids:
                            await conn.execute(
                                """
                                DELETE FROM agentbox_orphan_candidates
                                WHERE provider_name = %s AND provider_id = %s
                                """,
                                (provider_name, provider_id),
                            )
                        else:
                            await conn.execute(
                                """
                                INSERT INTO agentbox_orphan_candidates (
                                    provider_name, provider_id, sandbox_id,
                                    first_seen_at, last_seen_at
                                ) VALUES (
                                    %s, %s, %s, to_timestamp(%s), to_timestamp(%s)
                                ) ON CONFLICT (provider_name, provider_id)
                                DO UPDATE SET
                                    sandbox_id = coalesce(
                                        EXCLUDED.sandbox_id,
                                        agentbox_orphan_candidates.sandbox_id
                                    ),
                                    last_seen_at = EXCLUDED.last_seen_at
                                """,
                                (
                                    provider_name,
                                    provider_id,
                                    sandbox_id,
                                    inventory_started_at,
                                    inventory_started_at,
                                ),
                            )

    async def close(self) -> None:
        if self._pool is not None:
            pool, self._pool = self._pool, None
            await pool.close()

    @staticmethod
    def _provider_allocation(row: Any) -> ProviderAllocation:
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
