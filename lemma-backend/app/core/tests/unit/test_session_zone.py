"""Every connection to the platform database reads and writes in UTC.

A naive time cast to ``timestamp with time zone`` is read in the session's
zone. Left to the database or role default, the same write lands on a
different instant on a server set to another zone; pinned per connection, it
cannot. That the pin has this effect against a database set to another zone
is shown by ``test_run_times_migration_e2e``.
"""

from __future__ import annotations

import pytest

from app.core.infrastructure.db import session

pytestmark = pytest.mark.unit


def test_every_connection_is_pinned_to_utc() -> None:
    server_settings = session._build_connect_args()["server_settings"]

    assert server_settings["timezone"] == "UTC"
    assert session.SESSION_ZONE == {"timezone": "UTC"}
