"""The checkout probe that watches for pool exhaustion.

Written because the probe was dead for its entire life and nothing noticed. It
read `connection_record.pool`, which `_ConnectionRecord` does not have -- the
attribute is name-mangled to `_ConnectionRecord__pool` inside the class body --
so every checkout raised `AttributeError` into a handler that swallowed it, and
a `next(count()) == 0` guard meant the warning was emitted once per process and
never again. `_pool_pressure_incident` therefore never recorded, so the
`database_pool_capacity` incident that replaced the old concurrency guardrail
had never once fired.

The pool here is a real `QueuePool` over a stub DBAPI, so the listener is
invoked by SQLAlchemy with the arguments it really passes rather than with
arguments a test invented.
"""

from __future__ import annotations

import pytest
from sqlalchemy import event
from sqlalchemy.pool import QueuePool

from app.core.infrastructure.db.session import _pool_utilization_listener

pytestmark = pytest.mark.unit


class _StubConnection:
    def close(self) -> None:
        return

    def rollback(self) -> None:
        return


def _pool(size: int) -> QueuePool:
    return QueuePool(_StubConnection, pool_size=size, max_overflow=0)


def test_a_checkout_never_reaches_the_error_handler(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The probe must read the pool, not raise into its own `except`.

    Asserted through the warning the handler emits, because that is the only
    outward sign the probe ever gave. The counter that bounds it is reset
    first: it is a module-level `count()` that fires once per process, so a
    test which did not reset it would pass against the broken probe simply
    because something earlier had already consumed the one warning.
    """
    from itertools import count

    from app.core.infrastructure.db import session as session_module

    session_module._pool_probe_failures = count()

    pool = _pool(2)
    event.listen(pool, "checkout", _pool_utilization_listener(pool))

    with caplog.at_level("WARNING"):
        held = pool.connect()
        held.close()

    assert "pool_utilization_probe_failed" not in caplog.text, (
        "the probe raised and swallowed it; it is reading something the "
        "checkout event does not carry"
    )


def test_a_pressured_pool_records_an_incident() -> None:
    """At or above 80% checked out, the incident must actually be recorded.

    Observed through the incident's own failure count rather than by
    substituting its method: the thing under test is that the real object is
    reached at all, and a stand-in in its place would prove only that the test
    can call a test.
    """
    from app.core.infrastructure.db import session as session_module

    incident = session_module._pool_pressure_incident
    incident.record_success()  # start from a clean incident

    pool = _pool(2)
    event.listen(pool, "checkout", _pool_utilization_listener(pool))

    held = []
    try:
        held.append(pool.connect())  # 1/2 = 50%, below the threshold
        assert incident._failure_count == 0
        held.append(pool.connect())  # 2/2 = 100%, at it
        assert incident._failure_count == 1, (
            "pool pressure was never recorded, so nothing can report exhaustion"
        )
    finally:
        incident.record_success()
        for connection in held:
            connection.close()
