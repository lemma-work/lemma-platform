"""Provisioning and ACLs for the RLS-subject role that ad-hoc queries run under.

Ad-hoc SQL (``query.execute``) runs as ``datastore_query_role`` via
``SET LOCAL ROLE`` so row-level security is actually enforced — the
application's own connection is a superuser/BYPASSRLS role that would otherwise
see every row. That only works if the role can reach the pod's schema.

Access is given **by construction**, never by sweeping the catalog. A pod
schema is born with ``USAGE`` for the role and with a per-schema default
privilege that grants ``SELECT`` on every table the app later creates in it, all
inside the transaction that creates the schema. Nothing grants per table, and
nothing runs at boot: a repair that walks every pod schema grows with the number
of pods ever created, which is how it came to dominate API startup.

The default privilege is scoped ``IN SCHEMA`` on purpose. A database-wide
``ALTER DEFAULT PRIVILEGES ... ON TABLES`` would also hand the role ``SELECT`` on
every platform table a later migration creates whenever the datastore shares the
platform database (the default), and ``public`` is reachable by every role.

Schemas that predate this carry no such ACL. They heal on first use:
``missing_access`` tells a real missing relation from an invisible one, and
``heal_schema`` grants that one schema and nothing else.

A deployment whose app role cannot create the role must still be able to create
pods and tables, so ensuring the role is best-effort; queries then fail closed.
"""

import asyncio
import random
import re

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

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

#: What ``SchemaManager.get_schema_name`` produces: ``pod_`` and a UUID with its
#: hyphens replaced. Healing grants to exactly these and nothing else.
POD_SCHEMA_RE = re.compile(r"pod_[0-9a-f]{8}(?:_[0-9a-f]{4}){3}_[0-9a-f]{12}")

# Relation kinds `GRANT SELECT ON ALL TABLES IN SCHEMA` covers.
_READABLE_RELKINDS = "('r', 'p', 'v', 'm', 'f')"


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


def query_role_name() -> str:
    """Validated identifier for the RLS-subject role used by ad-hoc queries."""
    return sanitize_identifier(datastore_settings.datastore_query_role)


def schema_access_statements(schema_name: str) -> tuple[str, ...]:
    """What makes one pod schema, and every table later made in it, readable.

    Issued in the transaction that creates the schema. ``FOR ROLE CURRENT_USER``
    because default privileges attach to whoever creates the objects, and the
    app's own role is what creates pod tables.
    """
    role = query_role_name()
    return (
        f'GRANT USAGE ON SCHEMA "{schema_name}" TO "{role}"',
        (
            "ALTER DEFAULT PRIVILEGES FOR ROLE CURRENT_USER "
            f'IN SCHEMA "{schema_name}" GRANT SELECT ON TABLES TO "{role}"'
        ),
    )


class QueryRoleGrants:
    """Owns the query role's existence and its read access to pod schemas."""

    def __init__(self, engine):
        self._engine = engine
        self._role_ready = False

    async def ensure_role(self) -> None:
        """Idempotently establish the read-only, RLS-subject query role.

        The role is ``NOLOGIN`` (entered only via ``SET ROLE``) and granted to
        the connecting role so a non-superuser app role can switch into it.
        Cached per instance, and the manager holding it is a process singleton,
        so this costs two catalog lookups once per process.

        Both statements are *probed before they are issued*, because PostgreSQL
        checks the ``CREATEROLE`` privilege before it checks for a duplicate:
        ``CREATE ROLE`` on an existing role raises ``insufficient_privilege``,
        not ``duplicate_object``, so the exception guard below cannot catch it.
        An app role that is merely a member of an already-provisioned query
        role — the least privilege this mechanism can run on — would otherwise
        fail here on every call. Asking first makes the no-op a genuine no-op.
        """
        if self._role_ready:
            return
        role = query_role_name()
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

    async def try_ensure_role(self) -> bool:
        """``ensure_role`` for callers that must not fail because of it.

        Schema creation proceeds without the role's ACL, and a query proceeds
        to fail closed on ``SET LOCAL ROLE``. Warning, not debug: this is the
        only line that says why every query on the deployment is refused.
        """
        try:
            await self.ensure_role()
        except SQLAlchemyError:
            logger.warning(
                "datastore.query_role.ensure.degraded",
                role=query_role_name(),
                exc_info=True,
            )
            return False
        return True

    async def _retrying(self, run, schema_name: str) -> None:
        """Run ``run`` again while PostgreSQL is only losing a catalog race.

        A ``GRANT`` has no ``EXCEPTION WHEN`` available to it: two sessions
        granting on one schema at once leave one of them with an aborted
        transaction. Each attempt opens a fresh transaction and every statement
        is idempotent, so a repeat is a no-op once the other session commits.
        Jittered so that callers which collided once do not collide again.
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
                )
            delay = _GRANT_BACKOFF_SECONDS * 2 ** (attempt - 1)
            await asyncio.sleep(delay + random.uniform(0, delay / 2))

    async def missing_access(self, schema_name: str) -> bool:
        """Whether the role cannot see this pod schema or one of its tables.

        Asked only after a query failed. Without ``USAGE`` an unqualified name
        on the pod's ``search_path`` does not say "permission denied" — the
        schema is skipped and the table reads as *does not exist* — so the
        error alone cannot tell a legacy schema from a typo. The catalog can.
        """
        if not POD_SCHEMA_RE.fullmatch(schema_name):
            return False
        async with self._engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT NOT has_schema_privilege(:role, n.oid, 'USAGE') "
                    "OR EXISTS (SELECT 1 FROM pg_class c "
                    "WHERE c.relnamespace = n.oid "
                    f"AND c.relkind IN {_READABLE_RELKINDS} "
                    "AND NOT has_table_privilege(:role, c.oid, 'SELECT')) "
                    "FROM pg_namespace n WHERE n.nspname = :schema"
                ),
                {"role": query_role_name(), "schema": schema_name},
            )
            return bool(result.scalar())

    async def heal_schema(self, schema_name: str) -> bool:
        """Give the role what a new pod schema is born with. One schema only.

        For schemas created before access was granted by construction: the
        existing tables get ``SELECT``, and the schema gets the same default
        privilege a new one carries, so tables added later need no second heal.
        Returns whether it worked; the caller retries its query only if so.
        """
        if not POD_SCHEMA_RE.fullmatch(schema_name):
            return False
        role = query_role_name()

        async def grant() -> None:
            async with self._engine.begin() as conn:
                for statement in schema_access_statements(schema_name):
                    await conn.execute(text(statement))
                await conn.execute(
                    text(
                        f'GRANT SELECT ON ALL TABLES IN SCHEMA "{schema_name}" '
                        f'TO "{role}"'
                    )
                )

        if not await self.try_ensure_role():
            return False
        try:
            await self._retrying(grant, schema_name)
        except DBAPIError:
            logger.warning(
                "datastore.query_role.heal.degraded",
                schema_name=schema_name,
                exc_info=True,
            )
            return False
        logger.info("datastore.query_role.schema_healed", schema_name=schema_name)
        return True
