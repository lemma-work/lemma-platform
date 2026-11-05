"""Provisioning and ACLs for the RLS-subject roles that ad-hoc queries run under.

Ad-hoc SQL (``query.execute``, the agent's ``pod_query``, a contact's read of
their own rows) runs via ``SET LOCAL ROLE`` as a role that is neither a
superuser nor ``BYPASSRLS``, so row-level security is actually enforced -- the
application's own connection would otherwise see every row.

**One role per pod schema** (``pod_query_role_name``). There used to be a single
shared role, ``datastore_query_role``, holding ``USAGE`` and ``SELECT`` on every
pod schema, and what kept pod A's SQL out of pod B's tables was the SQL parser
alone. The parser is a denylist over a language with string-taking functions:
``table_to_xml('pod_<b>.t', ...)`` names a table in a string the parser never
sees as a table, and it read another organization's rows. Each pod schema now
has a role that can reach that schema and nothing else, so the database itself
refuses pod B's tables to pod A's query, however the query names them -- by
string, by ``regclass`` oid, through any function nobody has thought of yet.
The parser is defence in depth; the boundary is a privilege.

Access is given **by construction**, never by sweeping the catalog. A pod
schema is born with its role, ``USAGE`` for it, and a per-schema default
privilege that grants it ``SELECT`` on every table the app later creates there,
all inside the transaction that creates the schema. Nothing grants per table,
and nothing runs at boot: a repair that walks every pod schema grows with the
number of pods ever created, which is how it came to dominate API startup.

The default privilege is scoped ``IN SCHEMA`` on purpose. A database-wide
``ALTER DEFAULT PRIVILEGES ... ON TABLES`` would also hand the role ``SELECT`` on
every platform table a later migration creates whenever the datastore shares the
platform database, and ``public`` is reachable by every role.

Schemas that predate this carry no such role. They heal on first use:
``missing_access`` tells a real missing relation from an invisible one, and
``heal_schema`` creates that one schema's role and grants it that schema and
nothing else. The shared role's old grants are withdrawn by a pod-schema
migration step (``withdraw_shared_role``), so it reaches nothing even if
something still sets it. The role is dropped with its schema (``drop_pod_role``).

A deployment whose app role cannot create roles must still be able to create
pods and tables, so provisioning is best-effort; queries then fail closed.
Such a deployment cannot pre-provision these roles by hand the way it could the
shared one: their names are only known when a pod is made.
"""

import asyncio
import random
import re

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from app.core.infrastructure.db.transient_errors import raise_if_database_unavailable
from app.core.log.log import get_logger
from app.modules.datastore.config import datastore_settings
from app.modules.datastore.infrastructure import pod_schema_memo
from app.modules.datastore.infrastructure.sql_identifiers import (
    MAX_IDENTIFIER_BYTES,
    sanitize_identifier,
)

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

#: The change log's tables (``datastore.changes.schema``). The schema's default
#: privilege grants the role ``SELECT`` on them when they are created, and
#: ``GRANT ... ON ALL TABLES`` would again on a heal, so both are followed by a
#: revoke: the log carries every write's row, including rows a per-user policy
#: hides, and has no policy of its own. Named here rather than imported because
#: the change log imports this module; a unit test holds the two together.
WITHHELD_TABLES = ("reserved_changes", "reserved_change_subscribers")

# "_" and a pod uuid's 32 hex digits: the part of a role name that must survive.
_POD_SUFFIX_BYTES = 33


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
    """The configured base name every pod's query role is derived from.

    Also the name of the role every pod shared before roles were per pod,
    which ``withdraw_shared_role`` strips of its pod-schema grants.
    """
    return sanitize_identifier(datastore_settings.datastore_query_role)


def pod_query_role_name(schema_name: str) -> str:
    """The role one pod schema's ad-hoc SQL runs as: the base, then the pod.

    The pod's 32 hex digits always fit, and the base gives way to them: a role
    name is an identifier, PostgreSQL truncates past 63 bytes *silently*, and a
    truncated pod part would let two pods share one role -- the very boundary
    this exists to draw. Refuses anything that is not a pod schema's name.
    """
    if not POD_SCHEMA_RE.fullmatch(schema_name):
        raise ValueError(f"not a pod schema: {schema_name!r}")
    room = MAX_IDENTIFIER_BYTES - _POD_SUFFIX_BYTES
    base = query_role_name().encode("utf-8")[:room].decode("utf-8", "ignore")
    pod_hex = schema_name.removeprefix("pod_").replace("_", "")
    return sanitize_identifier(f"{base}_{pod_hex}")


