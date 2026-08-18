"""The lazy index build must not be able to hold a request open.

``ensure_record_index`` runs inside a live request the first time any process
touches a table. A plain ``CREATE INDEX`` takes a SHARE lock for the whole
build, and the advisory lock it sits under has no timeout of its own — so
without these bounds the first caller after a deploy waits out the build on
exactly the biggest tables, with every writer queued behind it.

There is no assertion here about the index being *created*; that is covered
against a real database in ``test_record_indexes_e2e``. What is pinned here is
that the guards are issued at all, and issued before the lock is taken, because
that ordering is what makes them effective and it is invisible at the call site.
"""

from __future__ import annotations

import pytest

from app.modules.datastore.infrastructure import record_indexes

pytestmark = pytest.mark.unit


class _RecordingConn:
    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(self, statement, *args, **kwargs):
        self.statements.append(str(statement))
        return None


class _RecordingEngine:
    def __init__(self, conn: _RecordingConn) -> None:
        self._conn = conn

    def begin(self):
        conn = self._conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


async def _noop_lock(conn, schema_name: str) -> None:
    await conn.execute(f"-- advisory lock on {schema_name}")


async def _ensure(memo=None) -> list[str]:
    conn = _RecordingConn()
    await record_indexes.ensure_record_index(
        _RecordingEngine(conn),
        "pod_x",
        "invoices",
        primary_key_column="id",
        has_created_at=True,
        enable_rls=True,
        lock=_noop_lock,
        memo=set() if memo is None else memo,
    )
    return conn.statements


async def test_the_build_bounds_how_long_it_can_wait_and_run() -> None:
    statements = await _ensure()
    joined = "\n".join(statements)

    assert "lock_timeout" in joined, (
        "the lazy index build can wait forever for the table lock; on a large "
        "table that stalls the request that triggered it"
    )
    assert "statement_timeout" in joined, (
        "the lazy index build can run without bound while holding a SHARE "
        "lock, which blocks every writer to that table"
    )


async def test_the_bounds_are_set_before_the_lock_is_taken() -> None:
    """Ordering is the whole point.

    A timeout set after the advisory lock is acquired cannot bound the wait for
    that lock, which is the wait most likely to be long.
    """
    statements = await _ensure()
    lock_at = next(i for i, s in enumerate(statements) if "advisory lock" in s)
    timeouts_at = [i for i, s in enumerate(statements) if "_timeout" in s]

    assert timeouts_at, "no timeouts were issued at all"
    assert max(timeouts_at) < lock_at, (
        f"timeouts were set at {timeouts_at} but the lock was taken at "
        f"{lock_at}; a bound set after the wait cannot limit it"
    )


async def test_a_table_is_only_built_once_per_process() -> None:
    """The memo is what keeps this off the hot path after the first read."""
    memo: set = set()
    first = await _ensure(memo)
    second = await _ensure(memo)

    assert first, "the first call issued nothing"
    assert second == [], (
        f"the second call re-issued {len(second)} statement(s); the build must "
        "be remembered, or every listing pays for it"
    )


async def test_a_failed_build_does_not_retry_on_every_read() -> None:
    """A permissions or disk problem must not become per-request overhead.

    The read works without the index, so a build that cannot succeed should
    step aside quietly rather than adding a failing statement to every listing.
    """
    from sqlalchemy.exc import DBAPIError

    class _FailingConn(_RecordingConn):
        async def execute(self, statement, *args, **kwargs):
            self.statements.append(str(statement))
            if "CREATE INDEX" in str(statement):
                raise DBAPIError("CREATE INDEX", None, Exception("denied"))

    conn = _FailingConn()
    memo: set = set()
    for _ in range(3):
        await record_indexes.ensure_record_index(
            _RecordingEngine(conn),
            "pod_x",
            "invoices",
            primary_key_column="id",
            has_created_at=True,
            enable_rls=True,
            lock=_noop_lock,
            memo=memo,
        )

    attempts = [s for s in conn.statements if "CREATE INDEX" in s]
    assert len(attempts) == 1, (
        f"a failing build was retried {len(attempts)} times; it must be tried "
        "once per process and then left alone"
    )
