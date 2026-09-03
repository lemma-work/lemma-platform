"""A guarded record update, and what it answers when the guard fails.

`record.update` had no version, ETag or `If-Match` and emitted
``UPDATE ... WHERE pk = :id`` with no predicate on prior state, so two clients
that read a row and each patched the same field both succeeded and the later
one silently won. The record API is what apps and agents bind to, and a row
edited from a surface and a UI at once lost an edit with no error anywhere.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.datastore.domain.datastore_entities import (
    ColumnSchema,
    DatastoreDataType,
    DatastoreTableEntity,
)
from app.modules.datastore.domain.errors import (
    DatastoreConflictError,
    DatastoreRecordNotFoundError,
    DatastoreValidationError,
)
from app.modules.datastore.infrastructure.record_repository import (
    DatastoreRecordRepository,
)
from app.modules.datastore.services.table_context import TableContext

_READ_AT = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


def _context(*, with_updated_at: bool = True) -> TableContext:
    columns = [
        ColumnSchema(name="id", type=DatastoreDataType.TEXT, required=True),
        ColumnSchema(name="status", type=DatastoreDataType.TEXT),
    ]
    if with_updated_at:
        columns.append(
            ColumnSchema(
                name="updated_at",
                type=DatastoreDataType.DATETIME,
                auto=True,
                system=True,
            )
        )
    table = DatastoreTableEntity(
        pod_id=uuid4(),
        table_name="tickets",
        primary_key_column="id",
        columns=columns,
        enable_rls=False,
    )
    return TableContext.from_table_entity(table, "pod_test", events_enabled=False)


class _Result:
    def __init__(self, row: object | None) -> None:
        self._row = row

    def fetchone(self) -> object | None:
        return self._row


class _Session:
    """Answers the update, then the existence probe behind a failed guard."""

    def __init__(self, *, rows: list[object | None]) -> None:
        self.rows = list(rows)
        self.statements: list[tuple[str, dict]] = []

    async def execute(self, statement, params=None):
        self.statements.append((str(statement), dict(params or {})))
        return _Result(self.rows.pop(0) if self.rows else None)

    async def commit(self) -> None:
        return None


def _repository(session: _Session) -> DatastoreRecordRepository:
    @asynccontextmanager
    async def _session_factory():
        yield session

    return DatastoreRecordRepository(
        SimpleNamespace(session_factory=_session_factory, set_rls_context=None)
    )


def _updated_row(**overrides) -> object:
    mapping = {"id": "t-1", "status": "done", "updated_at": _READ_AT, **overrides}
    return SimpleNamespace(_mapping=mapping)


class TestTheGuardReachesTheStatement:
    @pytest.mark.asyncio
    async def test_the_expected_timestamp_becomes_a_where_predicate(self) -> None:
        session = _Session(rows=[_updated_row()])

        await _repository(session).update_record(
            _context(),
            "t-1",
            {"status": "done"},
            uuid4(),
            enforce_user_scope=False,
            expected_updated_at=_READ_AT,
        )

        sql, params = session.statements[0]
        assert '"updated_at" = :expected_updated_at' in sql
        assert params["expected_updated_at"] == _READ_AT

    @pytest.mark.asyncio
    async def test_an_unguarded_update_is_unchanged(self) -> None:
        """Last-writer-wins stays the default: the guard is opt-in, so an
        existing caller sends the same statement it always did."""
        session = _Session(rows=[_updated_row()])

        await _repository(session).update_record(
            _context(),
            "t-1",
            {"status": "done"},
            uuid4(),
            enforce_user_scope=False,
        )

        sql, params = session.statements[0]
        assert "expected_updated_at" not in sql
        assert "expected_updated_at" not in params


class TestAFailedGuardSaysWhichFailureItWas:
    """A deleted row and a row somebody else edited both match nothing, and the
    caller has to do different things about them."""

    @pytest.mark.asyncio
    async def test_a_row_that_moved_on_is_a_conflict(self) -> None:
        # The update matches nothing; the probe behind it finds the row.
        session = _Session(rows=[None, SimpleNamespace(_mapping={"?column?": 1})])

        with pytest.raises(DatastoreConflictError, match="changed since it was read"):
            await _repository(session).update_record(
                _context(),
                "t-1",
                {"status": "done"},
                uuid4(),
                enforce_user_scope=False,
                expected_updated_at=_READ_AT,
            )

    @pytest.mark.asyncio
    async def test_a_row_that_is_gone_is_still_a_404(self) -> None:
        session = _Session(rows=[None, None])

        with pytest.raises(DatastoreRecordNotFoundError):
            await _repository(session).update_record(
                _context(),
                "t-1",
                {"status": "done"},
                uuid4(),
                enforce_user_scope=False,
                expected_updated_at=_READ_AT,
            )

    @pytest.mark.asyncio
    async def test_an_unguarded_miss_asks_nothing_extra(self) -> None:
        """The probe is on the guarded failure path only; a plain update that
        matched nothing must not pay for a second round trip."""
        session = _Session(rows=[None])

        with pytest.raises(DatastoreRecordNotFoundError):
            await _repository(session).update_record(
                _context(),
                "t-1",
                {"status": "done"},
                uuid4(),
                enforce_user_scope=False,
            )

        assert len(session.statements) == 1


class TestATableWithNoTimestampRefusesTheGuard:
    @pytest.mark.asyncio
    async def test_the_guard_is_refused_rather_than_ignored(self) -> None:
        """A concurrency check that silently does nothing is worse than none:
        the caller believes their write was checked."""
        session = _Session(rows=[_updated_row()])

        with pytest.raises(DatastoreValidationError, match="no 'updated_at' column"):
            await _repository(session).update_record(
                _context(with_updated_at=False),
                "t-1",
                {"status": "done"},
                uuid4(),
                enforce_user_scope=False,
                expected_updated_at=_READ_AT,
            )

        assert session.statements == []


class TestTheProbeSeesTheSameRowsTheUpdateCould:
    """The probe rebuilds the row scope rather than reusing the update's, and
    the two must not drift: a probe that ignored RLS would report another
    member's row as a conflict rather than as absent.
    """

    def _rls_context(self) -> TableContext:
        table = DatastoreTableEntity(
            pod_id=uuid4(),
            table_name="tickets",
            primary_key_column="id",
            columns=[
                ColumnSchema(name="id", type=DatastoreDataType.TEXT, required=True),
                ColumnSchema(name="status", type=DatastoreDataType.TEXT),
                ColumnSchema(
                    name="updated_at",
                    type=DatastoreDataType.DATETIME,
                    auto=True,
                    system=True,
                ),
                ColumnSchema(
                    name="user_id",
                    type=DatastoreDataType.UUID,
                    required=True,
                    auto=True,
                    system=True,
                ),
            ],
            enable_rls=True,
        )
        return TableContext.from_table_entity(table, "pod_test", events_enabled=False)

    @pytest.mark.asyncio
    async def test_the_probe_carries_the_row_scope(self) -> None:
        session = _Session(rows=[None, None])
        user_id = uuid4()
        schema_manager = SimpleNamespace(
            session_factory=None, set_rls_context=AsyncMock()
        )

        @asynccontextmanager
        async def _session_factory():
            yield session

        schema_manager.session_factory = _session_factory

        with pytest.raises(DatastoreRecordNotFoundError):
            await DatastoreRecordRepository(schema_manager).update_record(
                self._rls_context(),
                "t-1",
                {"status": "done"},
                user_id,
                expected_updated_at=_READ_AT,
            )

        probe_sql, probe_params = session.statements[-1]
        assert probe_sql.startswith("SELECT 1")
        assert '"user_id" = :current_user_id' in probe_sql
        assert probe_params["current_user_id"] == str(user_id)
        assert "expected_updated_at" not in probe_sql
