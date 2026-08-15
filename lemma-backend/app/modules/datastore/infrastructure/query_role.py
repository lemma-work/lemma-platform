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
"""

from sqlalchemy import text

from app.core.log.log import get_logger
from app.modules.datastore.config import datastore_settings
from app.modules.datastore.infrastructure.sql_identifiers import sanitize_identifier

logger = get_logger(__name__)


class QueryRoleGrants:
    """Owns the query role's existence and its read access to pod schemas."""

    def __init__(self, engine):
        self._engine = engine
        self._role_ready = False

    def _role(self) -> str:
        """Validated identifier for the RLS-subject role used by ad-hoc queries."""
        return sanitize_identifier(datastore_settings.datastore_query_role)

    async def ensure_role(self) -> None:
        """Idempotently create the read-only, RLS-subject query role.

        The role is ``NOLOGIN`` (entered only via ``SET ROLE``) and granted to
        the connecting role so a non-superuser app role can switch into it.
        """
        if self._role_ready:
            return
        role = self._role()
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    f'DO $$ BEGIN CREATE ROLE "{role}" '
                    "NOLOGIN NOSUPERUSER NOBYPASSRLS; "
                    "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
                )
            )
            await conn.execute(text(f'GRANT "{role}" TO CURRENT_USER'))
        self._role_ready = True

    async def try_grant(self, schema_name: str, table_name: str | None = None) -> None:
        """Best-effort read access to one schema, and optionally one table.

        Runs in its own transaction so a failure cannot roll back the schema or
        table it follows.

        Logged at warning, not debug: a pod that silently loses this grant
        answers every ``query.execute`` with "permission denied for schema",
        and nothing else in the system says why.
        """
        role = self._role()
        try:
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
        except Exception:  # noqa: BLE001
            logger.warning(
                'datastore.query_role.grant.degraded',
                schema_name=schema_name,
                table_name=table_name,
                exc_info=True,
            )

    async def backfill_grants(self) -> None:
        """Grant read access across all existing pod schemas.

        Idempotent; covers pods whose schemas/tables were created before the
        role mechanism existed. Safe to run at every startup, but it is the
        repair path — schemas and tables grant on creation.
        """
        await self.ensure_role()
        role = self._role()
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