def schema_access_statements(schema_name: str) -> tuple[str, ...]:
    """What makes one pod schema, and every table later made in it, readable
    by that schema's role and by no other.

    Issued in the transaction that creates the schema. ``FOR ROLE CURRENT_USER``
    because default privileges attach to whoever creates the objects, and the
    app's own role is what creates pod tables.
    """
    role = pod_query_role_name(schema_name)
    return (
        f'GRANT USAGE ON SCHEMA "{schema_name}" TO "{role}"',
        (
            "ALTER DEFAULT PRIVILEGES FOR ROLE CURRENT_USER "
            f'IN SCHEMA "{schema_name}" GRANT SELECT ON TABLES TO "{role}"'
        ),
    )


def withhold_statement(schema_name: str) -> str:
    """Revoke the schema's role from the change log's tables, where they exist.

    One ``DO`` block so it is a single round trip and a no-op, not an error,
    for a role or a table that is not there.
    """
    role = pod_query_role_name(schema_name)
    revokes = " ".join(
        f"IF to_regclass('\"{schema_name}\".{table}') IS NOT NULL THEN "
        f'REVOKE ALL ON "{schema_name}".{table} FROM "{role}"; END IF;'
        for table in WITHHELD_TABLES
    )
    return (
        "DO $$ BEGIN "
        f"IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "
        f"{revokes} END IF; END $$"
    )


async def _role_exists(conn, role: str) -> bool:
    result = await conn.execute(
        text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": role}
    )
    return result.scalar() is not None


async def _provision_role(conn, role: str) -> None:
    """Make ``role`` exist, ``NOLOGIN``, and enterable by the connecting role.

    Both statements are *probed before they are issued*, because PostgreSQL
    checks the ``CREATEROLE`` privilege before it checks for a duplicate:
    ``CREATE ROLE`` on an existing role raises ``insufficient_privilege``,
    not ``duplicate_object``, so the exception guard below cannot catch it.
    Asking first makes the no-op a genuine no-op.

    The guard still covers two sessions creating one role at once; the loser
    of that race sees ``unique_violation`` on ``pg_authid``, not
    ``duplicate_object``, when the winner commits while it waits.
    """
    if not await _role_exists(conn, role):
        await conn.execute(
            text(
                f'DO $$ BEGIN CREATE ROLE "{role}" '
                "NOLOGIN NOSUPERUSER NOBYPASSRLS; "
                "EXCEPTION WHEN duplicate_object OR unique_violation THEN NULL; "
                "END $$"
            )
        )
    # Whether the connecting role may *SET* the role, not whether it inherits
    # it: ad-hoc queries reach the role through SET LOCAL ROLE. Before 16 that
    # is 'MEMBER'. From 16 membership and SET are separate options, and a
    # non-superuser CREATEROLE role that creates a role is made a member
    # WITH ADMIN but *without* SET (``createrole_self_grant`` is empty by
    # default) -- so 'MEMBER' answers yes for a role it cannot enter, and every
    # query is refused. 'SET' does not exist before 16; CASE keeps it unasked.
    can_set = await conn.execute(
        text(
            "SELECT CASE WHEN current_setting('server_version_num')::int >= 160000 "
            "THEN pg_has_role(CURRENT_USER, CAST(:role AS name), 'SET') "
            "ELSE pg_has_role(CURRENT_USER, CAST(:role AS name), 'MEMBER') END"
        ),
        {"role": role},
    )
    if not can_set.scalar():
        # A grant of its own, by the connecting role, which carries SET by
        # default; the implicit one CREATE ROLE made is left as it was.
        await conn.execute(text(f'GRANT "{role}" TO CURRENT_USER'))


async def grant_at_birth(conn, schema_name: str) -> bool:
    """Give a schema being created its role and its access, in that transaction.

    Under a savepoint, so a deployment whose app role cannot create roles still
    gets its schema: the savepoint rolls back, the schema is made without
    access, and its queries fail closed. Returns whether the access was given.
    In the creating transaction rather than before it, so a schema whose
    creation rolls back leaves no role behind.
    """
    role = pod_query_role_name(schema_name)
    try:
        async with conn.begin_nested():
            await _provision_role(conn, role)
            for statement in schema_access_statements(schema_name):
                await conn.execute(text(statement))
    except DBAPIError as exc:
        raise_if_database_unavailable(exc)
        logger.warning("datastore.query_role.ensure.degraded", role=role, exc_info=True)
        return False
    return True


async def drop_pod_role(conn, schema_name: str) -> None:
    """Drop a pod's role, in the transaction that drops its schema.

    After the schema: the role's privileges, its schema's default privilege
    among them, are on objects the ``CASCADE`` removes, and a role still holding
    any cannot be dropped. Probed first for the same reason ``_provision_role``
    is: ``DROP ROLE`` checks ``CREATEROLE`` before it looks for the role, so on a
    deployment that could never create one, an unconditional ``IF EXISTS`` would
    fail every schema drop over a role that is not there.
    """
    role = pod_query_role_name(schema_name)
    if await _role_exists(conn, role):
        await conn.execute(text(f'DROP ROLE IF EXISTS "{role}"'))


