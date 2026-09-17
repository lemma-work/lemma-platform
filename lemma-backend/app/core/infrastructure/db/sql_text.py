"""Text predicates a btree can actually answer.

Prefix matching is the one string test PostgreSQL will serve from an index, and
it is easy to write in a way that silently cannot be. Both halves of that are
here so a caller gets them together.

``escape_like`` used to live in ``datastore/infrastructure/sql_identifiers``,
whose other helpers all raise datastore domain errors. This one raises nothing
and knows nothing about files; it moved here when app releases and function
revisions needed the same rule, because the alternative was a second copy.
"""

from __future__ import annotations

from sqlalchemy import ColumnElement
from sqlalchemy.orm.attributes import InstrumentedAttribute


def escape_like(value: str) -> str:
    """Escape ``%``/``_`` wildcards for a ``LIKE ... ESCAPE '!'`` clause."""
    return value.replace("!", "!!").replace("%", "!%").replace("_", "!_")


def starts_with(column: InstrumentedAttribute[str], prefix: str) -> ColumnElement[bool]:
    """``column LIKE 'prefix%'``, with the prefix taken literally.

    This is the SQL spelling of Python's ``str.startswith``, and the two are
    only equivalent because of the escaping: these prefixes are typed by
    people, and a bare ``%`` in one would turn a lookup into a wildcard search
    that reports unrelated rows as matches.

    Typed to a mapped attribute rather than to anything column-shaped because
    that is what both callers pass and what the index story below assumes: a
    real column on a real table. Widening it is a one-line change the type
    checker will then police.

    An anchored ``LIKE`` is the only prefix form the planner rewrites into an
    index range scan, and it will do that **only** against an index declared
    with ``text_pattern_ops``. A default btree on a text column is ordered by
    the database collation, under which the rows sharing a prefix are not
    contiguous, so the planner cannot use one however the query is written --
    which is why a query like this one can be correct and still read the table.
    """
    return column.like(f"{escape_like(prefix)}%", escape="!")
