"""Fail a test that holds a pooled connection across non-database work.

Opt-in, by naming the fixture. Deliberately not autouse:

* ``session-scope-baseline.json`` still lists known violations, so an autouse
  gate would be red on day one and would grow an opt-out flag within a week —
  at which point the flag becomes the default and the gate means nothing.
* The e2e harness holds sessions across blocking fixture work on purpose, which
  is exactly why ``scripts/check_session_scope.py`` excludes ``tests`` and
  ``test_support``. A runtime gate should not re-introduce what the static gate
  deliberately leaves out.

Reporting happens at check-in, which fires synchronously inside the test's own
execution — so there is no polling, no interval, and nothing to flake.

Usage::

    async def test_import_does_not_hold_a_connection(strict_connection_scope, ...):
        ...
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.core.observability import connection_scope
from app.core.observability.connection_scope import ConnectionScopeMonitor

# Tighter than the production default: a test should not hold a connection for
# a fifth of a second, and a tight threshold is what makes the gate useful on a
# fast machine. Loose enough to survive a loaded CI runner.
STRICT_IDLE_HOLD_SECONDS = 0.2


@pytest.fixture
def strict_connection_scope() -> Iterator[ConnectionScopeMonitor]:
    """Fail this test if it holds a pooled connection while not querying.

    Works under ``NullPool`` — the testing default — because checkout and
    check-in fire there exactly as they do on a real pool. No engine juggling
    is needed, so do not "fix" this by flipping ``settings.environment``.
    """
    from app.core.infrastructure.db.session import get_engine

    monitor = connection_scope.start_connection_scope_monitor(
        idle_hold_seconds=STRICT_IDLE_HOLD_SECONDS, strict=True
    )
    # The engines are usually built before this fixture runs, so attach here
    # rather than relying on construction-time wiring.
    monitor.attach(get_engine())
    try:
        from app.modules.datastore.infrastructure.session import get_datastore_engine

        monitor.attach(get_datastore_engine())
    except Exception:  # pragma: no cover - datastore is optional in some suites
        pass

    try:
        yield monitor
    finally:
        connection_scope.stop_connection_scope_monitor()

    if monitor.violations:
        report = "\n\n".join(hold.render() for hold in monitor.violations)
        pytest.fail(
            f"{len(monitor.violations)} pooled connection(s) held across "
            f"non-database work:\n\n{report}",
            pytrace=False,
        )
