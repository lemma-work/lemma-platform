"""The query role must be establishable without the power to create roles.

An app role that is only a *member* of an already-provisioned query role is the
least privilege this mechanism can run on, and it is what a managed Postgres
deployment tends to hand out. These tests pin that case: every privileged
statement is probed before it is issued, so the common no-op costs two catalog
lookups instead of an ``insufficient_privilege`` error.
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.modules.datastore.config import datastore_settings
from app.modules.datastore.infrastructure.query_role import QueryRoleGrants

ROLE = datastore_settings.datastore_query_role


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class _Connection:
    """Records SQL, answers the two catalog probes, and can refuse DDL.

    ``forbid`` models a role without ``CREATEROLE``: PostgreSQL rejects the
    statement outright rather than reporting the object as a duplicate.
    """

    def __init__(self, *, role_exists: bool, is_member: bool, forbid: tuple = ()):
        self.role_exists = role_exists
        self.is_member = is_member
        self.forbid = forbid
        self.statements: list[str] = []

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        for fragment in self.forbid:
            if fragment in sql:
                raise PermissionError(
                    "permission denied to create role\n"
                    "DETAIL: Only roles with the CREATEROLE attribute may "
                    "create roles."
                )
        if "pg_roles" in sql:
            return _Result(1 if self.role_exists else None)
        if "pg_has_role" in sql:
            return _Result(self.is_member)
        return _Result(None)


def _grants(connection: _Connection) -> QueryRoleGrants:
    @asynccontextmanager
    async def begin():
        yield connection

    return QueryRoleGrants(SimpleNamespace(begin=begin))


def _issued(connection: _Connection, fragment: str) -> bool:
    return any(fragment in statement for statement in connection.statements)


@pytest.mark.asyncio
async def test_existing_role_and_membership_issue_no_privileged_statement() -> None:
    """The regression: a provisioned role must not be re-created.

    ``CREATE ROLE`` on an existing role raises ``insufficient_privilege``
    before it can raise ``duplicate_object``, so the exception guard never
    fires and every caller inherits the failure.
    """
    connection = _Connection(
        role_exists=True,
        is_member=True,
        forbid=("CREATE ROLE", "TO CURRENT_USER"),
    )

    await _grants(connection).ensure_role()

    assert not _issued(connection, "CREATE ROLE")
    assert not _issued(connection, "TO CURRENT_USER")


@pytest.mark.asyncio
async def test_a_missing_role_is_created_and_granted() -> None:
    connection = _Connection(role_exists=False, is_member=False)

    await _grants(connection).ensure_role()

    assert _issued(connection, f'CREATE ROLE "{ROLE}"')
    assert _issued(connection, "duplicate_object")
    assert _issued(connection, f'GRANT "{ROLE}" TO CURRENT_USER')


@pytest.mark.asyncio
async def test_membership_is_granted_when_the_role_already_exists() -> None:
    """Provisioned by an operator, not yet reachable by the app role."""
    connection = _Connection(role_exists=True, is_member=False)

    await _grants(connection).ensure_role()

    assert not _issued(connection, "CREATE ROLE")
    assert _issued(connection, f'GRANT "{ROLE}" TO CURRENT_USER')


@pytest.mark.asyncio
async def test_membership_is_probed_for_set_role_not_inherited_privileges() -> None:
    """Ad-hoc queries enter the role via ``SET LOCAL ROLE``, which needs
    MEMBER; USAGE would report success on a role that cannot be entered."""
    connection = _Connection(role_exists=True, is_member=True)

    await _grants(connection).ensure_role()

    assert _issued(connection, "'MEMBER'")


@pytest.mark.asyncio
async def test_backfill_repairs_without_the_power_to_create_roles() -> None:
    """The repair path calls ``ensure_role`` first and used to die there.

    Two code paths were dead for one reason: pods got no grant at creation
    because ``try_grant`` swallowed the failure, and startup never repaired
    them because this one re-raised it.
    """
    connection = _Connection(
        role_exists=True,
        is_member=True,
        forbid=("CREATE ROLE",),
    )

    await _grants(connection).backfill_grants()

    assert _issued(connection, "FOR s IN SELECT nspname FROM pg_namespace")


@pytest.mark.asyncio
async def test_pod_schemas_grant_at_creation_without_the_power_to_create_roles() -> (
    None
):
    connection = _Connection(
        role_exists=True,
        is_member=True,
        forbid=("CREATE ROLE",),
    )

    await _grants(connection).try_grant("pod_abc", "widgets")

    assert _issued(connection, f'GRANT USAGE ON SCHEMA "pod_abc" TO "{ROLE}"')
    assert _issued(connection, f'GRANT SELECT ON "pod_abc"."widgets" TO "{ROLE}"')


@pytest.mark.asyncio
async def test_the_catalog_is_probed_once_per_process() -> None:
    connection = _Connection(role_exists=True, is_member=True)
    grants = _grants(connection)

    await grants.ensure_role()
    await grants.ensure_role()

    assert sum("pg_roles" in s for s in connection.statements) == 1
