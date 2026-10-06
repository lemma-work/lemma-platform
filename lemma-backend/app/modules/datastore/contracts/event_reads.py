"""Reading one row as a person, for something that tells them it exists.

An outside subscriber is told about a row only if the person it acts for could
read that row now -- not when they subscribed. So the row is read at delivery,
through the same table permission and row-level scope the API applies, and
``None`` means "do not tell them": no such table any more, no such row, or not
theirs to see.
"""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.context import Context
from app.core.domain.errors import DomainError
from app.modules.datastore.api.dependencies import (
    build_record_service,
    build_table_service,
    get_schema_manager,
)
from app.modules.datastore.domain.errors import (
    DatastoreRecordNotFoundError,
    DatastoreTableNotFoundError,
)
from app.modules.datastore.services.table_context import TableContext


async def read_record_as(
    uow,
    *,
    pod_id: UUID,
    table_name: str,
    record_id: str,
    user_id: UUID,
    ctx: Context,
) -> dict[str, object] | None:
    try:
        table = await build_table_service(uow).get_table(pod_id, table_name, ctx)
        record = await build_record_service(uow).get_record(
            TableContext.from_table_entity(
                table, get_schema_manager().get_schema_name(pod_id)
            ),
            record_id,
            user_id,
        )
    except DatastoreTableNotFoundError, DatastoreRecordNotFoundError:
        return None
    except DomainError as exc:
        if exc.status_code in (401, 403, 404):
            return None
        raise
    if record is None:
        return None
    return dict(record.data)