async def withdraw_shared_role(conn, schema_name: str) -> None:
    """Pod-schema step: the shared role loses everything it held on this schema.

    Before roles were per pod, one role held ``USAGE``, ``SELECT`` on every
    table, and a default privilege for future tables on every pod schema. The
    queries no longer set it, but a role that can still read every pod is the
    hole waiting for the next thing that does. Revoked per table as well as on
    the schema: ``SELECT`` on a table is checked without the schema when the
    table is reached by oid (``'16384'::regclass``), not by name.

    The pod's own role is not created here; a schema's first query heals it in.
    A role that does not exist has nothing to revoke.
    """
    if not POD_SCHEMA_RE.fullmatch(schema_name):
        raise ValueError(f"not a pod schema: {schema_name!r}")
    role = query_role_name()
    if not await _role_exists(conn, role):
        return
    for statement in (
        f'REVOKE ALL ON ALL TABLES IN SCHEMA "{schema_name}" FROM "{role}"',
        (
            "ALTER DEFAULT PRIVILEGES FOR ROLE CURRENT_USER "
            f'IN SCHEMA "{schema_name}" REVOKE ALL ON TABLES FROM "{role}"'
        ),
        f'REVOKE ALL ON SCHEMA "{schema_name}" FROM "{role}"',
    ):
        await conn.execute(text(statement))


class QueryRoleGrants:
    """Owns each pod role's existence and its read access to its own schema."""

    def __init__(self, engine):
        self._engine = engine

    async def ensure_role(self, schema_name: str) -> None:
        """Idempotently establish one pod schema's read-only query role.

        Remembered per process (``pod_schema_memo``), and forgotten when the
        schema -- and with it the role -- is dropped, so this costs two catalog
        lookups once per pod per process.
        """
        role = pod_query_role_name(schema_name)
        if pod_schema_memo.query_role_ready(schema_name, role):
            return
        async with self._engine.begin() as conn:
            await _provision_role(conn, role)
        pod_schema_memo.remember_query_role(schema_name, role)

    async def try_ensure_role(self, schema_name: str) -> bool:
        """``ensure_role`` for callers that must not fail because of it.

        A query proceeds to fail closed on ``SET LOCAL ROLE``. Warning, not
        debug: this is the only line that says why the pod's queries are refused.
        """
        try:
            await self.ensure_role(schema_name)
        except SQLAlchemyError as exc:
            raise_if_database_unavailable(exc)
            logger.warning(
                "datastore.query_role.ensure.degraded",
                role=pod_query_role_name(schema_name),
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
        """Whether the pod's role is absent or cannot see the schema or a table.

        Asked only after a query failed. Without ``USAGE`` an unqualified name
        on the pod's ``search_path`` does not say "permission denied" — the
        schema is skipped and the table reads as *does not exist* — so the
        error alone cannot tell a legacy schema from a typo. The catalog can.
        The change log's tables are withheld on purpose and do not count, or
        every typo in a pod with a change log would look like a legacy schema.
        """
        if not POD_SCHEMA_RE.fullmatch(schema_name):
            return False
        role = pod_query_role_name(schema_name)
        withheld = ", ".join(f"'{table}'" for table in WITHHELD_TABLES)
        async with self._engine.connect() as conn:
            # Asked apart: has_*_privilege raises on a role that does not exist.
            if not await _role_exists(conn, role):
                return True
            result = await conn.execute(
                text(
                    "SELECT NOT has_schema_privilege(:role, n.oid, 'USAGE') "
                    "OR EXISTS (SELECT 1 FROM pg_class c "
                    "WHERE c.relnamespace = n.oid "
                    f"AND c.relkind IN {_READABLE_RELKINDS} "
                    f"AND c.relname NOT IN ({withheld}) "
                    "AND NOT has_table_privilege(:role, c.oid, 'SELECT')) "
                    "FROM pg_namespace n WHERE n.nspname = :schema"
                ),
                {"role": role, "schema": schema_name},
            )
            return bool(result.scalar())

    async def heal_schema(self, schema_name: str) -> bool:
        """Give the pod's role what a new pod schema is born with. One schema only.

        For schemas created before access was granted by construction: the role
        is created, the existing tables get ``SELECT`` -- all but the change
        log's -- and the schema gets the same default privilege a new one
        carries, so tables added later need no second heal. Returns whether it
        worked; the caller retries its query only if so.
        """
        if not POD_SCHEMA_RE.fullmatch(schema_name):
            return False
        role = pod_query_role_name(schema_name)

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
                await conn.execute(text(withhold_statement(schema_name)))

        if not await self.try_ensure_role(schema_name):
            return False
        try:
            await self._retrying(grant, schema_name)
        except DBAPIError as exc:
            raise_if_database_unavailable(exc)
            logger.warning(
                "datastore.query_role.heal.degraded",
                schema_name=schema_name,
                exc_info=True,
            )
            return False
        logger.info("datastore.query_role.schema_healed", schema_name=schema_name)
        return True
