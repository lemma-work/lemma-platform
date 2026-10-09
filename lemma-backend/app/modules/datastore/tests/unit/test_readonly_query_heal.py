"""A legacy pod schema heals on the query that finds it unreadable, once.

Pod schemas are born readable by the query role now. Those created before that
are repaired here instead of by a boot-time sweep over every schema, so this
path must heal exactly when the role truly lacks access, retry the query
exactly once, and never turn an ordinary bad query into a grant.

The real Postgres behaviour -- which errors a schema without ``USAGE`` actually
produces -- is pinned by ``test_query_role_access_e2e.py``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from uuid import uuid4

import pytest
from sqlalchemy.exc import DBAPIError

from app.modules.datastore.domain.errors import DatastoreDomainError
from app.modules.datastore.domain.row_security import RowPrincipal
from app.modules.datastore.infrastructure.readonly_query import (
    execute_readonly_query,
)

_PLAN = [{"Plan": {"Node Type": "Result", "Total Cost": 0.01, "Plan Rows": 1}}]


class _DriverError(DBAPIError):
    def __init__(self, sqlstate: str, message: str):
        super().__init__("EXPLAIN", {}, Exception(message))
        self.orig = type("_Orig", (), {"sqlstate": sqlstate})()


class _Result:
    def __init__(self, settings: tuple[str, ...] = ()):
        self._settings = settings

    def scalar_one(self):
        return _PLAN

    def one(self):
        return self._settings


class _Stream:
    def __init__(self):
        self._rows = iter([{"one": 1}])

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            row = next(self._rows)
        except StopIteration:
            raise StopAsyncIteration from None
        return type("_Row", (), {"_mapping": row})()

    async def close(self) -> None:
        return None


@dataclass
class _SchemaManager:
    """The pod's datastore as the query path sees it.

    ``refusals`` is how many planning attempts the database refuses with
    ``refusal`` before answering; ``lacks_access`` is what the catalog says.
    """

    refusal: tuple[str, str] | None = None
    refusals: int = 0
    lacks_access: bool = True
    heal_works: bool = True
    runs: int = 0
    heals: list[str] = field(default_factory=list)
    ensured: int = 0
    principal: RowPrincipal | None = None

    def get_schema_name(self, pod_id) -> str:
        return f"pod_{str(pod_id).replace('-', '_')}"

    async def ensure_query_role(self) -> bool:
        self.ensured += 1
        return True

    async def heal_query_role_access(self, schema_name: str) -> bool:
        if not self.lacks_access:
            return False
        self.heals.append(schema_name)
        if self.heal_works:
            self.lacks_access = False
        return self.heal_works

    async def set_rls_context(self, session, principal: RowPrincipal) -> None:
        self.principal = principal

    def session_factory(self):
        manager = self

        class _Session:
            async def execute(self, statement, params=None):
                if str(statement).startswith("EXPLAIN"):
                    manager.runs += 1
                    if manager.refusal is not None and manager.refusals > 0:
                        manager.refusals -= 1
                        raise _DriverError(*manager.refusal)
                if "current_setting" in str(statement) and manager.principal:
                    return _Result(tuple(manager.principal.settings().values()))
                return _Result()

            async def stream(self, statement):
                return _Stream()

        @asynccontextmanager
        async def session():
            yield _Session()

        return session()


_NO_USAGE = ("42P01", 'relation "widgets" does not exist')
_DENIED = ("42501", "permission denied for table widgets")


async def _query(manager: _SchemaManager):
    return await execute_readonly_query(
        manager,
        uuid4(),
        "SELECT * FROM widgets",
        RowPrincipal.for_user(uuid4()),
    )


@pytest.mark.parametrize("refusal", [_NO_USAGE, _DENIED])
async def test_an_unreadable_legacy_schema_heals_and_the_query_runs(refusal) -> None:
    manager = _SchemaManager(refusal=refusal, refusals=1)

    rows, count, truncated = await _query(manager)

    assert (rows, count, truncated) == ([{"one": 1}], 1, False)
    assert len(manager.heals) == 1
    assert manager.runs == 2


async def test_a_table_that_does_not_exist_is_not_healed_and_not_retried() -> None:
    """The catalog says the role can see everything: it is a typo."""
    manager = _SchemaManager(refusal=_NO_USAGE, refusals=1, lacks_access=False)

    with pytest.raises(DatastoreDomainError):
        await _query(manager)

    assert manager.heals == []
    assert manager.runs == 1


async def test_a_heal_that_did_not_help_is_not_retried_again() -> None:
    """Exactly one retry. The second refusal is the answer."""
    manager = _SchemaManager(refusal=_DENIED, refusals=99)

    with pytest.raises(DatastoreDomainError):
        await _query(manager)

    assert len(manager.heals) == 1
    assert manager.runs == 2


async def test_a_failed_heal_surfaces_the_original_refusal() -> None:
    manager = _SchemaManager(refusal=_DENIED, refusals=99, heal_works=False)

    with pytest.raises(DatastoreDomainError):
        await _query(manager)

    assert manager.runs == 1


async def test_an_unrelated_error_is_not_a_reason_to_heal() -> None:
    manager = _SchemaManager(refusal=("22012", "division by zero"), refusals=1)

    with pytest.raises(DatastoreDomainError):
        await _query(manager)

    assert manager.heals == []
    assert manager.runs == 1


async def test_a_healed_schema_is_not_healed_again() -> None:
    manager = _SchemaManager(refusal=_NO_USAGE, refusals=1)

    await _query(manager)
    await _query(manager)

    assert len(manager.heals) == 1
    assert manager.runs == 3


async def test_the_role_is_ensured_before_the_first_query() -> None:
    manager = _SchemaManager()

    await _query(manager)

    assert manager.ensured == 1
