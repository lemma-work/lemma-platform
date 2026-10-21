"""Which constraint the database refused a write on.

A unique index is often the only thing that knows two rows clash -- two
requests racing, or one name asked for twice -- and the answer a person should
get depends on which index it was. These read its name off the error.
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError


def violated_constraint(error: IntegrityError) -> str | None:
    """Which constraint the database refused on, if it said.

    Three places to look, because the driver stack moves it. SQLAlchemy's
    asyncpg dialect does not re-raise asyncpg's own `UniqueViolationError`: it
    translates it into a DBAPI-shaped `IntegrityError` carrying only `sqlstate`,
    and chains the original underneath. So `error.orig` is the translation and
    `error.orig.__cause__` is the exception that actually knows the name.
    `.diag` is psycopg's spelling, checked because the migration and test
    tooling reach Postgres through it.

    `None` means the database did not say. A caller treats that as "not the
    constraint I handle", which is the safe direction: an integrity failure
    nobody expected reaches the caller rather than being explained away.
    """
    original = error.orig
    for candidate in (original, getattr(original, "__cause__", None)):
        named = getattr(candidate, "constraint_name", None)
        if isinstance(named, str) and named:
            return named
        named = getattr(getattr(candidate, "diag", None), "constraint_name", None)
        if isinstance(named, str) and named:
            return named
    return None
