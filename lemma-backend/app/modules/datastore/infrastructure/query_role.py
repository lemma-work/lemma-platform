"""Provisioning and ACLs for the RLS-subject role that ad-hoc queries run under.

Ad-hoc SQL (``query.execute``) runs as ``datastore_query_role`` via
``SET LOCAL ROLE`` so row-level security is actually enforced — the
application's own connection is a superuser/BYPASSRLS role that would otherwise
see every row. That only works if the role can reach the pod's schema, which
makes these grants part of provisioning a pod, not an afterthought of creating
its first table.

Every grant here is best-effort and never raises: a deployment whose app role
cannot create or grant roles must still be able to create pods and tables.
Queries fail closed, and ``backfill_grants`` repairs whatever was missed.

The commonest way to lose a grant, though, is not privilege but contention.
``GRANT`` writes a catalog row, and two sessions granting on the same schema at
once — two API processes, or two test workers — leave one of them holding a
transient error instead of the grant it asked for. Those are retried here
rather than degraded, because "best-effort" was never meant to mean "gives up
the first time two pods are created at the same moment".
"""

import asyncio
import random

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.log.log import get_logger
from app.modules.datastore.config import datastore_settings
from app.modules.datastore.infrastructure.sql_identifiers import sanitize_identifier

logger = get_logger(__name__)

# Bounded: a conflict that survives four attempts is not contention any more,
# and the caller's degraded path is a better answer than a longer wait.
_GRANT_ATTEMPTS = 4
_GRANT_BACKOFF_SECONDS = 0.05

# 40001 serialization_failure, 40P01 deadlock_detected — the two codes
# PostgreSQL documents as "retry the transaction".
_TRANSIENT_SQLSTATES = frozenset({"40001", "40P01"})

# `tuple concurrently updated` is raised by a bare elog() deep in the catalog
# update path, so it arrives as XX000 (internal_error) — a code it shares with
# every other unclassified server fault. Unlike the codes above it can only be
# recognised by its message, which is exact enough to be safe and is the reason
# this list exists at all.
_TRANSIENT_MESSAGES = ("tuple concurrently updated", "tuple concurrently deleted")


def _is_transient_conflict(exc: BaseException) -> bool:
    """Whether PostgreSQL refused the statement over contention, not privilege.

    Read through ``orig`` first: SQLAlchemy wraps the driver error, and the
    SQLSTATE is the only identification that does not depend on message text.
    """
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(exc, "sqlstate", None)
    if sqlstate in _TRANSIENT_SQLSTATES:
        return True
    message = str(exc)
    return any(fragment in message for fragment in _TRANSIENT_MESSAGES)


