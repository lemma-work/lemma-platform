"""Tables open to people outside the pod, for the modules that serve them.

``agent_surfaces`` asks what a visitor's page may fill and adds their row;
``agent`` tells a visitor's run which tables it may help fill. Neither learns
anything else about the pod's tables. See ``domain/public_rows`` for the rules
and ``services/public_rows`` for how a row is added.

The service layer is imported where it is called, so naming these types costs
the importer nothing.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.datastore.domain.public_rows import (
    OpenTable,
    PublicAudience,
    PublicColumn,
    PublicRowRefused,
    PublicRowsClosed,
    public_values,
)

__all__ = [
    "OpenTable",
    "PublicAudience",
    "PublicColumn",
    "PublicRowRefused",
    "PublicRowsClosed",
    "add_visitor_row",
    "open_table_names",
    "public_values",
    "visitor_table",
]


async def visitor_table(uow, *, pod_id: UUID, table_name: str) -> OpenTable | None:
    """What a page outside the pod may fill of a table, or ``None`` if nothing."""
    from app.modules.datastore.services.public_rows import visitor_table as read

    return await read(uow, pod_id=pod_id, table_name=table_name)


async def open_table_names(uow, *, pod_id: UUID) -> list[tuple[str, PublicAudience]]:
    """The pod's tables that take rows from outside, and from whom."""
    from app.modules.datastore.services.public_rows import open_tables

    return await open_tables(uow, pod_id=pod_id)


async def add_visitor_row(
    uow_factory: UnitOfWorkFactory,
    *,
    pod_id: UUID,
    table_name: str,
    answers: dict[str, object],
    contact_id: UUID | None,
) -> None:
    """Add one row from outside; see ``services/public_rows.add_visitor_row``."""
    from app.modules.datastore.services.public_rows import add_visitor_row as add

    await add(
        uow_factory,
        pod_id=pod_id,
        table_name=table_name,
        answers=answers,
        contact_id=contact_id,
    )
