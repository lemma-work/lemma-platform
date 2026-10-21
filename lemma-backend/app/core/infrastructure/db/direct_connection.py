"""Arguments for a raw asyncpg connection that lives outside the SQLAlchemy pool.

A ``LISTEN`` registration belongs to one backend, and the pool recycles its
connections (``pool_recycle``), so a pooled listener drops its registration on
recycle and goes quiet without erroring. Anything that has to hold a
connection open -- the work runtime's wake listener -- connects directly with
these arguments instead.

Kept here, not beside one listener, so the listeners that need it do not
depend on each other and either can be deleted on its own.
"""

from __future__ import annotations

import re
from typing import NotRequired, TypedDict
from urllib.parse import parse_qs, urlsplit, urlunsplit

_DRIVER_PREFIX = re.compile(r"^postgresql\+\w+://")


class AsyncpgConnectKwargs(TypedDict):
    """The keyword arguments ``asyncpg.connect`` is called with."""

    dsn: str
    server_settings: dict[str, str]
    ssl: NotRequired[str]


def asyncpg_connect_kwargs(
    database_url: str, *, application_name: str
) -> AsyncpgConnectKwargs:
    """Translate a SQLAlchemy URL into raw asyncpg connect arguments."""
    dsn = _DRIVER_PREFIX.sub("postgresql://", database_url)
    parts = urlsplit(dsn)
    query = parse_qs(parts.query)
    # SQLAlchemy spells this `ssl`; libpq spells it `sslmode`. asyncpg takes
    # either spelling's value as its `ssl` argument, but not as a DSN query
    # parameter, so it has to be lifted out.
    ssl_mode = (query.get("ssl") or query.get("sslmode") or [None])[0]
    kwargs: AsyncpgConnectKwargs = {
        "dsn": urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")),
        # Named so an operator can tell a listener from a pooled connection in
        # pg_stat_activity, which is the first thing you want during a
        # notification-queue incident.
        "server_settings": {
            "application_name": application_name,
            # A listener never opens a transaction. This is a backstop, not a
            # mechanism: a backend that holds LISTEN open inside an idle
            # transaction is the one failure mode that can stall the shared
            # notification queue and start failing NOTIFY for everyone.
            "idle_in_transaction_session_timeout": "60000",
        },
    }
    if ssl_mode:
        kwargs["ssl"] = ssl_mode
    return kwargs


__all__ = ["AsyncpgConnectKwargs", "asyncpg_connect_kwargs"]