class QueryRoleGrants:
    """Owns the query role's existence and its read access to pod schemas."""

    def __init__(self, engine):
        self._engine = engine
        self._role_ready = False

    def _role(self) -> str:
        """Validated identifier for the RLS-subject role used by ad-hoc queries."""
        return sanitize_identifier(datastore_settings.datastore_query_role)

    async def ensure_role(self) -> None:
        """Idempotently establish the read-only, RLS-subject query role.

        The role is ``NOLOGIN`` (entered only via ``SET ROLE``) and granted to
        the connecting role so a non-superuser app role can switch into it.

        Both statements are *probed before they are issued*, because PostgreSQL
        checks the ``CREATEROLE`` privilege before it checks for a duplicate:
        ``CREATE ROLE`` on an existing role raises ``insufficient_privilege``,
        not ``duplicate_object``, so the exception guard below cannot catch it.
        An app role that is merely a member of an already-provisioned query
        role — the least privilege this mechanism can run on — would otherwise
        fail here on every call, taking ``try_grant`` and the ``backfill_grants``
        repair path down with it. Asking first costs two catalog lookups and
        makes the no-op case a genuine no-op.
        """
        if self._role_ready:
            return
        role = self._role()
        async with self._engine.begin() as conn:
            exists = await conn.execute(
                text("SELECT 1 FROM pg_roles WHERE rolname = :role"),
                {"role": role},
            )
            if exists.scalar() is None:
                # Still guarded: two API processes can reach this together.
                await conn.execute(
                    text(
                        f'DO $$ BEGIN CREATE ROLE "{role}" '
                        "NOLOGIN NOSUPERUSER NOBYPASSRLS; "
                        "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
                    )
                )
            # 'MEMBER', not 'USAGE': ad-hoc queries reach the role through
            # SET LOCAL ROLE, which inherited privileges alone do not permit.
            member = await conn.execute(
                text("SELECT pg_has_role(CURRENT_USER, :role, 'MEMBER')"),
                {"role": role},
            )
            if not member.scalar():
                await conn.execute(text(f'GRANT "{role}" TO CURRENT_USER'))
        self._role_ready = True

    async def _retrying(
        self,
        run,
        schema_name: str | None = None,
        table_name: str | None = None,
    ) -> None:
        """Run ``run`` again while PostgreSQL is only losing a catalog race.

        ``ensure_role`` guards its own concurrency in SQL, with ``EXCEPTION
        WHEN duplicate_object``. A ``GRANT`` has no such clause available: the
        conflict surfaces as an aborted transaction, so the equivalent guard
        has to be issuing the statement again. Each attempt opens a fresh
        transaction — the failed one is already rolled back, and every
        statement under this role is idempotent, so a repeat is a no-op once
        the racing session has committed.

        Backoff is jittered so that workers that collided once do not line up
        to collide again on the same schedule.
        """
        for attempt in range(1, _GRANT_ATTEMPTS + 1):
            try:
                await run()
                return
            except DBAPIError as exc:
                if attempt == _GRANT_ATTEMPTS or not _is_transient_conflict(exc):
                    raise
                logger.debug(
                    "datastore.query_role.grant.contended",
                    attempt=attempt,
                    schema_name=schema_name,
                    table_name=table_name,
                )
            delay = _GRANT_BACKOFF_SECONDS * 2 ** (attempt - 1)
            await asyncio.sleep(delay + random.uniform(0, delay / 2))

    async def try_grant(self, schema_name: str, table_name: str | None = None) -> None:
        """Best-effort read access to one schema, and optionally one table.

        Runs in its own transaction so a failure cannot roll back the schema or
        table it follows, and retries a transient conflict before degrading —
        losing this grant to a momentary catalog race would be indistinguishable
        from never having had the privilege, and only one of those is worth a
        warning.

        Logged at warning, not debug: a pod that silently loses this grant
        answers every ``query.execute`` with "permission denied for schema",
        and nothing else in the system says why.
        """
        role = self._role()

        async def grant() -> None:
            await self.ensure_role()
            async with self._engine.begin() as conn:
                await conn.execute(
                    text(f'GRANT USAGE ON SCHEMA "{schema_name}" TO "{role}"')
                )
                if table_name is not None:
                    await conn.execute(
                        text(
                            f'GRANT SELECT ON "{schema_name}"."{table_name}" '
                            f'TO "{role}"'
                        )
                    )

        try:
            await self._retrying(grant, schema_name=schema_name, table_name=table_name)
        except Exception:  # noqa: BLE001
            logger.warning(
                "datastore.query_role.grant.degraded",
                schema_name=schema_name,
                table_name=table_name,
                exc_info=True,
            )

    async def backfill_grants(self) -> None:
        """Grant read access across all existing pod schemas.

        Idempotent; covers pods whose schemas/tables were created before the
        role mechanism existed. Safe to run at every startup, but it is the
        repair path — schemas and tables grant on creation.

        Retried on the same conflicts as ``try_grant``, and with more at stake:
        this is one transaction over *every* pod schema, so a single grant
        losing a race to a pod being created alongside it discards the repair
        for all the others too, until the next restart.
        """
        role = self._role()

        async def backfill() -> None:
            await self.ensure_role()
            async with self._engine.begin() as conn:
                await conn.execute(
                    text(
                        "DO $$ DECLARE s text; BEGIN "
                        "FOR s IN SELECT nspname FROM pg_namespace "
                        "WHERE nspname LIKE 'pod\\_%' LOOP "
                        f"EXECUTE format('GRANT USAGE ON SCHEMA %I TO \"{role}\"', s); "
                        "EXECUTE format("
                        f"'GRANT SELECT ON ALL TABLES IN SCHEMA %I TO \"{role}\"', s); "
                        "END LOOP; END $$"
                    )
                )

        await self._retrying(backfill)
