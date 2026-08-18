"""Count the statements a piece of work issues.

Query counts are the right unit for the class of regression these tests guard
against: an extra round trip per item looks like nothing at the call site and
like everything under load, and unlike milliseconds it does not depend on the
hardware CI happens to provide. A change that *moves* a query rather than
removing it should not look like a win, so this counts statements rather than
sessions.
"""

from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator

from sqlalchemy import event
from sqlalchemy.engine import Engine


@contextmanager
def counted_queries() -> Iterator[list[str]]:
    """Collect every statement executed on any engine while the block runs.

    Listening on the ``Engine`` *class* rather than on a particular engine is
    load-bearing. The application engine is a module singleton while the e2e
    fixtures drive their own, and the datastore engine is a third — so a
    listener attached to one of them can observe nothing at all and leave an
    assertion of the form ``statements == []`` passing for the wrong reason.
    """
    statements: list[str] = []

    def before(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(Engine, "before_cursor_execute", before)
    try:
        yield statements
    finally:
        event.remove(Engine, "before_cursor_execute", before)


def statements_touching(statements: list[str], table: str) -> list[str]:
    """The subset of *statements* naming *table*."""
    return [statement for statement in statements if table in statement]


def format_statements(statements: list[str], *, limit: int = 20) -> str:
    shown = statements[:limit]
    lines = [f"  - {statement.strip()[:160]}" for statement in shown]
    if len(statements) > limit:
        lines.append(f"  ... and {len(statements) - limit} more")
    return "\n".join(lines)
