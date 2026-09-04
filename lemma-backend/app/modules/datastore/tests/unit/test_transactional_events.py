from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import DBAPIError

import app.modules.datastore.infrastructure.session as session_module
from app.modules.datastore.domain.events import DatastoreFileCreatedEvent
from app.modules.datastore.infrastructure.transactional_events import (
    ensure_datastore_event_outbox,
    reset_datastore_event_outbox_state,
    stage_domain_events,
)


@pytest.mark.asyncio
async def test_stage_domain_events_uses_one_bulk_insert() -> None:
    session = AsyncMock()
    events = [
        DatastoreFileCreatedEvent(
            file_id=uuid4(),
            pod_id=uuid4(),
            path=f"/document-{index}.md",
        )
        for index in range(3)
    ]

    await stage_domain_events(session, events)

    # One insert carrying every row -- the property under test. Staging also
    # notifies the dispatcher in this transaction, so count the inserts rather
    # than the statements: three events must not mean three inserts.
    inserts = [
        call
        for call in session.execute.await_args_list
        if len(call.args) > 1 and isinstance(call.args[1], list)
    ]
    assert len(inserts) == 1
    rows = inserts[0].args[1]
    assert {row["id"] for row in rows} == {event.event_id for event in events}
    assert (
        sum(
            "pg_notify" in str(call.args[0]) for call in session.execute.await_args_list
        )
        == 1
    )


@pytest.mark.asyncio
async def test_the_outbox_statement_does_not_depend_on_the_batch_size() -> None:
    """One compiled statement for every batch size.

    Rendering the rows inline with ``.values(list)`` makes the SQL a function of
    how many rows there are, so the statement cache misses on every batch size
    it has not seen and recompiles from scratch. That cost lands on the event
    loop inside the write transaction, holding the row locks of the write that
    produced the events -- measured once at 1027ms.

    Asserting the compiled SQL is identical across batch sizes pins the
    property rather than the spelling: any future rewrite is free, as long as it
    does not put the row count back into the statement.
    """

    async def compile_for(count: int) -> str:
        session = AsyncMock()
        await stage_domain_events(
            session,
            [
                DatastoreFileCreatedEvent(
                    file_id=uuid4(), pod_id=uuid4(), path=f"/d-{index}.md"
                )
                for index in range(count)
            ],
        )
        return str(session.execute.await_args.args[0])

    assert await compile_for(1) == await compile_for(50)


@pytest.mark.asyncio
async def test_stage_domain_events_skips_empty_batches() -> None:
    session = AsyncMock()

    await stage_domain_events(session, [])

    session.execute.assert_not_awaited()


class TestTheOutboxBootstrapIsSafeAcrossProcesses:
    """`CREATE TABLE ... IF NOT EXISTS` is not race-free in PostgreSQL: two
    transactions can both observe the table as absent and one then loses on the
    catalog's unique index. The guard here was a module-global flag and an
    `asyncio.Lock`, which coordinate one event loop -- and the callers are two
    API replicas or two workers booting together, where the loser's failure is
    a replica that refuses to start.
    """

    @pytest.fixture(autouse=True)
    def _forget_the_bootstrap(self):
        # The "already done" flag is a module global; leaving it set decides
        # what a later test in this process sees.
        reset_datastore_event_outbox_state()
        yield
        reset_datastore_event_outbox_state()

    def _engine(self, statements: list[str], *, create_raises=None):
        class _Connection:
            async def execute(self, statement, params=None):
                statements.append(str(statement))

            async def run_sync(self, function, **kwargs):
                statements.append("create_table")
                if create_raises is not None:
                    raise create_raises

        @asynccontextmanager
        async def _begin():
            yield _Connection()

        return SimpleNamespace(begin=_begin)

    @pytest.mark.asyncio
    async def test_the_create_is_serialized_by_an_advisory_lock(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        statements: list[str] = []
        monkeypatch.setattr(
            session_module, "get_datastore_engine", lambda: self._engine(statements)
        )

        await ensure_datastore_event_outbox()

        assert "pg_advisory_xact_lock" in statements[0]
        assert statements[1] == "create_table"

    @pytest.mark.asyncio
    async def test_losing_the_race_is_not_a_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Something outside this lock's reach -- a migration, an operator --
        can also create the table, and a replica must not refuse to boot over a
        table that exists."""
        duplicate = DBAPIError("CREATE TABLE", {}, Exception("duplicate key value"))
        duplicate.orig.sqlstate = "42P07"  # type: ignore[attr-defined]
        statements: list[str] = []
        monkeypatch.setattr(
            session_module,
            "get_datastore_engine",
            lambda: self._engine(statements, create_raises=duplicate),
        )

        await ensure_datastore_event_outbox()

        # It tried, and it treated the loss as a success -- a second call does
        # not go back to the database.
        assert statements[-1] == "create_table"
        await ensure_datastore_event_outbox()
        assert statements.count("create_table") == 1

    @pytest.mark.asyncio
    async def test_any_other_failure_still_stops_startup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The lifespan hook exists to fail the boot: record mutation must never
        degrade to best-effort publication after the commit."""
        refused = DBAPIError("CREATE TABLE", {}, Exception("permission denied"))
        refused.orig.sqlstate = "42501"  # type: ignore[attr-defined]
        monkeypatch.setattr(
            session_module,
            "get_datastore_engine",
            lambda: self._engine([], create_raises=refused),
        )

        with pytest.raises(DBAPIError):
            await ensure_datastore_event_outbox()
