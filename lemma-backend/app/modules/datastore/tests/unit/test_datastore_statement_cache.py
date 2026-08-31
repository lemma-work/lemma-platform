"""The datastore engine must disable the cache SQLAlchemy actually keeps.

There are two prepared-statement caches in play and they have confusingly
similar names. ``statement_cache_size`` is asyncpg's own.
``prepared_statement_cache_size`` is SQLAlchemy's, which the asyncpg dialect
maintains on top because it prepares every statement itself, and which defaults
to **100 per connection**.

Setting only asyncpg's looks right, passes review, and does nothing: the
dialect's own cache stays at 100. That is what shipped, and the pod schema is
selected by ``SET LOCAL search_path`` rather than by anything in the SQL text —
so two pods whose tables share a name share a cache entry, and a column added
or removed leaves the cached descriptor describing a table that no longer
exists in that shape. In one production day it cost 59 HTTP 500s, every one of
them from ``execute_readonly_query``.

These tests assert the *effective* configuration rather than the intent, which
is the only kind of assertion that could have caught it.
"""

from __future__ import annotations

from sqlalchemy.dialects.postgresql.asyncpg import PGDialect_asyncpg
from sqlalchemy.engine.url import make_url

from app.modules.datastore.infrastructure.session import (
    _build_datastore_connect_args,
)

_SQLALCHEMY_CACHE = "prepared_statement_cache_size"
_ASYNCPG_CACHE = "statement_cache_size"


def _coerced(param: str, value: str) -> object:
    """What the asyncpg dialect makes of a connect argument."""
    url = make_url(f"postgresql+asyncpg://u:p@h/db?{param}={value}")
    _, params = PGDialect_asyncpg().create_connect_args(url)
    return params.get(param)


def test_sqlalchemy_owns_a_second_cache_under_a_different_name() -> None:
    """The distinction the original fix missed.

    The dialect coerces its own knob to an int and leaves asyncpg's as the
    string it arrived as — it does not know that name. Disabling the one it
    does not know leaves its own cache at the default 100.
    """
    assert _coerced(_SQLALCHEMY_CACHE, "0") == 0
    assert _coerced(_ASYNCPG_CACHE, "0") == "0"


def test_the_datastore_engine_disables_both_caches() -> None:
    connect_args = _build_datastore_connect_args()

    assert connect_args[_SQLALCHEMY_CACHE] == 0, (
        "SQLAlchemy's asyncpg dialect prepares every statement and caches 100 "
        "per connection by default; the datastore runs user SQL against tables "
        "users can reshape, so that cache must be off"
    )
    assert connect_args[_ASYNCPG_CACHE] == 0


def test_the_cache_is_disabled_by_value_not_merely_mentioned() -> None:
    """A non-zero value is as good as absent: SQLAlchemy tests truthiness."""
    for key in (_SQLALCHEMY_CACHE, _ASYNCPG_CACHE):
        value = _build_datastore_connect_args()[key]
        assert value == 0 and not isinstance(value, bool)
