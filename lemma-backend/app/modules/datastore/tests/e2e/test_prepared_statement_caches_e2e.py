"""Neither prepared-statement cache survives a column change.

A record read is ``SELECT * FROM "<schema>"."<table>"`` -- the same SQL text
before and after a column is added or removed. A cached prepared statement for
it therefore keeps a result descriptor that no longer matches the table, and
asyncpg raises ``InvalidCachedStatementError``. Nothing catches it, so a person
who adds or removes a column gets a 400 from their own table until the
connection recycles.

**Two caches, and the first attempt at this turned off only one.**
DEV-DATA-004 was closed by setting asyncpg's ``statement_cache_size=0``.
SQLAlchemy's asyncpg dialect keeps a second cache of its own, per DBAPI
connection, defaulting to 100 statements, which that setting does not touch --
so the bug was still there, and the register entry said it was fixed.

Asserted on the connection the engine actually hands out rather than on the
arguments passed to it: the arguments are two dict literals in two branches of
``get_datastore_engine``, and the failure mode this guards against is precisely
somebody setting one of them.

Why this needs a test rather than being obvious from the code: in ``testing``
the datastore engine pools with ``NullPool``, so no connection lives long
enough for a stale cached statement to be reused, and every existing scenario
passed against a booted stack while the bug was live on any real deployment.
That is why this asserts on configuration rather than by reproducing -- the
reproduction is a product scenario run against a real pool
(``make scenarios-desktop``), which is what found it the second time.
"""

from __future__ import annotations

import pytest

from app.modules.datastore.infrastructure.session import get_datastore_engine

pytestmark = pytest.mark.asyncio


async def test_the_datastore_engine_caches_no_prepared_statement():
    engine = get_datastore_engine()
    async with engine.connect() as connection:
        raw = await connection.get_raw_connection()
        holder = getattr(raw, "dbapi_connection", None) or raw

        assert getattr(holder, "_prepared_statement_cache", "missing") is None, (
            "SQLAlchemy's asyncpg dialect is caching prepared statements "
            "(default 100 per connection). Its cache is separate from "
            "asyncpg's and needs prepared_statement_cache_size=0; with it on, "
            "adding or removing a column makes the table answer 400 until the "
            "connection recycles."
        )

        driver = getattr(holder, "_connection", None)
        statement_cache = getattr(driver, "_stmt_cache", None)
        assert getattr(statement_cache, "_max_size", None) == 0, (
            "asyncpg is caching prepared statements; needs "
            "statement_cache_size=0 for the same reason as above."
        )
