"""A database that cannot be reached is unavailability, not the item failing.

A handler that hits a refused connection, a full server or an empty pool did
nothing wrong; the runtime retries it uncharged, bounded by the kind's
unavailable budget. A deadlock or a statement timeout is still charged.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from pydantic import BaseModel
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from app.core.infrastructure.db.transient_errors import (
    is_database_unavailable,
    is_transient_database_error,
)
from app.core.infrastructure.work import (
    JobContext,
    RetryPolicy,
    WorkClass,
    WorkMode,
    WorkRegistry,
)
from app.core.infrastructure.work.execute import decide
from app.core.infrastructure.work.registry import HandlerSpec
from app.core.infrastructure.work.retry import AttemptState, Dead, Requeue

NOW = datetime(2026, 1, 1, tzinfo=UTC)
FRESH = AttemptState(attempts=0, lease_losses=0, first_unavailable_at=None)


class _State(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


def _db(sqlstate: str) -> OperationalError:
    return OperationalError("SELECT 1", {}, _State(sqlstate))


class _Empty(BaseModel):
    pass


def _spec(budget: timedelta = timedelta(minutes=30)) -> HandlerSpec:
    registry = WorkRegistry()

    @registry.handler(
        kind="test.db",
        cls=WorkClass.AUTOMATION,
        mode=WorkMode.JOB,
        payload=_Empty,
        retry=RetryPolicy(max_attempts=1, unavailable_budget=budget),
    )
    async def _handler(ctx: JobContext, payload: _Empty) -> None:
        return None

    return registry.get("test.db")


UNAVAILABLE = [
    pytest.param(_db("53300"), id="too_many_connections"),
    pytest.param(_db("57P01"), id="admin_shutdown"),
    pytest.param(_db("57P03"), id="cannot_connect_now"),
    pytest.param(_db("08006"), id="connection_failure"),
    pytest.param(PoolTimeoutError("pool exhausted"), id="pool_timeout"),
    pytest.param(InterfaceError("SELECT 1", {}, Exception("closed")), id="interface"),
    pytest.param(ConnectionRefusedError(61, "refused"), id="refused"),
    pytest.param(OSError("Multiple exceptions: refused, refused"), id="bare_oserror"),
    # Raised by the pool's connect, before SQLAlchemy wraps anything.
    pytest.param(
        asyncpg.exceptions.TooManyConnectionsError("too many clients"),
        id="asyncpg_too_many_connections",
    ),
    pytest.param(
        asyncpg.exceptions.ConnectionDoesNotExistError("closed"),
        id="asyncpg_connection_gone",
    ),
]


@pytest.mark.parametrize("exc", UNAVAILABLE)
def test_the_database_being_unavailable_charges_nothing(exc: Exception) -> None:
    assert is_database_unavailable(exc)
    assert is_transient_database_error(exc)

    decision = decide(_spec(), FRESH, exc, NOW)

    assert isinstance(decision, Requeue)
    assert decision.attempts == 0
    assert decision.first_unavailable_at == NOW
    assert decision.error_class == "unavailable:connection_refused"


def test_wrapped_by_a_caller_it_is_still_unavailability() -> None:
    try:
        try:
            raise _db("53300")
        except OperationalError as inner:
            raise RuntimeError("could not load the run") from inner
    except RuntimeError as outer:
        assert is_database_unavailable(outer)


def test_an_outage_past_the_budget_goes_dead_as_unavailable_exhausted() -> None:
    started = NOW - timedelta(minutes=31)
    state = AttemptState(attempts=0, lease_losses=0, first_unavailable_at=started)

    decision = decide(_spec(), state, _db("53300"), NOW)

    assert isinstance(decision, Dead)
    assert decision.reason == "unavailable_exhausted"


@pytest.mark.parametrize(
    "exc",
    [
        pytest.param(_db("40P01"), id="deadlock"),
        pytest.param(_db("57014"), id="statement_timeout"),
        pytest.param(_db("23505"), id="unique_violation"),
        pytest.param(FileNotFoundError("/tmp/x"), id="file_error"),
        pytest.param(TimeoutError(), id="timeout"),
        pytest.param(ValueError("bad"), id="bug"),
    ],
)
def test_anything_else_is_the_item_failing(exc: Exception) -> None:
    assert not is_database_unavailable(exc)

    decision = decide(_spec(), FRESH, exc, NOW)

    assert isinstance(decision, Dead) and decision.reason == "attempts_exhausted"


class _Session:
    async def __aenter__(self) -> "_Session":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def commit(self) -> None:
        return None


async def test_a_booting_worker_waits_for_a_full_database(monkeypatch) -> None:
    """Too many clients at boot is waited out, not an exit and a crash loop."""
    from app.core.infrastructure.work import maintenance, runtime

    calls = {"n": 0}

    async def arm(session, registry, classes) -> int:
        calls["n"] += 1
        if calls["n"] < 3:
            raise _db("53300")
        return 4

    async def no_wait(_seconds: float) -> None:
        return None

    monkeypatch.setattr(maintenance, "arm_periodics", arm)
    monkeypatch.setattr(runtime.asyncio, "sleep", no_wait)
    work = runtime.WorkRuntime(_Session, classes=[WorkClass.EVENTS])

    assert await work._arm_periodics() == 4  # noqa: SLF001
    assert calls["n"] == 3

    async def broken(session, registry, classes) -> int:
        raise _db("42P01")

    monkeypatch.setattr(maintenance, "arm_periodics", broken)
    with pytest.raises(OperationalError):
        await work._arm_periodics()  # noqa: SLF001


SKEW = [
    pytest.param(_db("42703"), id="undefined_column"),
    pytest.param(_db("42P01"), id="undefined_table"),
]


@pytest.mark.parametrize("exc", SKEW)
def test_a_missing_column_mid_rollout_waits_uncharged(exc: Exception) -> None:
    """A release runs new code before its migration finishes, and work queued in
    that window used to spend all three attempts on `UndefinedColumn`."""
    decision = decide(_spec(), FRESH, exc, NOW)

    assert isinstance(decision, Requeue)
    assert decision.attempts == 0
    assert decision.error_class == "unavailable:schema_skew"
    assert decision.first_unavailable_at == NOW


@pytest.mark.parametrize("exc", SKEW)
def test_a_missing_column_past_any_rollout_is_charged(exc: Exception) -> None:
    """A column that is still missing long after a release is a bug, not a
    rollout, and costs the attempt it always did."""
    state = AttemptState(
        attempts=0, lease_losses=0, first_unavailable_at=NOW - timedelta(hours=1)
    )

    decision = decide(_spec(), state, exc, NOW)

    assert isinstance(decision, Dead)
    assert decision.reason == "attempts_exhausted"
