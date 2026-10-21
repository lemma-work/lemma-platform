"""A pod's record reads and writes, as a database role row security binds.

The e2e database connects as a superuser, and a superuser passes every row
policy, ``FORCE ROW LEVEL SECURITY`` or not. A deployment's own role is neither
a superuser nor ``BYPASSRLS``, so there a record read or write that names no
row principal is refused, or matches nothing. On the superuser it works, and
the test passes over the bug: a contact-owned table refused every member write
in a deployment while every e2e test of it passed.

``row_bound_records`` runs the datastore's record sessions as such a role, so a
test sees what a deployment sees.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.modules.datastore.config import datastore_settings
from app.modules.datastore.infrastructure.pod_schema_migrations import (
    schema_name_for_pod,
)
from app.modules.datastore.infrastructure.session import get_datastore_engine
from app.modules.datastore.services.wiring import get_schema_manager


def _grants(schema: str, role: str) -> list[str]:
    """What the API's own role holds on a pod schema, short of owning it.

    Default privileges too, so the change log a first write creates is the
    role's to append to. And the query role, which a contact's read sets
    itself to.
    """
    query_role = datastore_settings.datastore_query_role
    return [
        f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"',
        f'GRANT ALL ON ALL TABLES IN SCHEMA "{schema}" TO "{role}"',
        f'GRANT ALL ON ALL SEQUENCES IN SCHEMA "{schema}" TO "{role}"',
        f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{schema}" GRANT ALL ON TABLES TO "{role}"',
        (
            f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{schema}" '
            f'GRANT ALL ON SEQUENCES TO "{role}"'
        ),
        f'GRANT "{query_role}" TO "{role}"',
    ]


@asynccontextmanager
async def row_bound_records(pod_id: UUID) -> AsyncIterator[None]:
    """Inside, the datastore's record sessions log in as a row-bound role.

    ``NOSUPERUSER NOBYPASSRLS``. Not the tables' owner, as a deployment's role
    is, but row security binds a non-owner the same way ``FORCE`` binds the
    owner. Open it once the pod's schema exists: after its first table.
    """
    schema_manager = get_schema_manager()
    await schema_manager.ensure_query_role()
    role = f"lemma_row_bound_{secrets.token_hex(6)}"
    password = secrets.token_hex(16)
    admin = get_datastore_engine()
    async with admin.begin() as conn:
        await conn.execute(
            text(
                f"CREATE ROLE \"{role}\" LOGIN PASSWORD '{password}' "
                "NOSUPERUSER NOBYPASSRLS"
            )
        )
        for grant in _grants(schema_name_for_pod(pod_id), role):
            await conn.execute(text(grant))
    engine = create_async_engine(
        admin.url.set(username=role, password=password),
        poolclass=NullPool,
        json_serializer=lambda value: json.dumps(value, default=str),
        connect_args={
            "statement_cache_size": 0,
            "prepared_statement_cache_size": 0,
            "server_settings": {"TimeZone": "UTC"},
        },
    )
    usual = schema_manager.session_factory
    schema_manager.session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        yield
    finally:
        schema_manager.session_factory = usual
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP OWNED BY "{role}"'))
            await conn.execute(text(f'DROP ROLE "{role}"'))


__all__ = ["row_bound_records"]
