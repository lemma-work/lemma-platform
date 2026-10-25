"""Each pod's query role must survive a weak app role and a contended catalog.

Ad-hoc SQL runs as a role of the pod's own (``pod_query_role_name``), which can
reach that pod's schema and no other. An app role that is only a *member* of an
already-provisioned role must not be asked to create it again: every privileged
statement is probed before it is issued, so the common no-op costs two catalog
lookups instead of an ``insufficient_privilege`` error.

The second half pins healing a pod schema that predates access by
construction. ``GRANT`` updates a catalog row, and two sessions healing the same
schema at once leave one of them with a transient error instead of the grant.
That one is retried; a genuine refusal still degrades on the first answer.
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import DBAPIError

from app.modules.datastore.changes.schema import CHANGES_TABLE, SUBSCRIBERS_TABLE
from app.modules.datastore.config import datastore_settings
from app.modules.datastore.infrastructure import pod_schema_memo
from app.modules.datastore.infrastructure import query_role as query_role_module
from app.modules.datastore.infrastructure.query_role import (
    WITHHELD_TABLES,
    QueryRoleGrants,
    _is_transient_conflict,
    drop_pod_role,
    pod_query_role_name,
    schema_access_statements,
    withdraw_shared_role,
)
from app.modules.datastore.infrastructure.sql_identifiers import MAX_IDENTIFIER_BYTES

SHARED_ROLE = datastore_settings.datastore_query_role
POD_SCHEMA = "pod_0b7c2a4e_1f3d_4c5b_9a8e_7d6c5b4a3f2e"
OTHER_POD_SCHEMA = "pod_9f8e7d6c_5b4a_4c3d_8e2f_1a0b9c8d7e6f"
ROLE = pod_query_role_name(POD_SCHEMA)


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

    return QueryRoleGrants(SimpleNamespace(begin=begin, connect=begin))


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


@pytest.fixture(autouse=True)
def _fresh_process():
    """The role memo is process-wide; each test starts as a new process."""
    pod_schema_memo.forget_all()
    yield
    pod_schema_memo.forget_all()


class TestPodRoleNames:
    """The role is the boundary, so its name must be one per pod, always."""

    def test_each_pod_schema_has_a_role_of_its_own(self) -> None:
        assert pod_query_role_name(POD_SCHEMA) != pod_query_role_name(OTHER_POD_SCHEMA)
        assert pod_query_role_name(POD_SCHEMA) != SHARED_ROLE

    def test_the_name_is_the_configured_base_then_the_pod(self) -> None:
        assert ROLE == f"{SHARED_ROLE}_0b7c2a4e1f3d4c5b9a8e7d6c5b4a3f2e"

    def test_a_long_base_gives_way_to_the_pod_and_never_truncates_it(
        self, monkeypatch
    ) -> None:
        """PostgreSQL truncates past 63 bytes silently; two pods sharing a
        63-byte prefix would share a role."""
        monkeypatch.setattr(datastore_settings, "datastore_query_role", "q" * 80)

        name = pod_query_role_name(POD_SCHEMA)

        assert len(name.encode()) <= MAX_IDENTIFIER_BYTES
        assert name.endswith("_0b7c2a4e1f3d4c5b9a8e7d6c5b4a3f2e")
        assert name != pod_query_role_name(OTHER_POD_SCHEMA)

    @pytest.mark.parametrize("name", ["public", "pod_abc", 'pod_x"; DROP ROLE q'])
    def test_only_a_pod_schema_has_one(self, name: str) -> None:
        with pytest.raises(ValueError):
            pod_query_role_name(name)

    def test_the_withheld_tables_are_the_change_logs(self) -> None:
        assert set(WITHHELD_TABLES) == {CHANGES_TABLE, SUBSCRIBERS_TABLE}


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

    await _grants(connection).ensure_role(POD_SCHEMA)

    assert not _issued(connection, "CREATE ROLE")
    assert not _issued(connection, "TO CURRENT_USER")


@pytest.mark.asyncio
async def test_a_missing_role_is_created_and_granted() -> None:
    connection = _Connection(role_exists=False, is_member=False)

    await _grants(connection).ensure_role(POD_SCHEMA)

    assert _issued(connection, f'CREATE ROLE "{ROLE}"')
    assert _issued(connection, "duplicate_object")
    assert _issued(connection, f'GRANT "{ROLE}" TO CURRENT_USER')


@pytest.mark.asyncio
async def test_membership_is_granted_when_the_role_already_exists() -> None:
    """Provisioned by an operator, not yet reachable by the app role."""
    connection = _Connection(role_exists=True, is_member=False)

    await _grants(connection).ensure_role(POD_SCHEMA)

    assert not _issued(connection, "CREATE ROLE")
    assert _issued(connection, f'GRANT "{ROLE}" TO CURRENT_USER')


@pytest.mark.asyncio
async def test_membership_is_probed_for_set_role_not_inherited_privileges() -> None:
    """Ad-hoc queries enter the role via ``SET LOCAL ROLE``. USAGE would report
    success on a role that cannot be entered, and from PostgreSQL 16 so would
    MEMBER: a CREATEROLE app role is made a member of what it creates, without
    the SET option. 'SET' is asked there, 'MEMBER' only before 16."""
    connection = _Connection(role_exists=True, is_member=True)

    await _grants(connection).ensure_role(POD_SCHEMA)

    probe = next(s for s in connection.statements if "pg_has_role" in s)
    assert "server_version_num')::int >= 160000" in probe
    assert "'SET'" in probe and "'MEMBER'" in probe


@pytest.mark.asyncio
async def test_a_role_that_cannot_be_ensured_degrades_instead_of_raising() -> None:
    """Pods and tables must still be creatable where the role cannot be."""

    class _Denied(DBAPIError):
        def __init__(self):
            super().__init__("CREATE ROLE", {}, Exception("permission denied"))

    connection = _Connection(
        role_exists=False, is_member=False, fail=_Denied, fail_on="CREATE ROLE"
    )
    connection.fail_times = 1

    assert await _grants(connection).try_ensure_role(POD_SCHEMA) is False


def test_a_new_schema_is_born_readable_and_so_is_every_later_table() -> None:
    """Per-schema default privileges, not a database-wide one, to the pod's role.

    A database-wide ``ON TABLES`` default would also give the query role every
    platform table a later migration creates whenever the datastore shares the
    platform database.
    """
    usage, defaults = schema_access_statements(POD_SCHEMA)

    assert SHARED_ROLE != ROLE and f'"{SHARED_ROLE}"' not in usage + defaults
    assert usage == f'GRANT USAGE ON SCHEMA "{POD_SCHEMA}" TO "{ROLE}"'
    assert f'IN SCHEMA "{POD_SCHEMA}"' in defaults
    assert "FOR ROLE CURRENT_USER" in defaults
    assert defaults.endswith(f'GRANT SELECT ON TABLES TO "{ROLE}"')


@pytest.mark.asyncio
async def test_healing_grants_one_pod_schema_its_tables_and_its_future() -> None:
    connection = _Connection(role_exists=True, is_member=True)

    assert await _grants(connection).heal_schema(POD_SCHEMA) is True

    assert _issued(connection, f'GRANT USAGE ON SCHEMA "{POD_SCHEMA}"')
    assert _issued(connection, f'GRANT SELECT ON ALL TABLES IN SCHEMA "{POD_SCHEMA}"')
    assert _issued(connection, f'IN SCHEMA "{POD_SCHEMA}" GRANT SELECT ON TABLES')
    assert _issued(connection, f'TO "{ROLE}"')
    assert not _issued(connection, "pg_namespace")


@pytest.mark.asyncio
async def test_healing_withholds_the_change_log_it_just_granted() -> None:
    """``GRANT ... ON ALL TABLES`` includes the change log, which carries rows a
    per-user policy hides. A heal used to leave it readable."""
    connection = _Connection(role_exists=True, is_member=True)

    assert await _grants(connection).heal_schema(POD_SCHEMA) is True

    grant = next(i for i, s in enumerate(connection.statements) if "ON ALL TABLES" in s)
    withhold = next(
        i
        for i, s in enumerate(connection.statements)
        if f'REVOKE ALL ON "{POD_SCHEMA}".reserved_changes FROM "{ROLE}"' in s
    )
    assert grant < withhold
    assert _issued(connection, f'"{POD_SCHEMA}".reserved_change_subscribers FROM')


@pytest.mark.asyncio
async def test_healing_creates_the_pods_role_when_it_is_missing() -> None:
    """A schema from before per-pod roles has none; its first query makes it."""
    connection = _Connection(role_exists=False, is_member=False)

    assert await _grants(connection).heal_schema(POD_SCHEMA) is True

    assert _issued(connection, f'CREATE ROLE "{ROLE}"')
    assert _issued(connection, f'GRANT USAGE ON SCHEMA "{POD_SCHEMA}" TO "{ROLE}"')


@pytest.mark.asyncio
async def test_a_missing_role_is_missing_access_without_asking_its_privileges() -> None:
    """``has_schema_privilege`` raises on a role that does not exist."""
    connection = _Connection(role_exists=False, is_member=False)

    assert await _grants(connection).missing_access(POD_SCHEMA) is True

    assert not _issued(connection, "has_schema_privilege")


@pytest.mark.asyncio
async def test_the_withheld_change_log_is_not_mistaken_for_missing_access() -> None:
    """Otherwise every typo in a pod with a change log would trigger a heal."""
    connection = _Connection(role_exists=True, is_member=True)

    await _grants(connection).missing_access(POD_SCHEMA)

    privileges = next(s for s in connection.statements if "has_table_privilege" in s)
    assert "NOT IN ('reserved_changes', 'reserved_change_subscribers')" in privileges


@pytest.mark.asyncio
async def test_a_dropped_schema_takes_its_role_with_it() -> None:
    connection = _Connection(role_exists=True, is_member=True)

    await drop_pod_role(connection, POD_SCHEMA)

    assert _issued(connection, f'DROP ROLE IF EXISTS "{ROLE}"')


@pytest.mark.asyncio
async def test_a_role_that_was_never_made_is_not_dropped() -> None:
    """``DROP ROLE`` checks CREATEROLE before existence: a deployment that could
    never create the role would otherwise fail every schema drop."""
    connection = _Connection(role_exists=False, is_member=False, forbid=("DROP ROLE",))

    await drop_pod_role(connection, POD_SCHEMA)

    assert not _issued(connection, "DROP ROLE")


@pytest.mark.asyncio
async def test_the_shared_role_loses_the_schema_its_tables_and_their_future() -> None:
    """The migration step: what the shared role was given, taken back."""
    connection = _Connection(role_exists=True, is_member=True)

    await withdraw_shared_role(connection, POD_SCHEMA)

    assert _issued(
        connection,
        f'REVOKE ALL ON ALL TABLES IN SCHEMA "{POD_SCHEMA}" FROM "{SHARED_ROLE}"',
    )
    assert _issued(
        connection,
        f'IN SCHEMA "{POD_SCHEMA}" REVOKE ALL ON TABLES FROM "{SHARED_ROLE}"',
    )
    assert _issued(
        connection, f'REVOKE ALL ON SCHEMA "{POD_SCHEMA}" FROM "{SHARED_ROLE}"'
    )


@pytest.mark.asyncio
async def test_a_deployment_that_never_had_the_shared_role_revokes_nothing() -> None:
    connection = _Connection(role_exists=False, is_member=False, forbid=("REVOKE",))

    await withdraw_shared_role(connection, POD_SCHEMA)

    assert not _issued(connection, "REVOKE")


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["public", "pod_abc", "pod_x; DROP ROLE q"])
async def test_healing_refuses_anything_that_is_not_a_pod_schema(name: str) -> None:
    connection = _Connection(role_exists=True, is_member=True)

    assert await _grants(connection).heal_schema(name) is False
    assert await _grants(connection).missing_access(name) is False

    assert connection.statements == []


@pytest.mark.asyncio
async def test_the_catalog_is_probed_once_per_process() -> None:
    connection = _Connection(role_exists=True, is_member=True)
    grants = _grants(connection)

    await grants.ensure_role(POD_SCHEMA)
    await grants.ensure_role(POD_SCHEMA)

    assert sum("pg_roles" in s for s in connection.statements) == 1


@pytest.mark.asyncio
async def test_a_dropped_schema_is_probed_again() -> None:
    """Both drop paths forget the schema, so a recreated pod gets its role back."""
    connection = _Connection(role_exists=True, is_member=True)
    grants = _grants(connection)

    await grants.ensure_role(POD_SCHEMA)
    pod_schema_memo.forget(POD_SCHEMA)
    await grants.ensure_role(POD_SCHEMA)

    assert sum("pg_roles" in s for s in connection.statements) == 2


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
async def test_a_heal_that_lost_a_catalog_race_is_retried() -> None:
    """Two queries healing one schema at once, and one grant vanishes.

    Swallowing the conflict would leave the pod refusing every query until the
    next heal, which is the failure the heal exists to end.
    """
    connection = _Connection(
        role_exists=True,
        is_member=True,
        fail=_Conflict,
        fail_on="GRANT USAGE",
        fail_times=1,
    )

    assert await _grants(connection).heal_schema(POD_SCHEMA) is True

    assert _times_issued(connection, f'GRANT USAGE ON SCHEMA "{POD_SCHEMA}"') == 2
    assert _issued(connection, "GRANT SELECT ON ALL TABLES IN SCHEMA")


@pytest.mark.asyncio
async def test_a_deadlocked_heal_is_retried() -> None:
    connection = _Connection(
        role_exists=True,
        is_member=True,
        fail=_Deadlock,
        fail_on="GRANT USAGE",
        fail_times=2,
    )

    assert await _grants(connection).heal_schema(POD_SCHEMA) is True

    assert _times_issued(connection, "GRANT USAGE ON SCHEMA") == 3


@pytest.mark.asyncio
async def test_a_denied_heal_degrades_without_retrying() -> None:
    """Waiting out a privilege the app role does not have helps nobody."""
    connection = _Connection(
        role_exists=True,
        is_member=True,
        fail=_Refusal,
        fail_on="GRANT USAGE",
        fail_times=99,
    )

    assert await _grants(connection).heal_schema(POD_SCHEMA) is False

    assert _times_issued(connection, "GRANT USAGE ON SCHEMA") == 1


@pytest.mark.asyncio
async def test_an_unending_conflict_still_degrades_rather_than_retrying_forever() -> (
    None
):
    """Bounded. A query must not block on a catalog that stays contended."""
    connection = _Connection(
        role_exists=True,
        is_member=True,
        fail=_Conflict,
        fail_on="GRANT USAGE",
        fail_times=99,
    )

    assert await _grants(connection).heal_schema(POD_SCHEMA) is False

    assert _times_issued(connection, "GRANT USAGE ON SCHEMA") == 4
