"""The schema-revision half of `/health/ready`.

The path resolution is the part most likely to break silently: `migrations/`
sits beside `app/` in the repository and in the image, and if it is ever not
found the check answers `unknown` for the rest of the process's life without
saying so.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import OperationalError

from app.core.infrastructure.db import migration_state

pytestmark = pytest.mark.unit


class _FakeConn:
    def __init__(self, revision: str | None) -> None:
        self._revision = revision

    async def scalar(self, *_args, **_kwargs) -> str | None:
        return self._revision

    async def __aenter__(self) -> "_FakeConn":
        return self

    async def __aexit__(self, *_args) -> bool:
        return False


class _FakeEngine:
    def __init__(self, revision: str | None) -> None:
        self._revision = revision

    def connect(self) -> _FakeConn:
        return _FakeConn(self._revision)


class _UnreachableEngine:
    def connect(self):
        raise OperationalError("SELECT version_num", {}, Exception("no route"))


@pytest.fixture(autouse=True)
def _forget_previous_answer():
    """The verdict latches once current, which is the point; tests start clean.

    Through the module's own reset rather than by writing its globals: a test
    that pokes the state it is named after passes a rename it should have
    caught.
    """
    migration_state.forget_cached_schema_state()
    yield
    migration_state.forget_cached_schema_state()


def _database_at(monkeypatch, revision: str | None) -> None:
    monkeypatch.setattr(
        "app.core.infrastructure.db.session.get_engine",
        lambda: _FakeEngine(revision),
    )


def test_the_head_revision_is_found_beside_the_app_package():
    """Resolved from this file, not from the working directory, which differs
    between the API, the worker and a test run."""
    assert migration_state._code_head_revision()


async def test_a_database_at_the_head_revision_is_current(monkeypatch):
    _database_at(monkeypatch, migration_state._code_head_revision())

    assert await migration_state.schema_migration_state() == migration_state.CURRENT


async def test_a_database_behind_the_head_revision_is_pending(monkeypatch):
    _database_at(monkeypatch, "0001_something_much_older")

    assert await migration_state.schema_migration_state() == migration_state.PENDING


async def test_a_database_that_cannot_be_asked_answers_unknown(monkeypatch):
    """No `alembic_version` table, or no database at all. Readiness reports it
    and does not refuse work over it: the `db` component already covers a
    database that is simply broken."""
    monkeypatch.setattr(
        "app.core.infrastructure.db.session.get_engine", _UnreachableEngine
    )

    assert await migration_state.schema_migration_state() == migration_state.UNKNOWN


async def test_the_current_answer_is_latched_and_not_re_read(monkeypatch):
    """A readiness probe runs every few seconds for the life of the process, so
    the query has to stop once its answer cannot change."""
    _database_at(monkeypatch, migration_state._code_head_revision())
    assert await migration_state.schema_migration_state() == migration_state.CURRENT

    monkeypatch.setattr(
        "app.core.infrastructure.db.session.get_engine", _UnreachableEngine
    )

    assert await migration_state.schema_migration_state() == migration_state.CURRENT
