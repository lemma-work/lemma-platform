"""The query role must survive both a weak app role and a contended catalog.

An app role that is only a *member* of an already-provisioned query role is the
least privilege this mechanism can run on, and it is what a managed Postgres
deployment tends to hand out. These tests pin that case: every privileged
statement is probed before it is issued, so the common no-op costs two catalog
lookups instead of an ``insufficient_privilege`` error.

The second half pins the other way a grant goes missing. ``GRANT`` updates a
catalog row, and two sessions granting on the same schema at once leave one of
them with a transient error instead of the grant. That one is retried; a
genuine refusal still degrades on the first answer.
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import DBAPIError

from app.modules.datastore.config import datastore_settings
from app.modules.datastore.infrastructure import query_role as query_role_module
from app.modules.datastore.infrastructure.query_role import (
    QueryRoleGrants,
    _is_transient_conflict,
)

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

    def __init__(
        self,
        *,
        role_exists: bool,
        is_member: bool,
        forbid: tuple = (),
        fail: object = None,
        fail_on: str = "GRANT",
        fail_times: int = 0,
    ):
        self.role_exists = role_exists
        self.is_member = is_member
        self.forbid = forbid
        self.fail = fail
        self.fail_on = fail_on
        self.fail_times = fail_times
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
        if self.fail is not None and self.fail_times and self.fail_on in sql:
            self.fail_times -= 1
            raise self.fail()
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


def _times_issued(connection: _Connection, fragment: str) -> int:
    return sum(fragment in statement for statement in connection.statements)


class _Conflict(DBAPIError):
    """A ``GRANT`` that lost a catalog race, shaped the way asyncpg reports it.

    `tuple concurrently updated` carries no SQLSTATE of its own — PostgreSQL
    raises it from a bare ``elog`` — so it reaches us as an internal error whose
    message is the only thing that identifies it.
    """

    def __init__(self):
        super().__init__(
            'GRANT USAGE ON SCHEMA "pod_abc"',
            {},
            Exception("tuple concurrently updated"),
        )


class _Deadlock(DBAPIError):
    """40P01, reported through ``orig.sqlstate`` rather than the message."""

    def __init__(self):
        super().__init__('GRANT USAGE ON SCHEMA "pod_abc"', {}, Exception("deadlock"))
        self.orig = type("_Orig", (), {"sqlstate": "40P01"})()


class _Refusal(DBAPIError):
    """A permanent answer: no retry can turn this one into a grant."""

    def __init__(self):
        super().__init__(
            'GRANT USAGE ON SCHEMA "pod_abc"',
            {},
            Exception("permission denied for schema pod_abc"),
        )
        self.orig = type("_Orig", (), {"sqlstate": "42501"})()


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    """The retry is bounded by attempts, not by wall clock; don't pay for it."""
    monkeypatch.setattr(query_role_module, "_GRANT_BACKOFF_SECONDS", 0.0)


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


class TestTransientConflictDetection:
    """A retry that fires on the wrong error hides a real misconfiguration."""

    def test_a_lost_catalog_race_is_recognised(self) -> None:
        assert _is_transient_conflict(_Conflict()) is True

    def test_a_deadlock_is_recognised_by_sqlstate(self) -> None:
        assert _is_transient_conflict(_Deadlock()) is True

    def test_a_denied_grant_is_not_transient(self) -> None:
        assert _is_transient_conflict(_Refusal()) is False

    def test_an_error_with_no_driver_detail_is_not_transient(self) -> None:
        assert _is_transient_conflict(PermissionError("permission denied")) is False


@pytest.mark.asyncio
async def test_a_grant_that_lost_a_catalog_race_is_retried() -> None:
    """The regression: two workers granting at once, and one grant vanishes.

    ``try_grant`` swallowed the conflict and logged a warning, so the role never
    received USAGE and the pod answered every ``query.execute`` with "permission
    denied for table <x>" — somewhere else entirely, and much later.
    """
    connection = _Connection(
        role_exists=True,
        is_member=True,
        fail=_Conflict,
        fail_on="GRANT USAGE",
        fail_times=1,
    )

    await _grants(connection).try_grant("pod_abc", "widgets")

    assert (
        _times_issued(connection, f'GRANT USAGE ON SCHEMA "pod_abc" TO "{ROLE}"') == 2
    )
    assert _issued(connection, f'GRANT SELECT ON "pod_abc"."widgets" TO "{ROLE}"')


@pytest.mark.asyncio
async def test_a_deadlocked_grant_is_retried() -> None:
    connection = _Connection(
        role_exists=True,
        is_member=True,
        fail=_Deadlock,
        fail_on="GRANT USAGE",
        fail_times=2,
    )

    await _grants(connection).try_grant("pod_abc")

    assert _times_issued(connection, "GRANT USAGE ON SCHEMA") == 3


@pytest.mark.asyncio
async def test_a_denied_grant_degrades_without_retrying() -> None:
    """Waiting out a privilege the app role does not have helps nobody."""
    connection = _Connection(
        role_exists=True,
        is_member=True,
        fail=_Refusal,
        fail_on="GRANT USAGE",
        fail_times=99,
    )

    await _grants(connection).try_grant("pod_abc")

    assert _times_issued(connection, "GRANT USAGE ON SCHEMA") == 1


@pytest.mark.asyncio
async def test_an_unending_conflict_still_degrades_rather_than_retrying_forever() -> (
    None
):
    """Bounded. A pod creation must not block on a catalog that stays contended."""
    connection = _Connection(
        role_exists=True,
        is_member=True,
        fail=_Conflict,
        fail_on="GRANT USAGE",
        fail_times=99,
    )

    await _grants(connection).try_grant("pod_abc")

    assert _times_issued(connection, "GRANT USAGE ON SCHEMA") == 4


@pytest.mark.asyncio
async def test_backfill_retries_a_lost_catalog_race() -> None:
    """One transaction covers every pod schema, so one lost race loses them all."""
    connection = _Connection(
        role_exists=True,
        is_member=True,
        fail=_Conflict,
        fail_on="FOR s IN SELECT nspname",
        fail_times=1,
    )

    await _grants(connection).backfill_grants()

    assert _times_issued(connection, "FOR s IN SELECT nspname FROM pg_namespace") == 2


@pytest.mark.asyncio
async def test_backfill_still_surfaces_a_conflict_it_cannot_clear() -> None:
    """Startup logs this one; swallowing it here would leave nothing to log."""
    connection = _Connection(
        role_exists=True,
        is_member=True,
        fail=_Conflict,
        fail_on="FOR s IN SELECT nspname",
        fail_times=99,
    )

    with pytest.raises(DBAPIError):
        await _grants(connection).backfill_grants()
