"""USER-typed cell values, and confirming each names a real user."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.modules.datastore.domain.datastore_entities import DatastoreDataType
from app.modules.datastore.domain.errors import DatastoreValidationError
from app.modules.datastore.services.record_validator import convert_record
from app.modules.datastore.services.table_context import TableContext
from app.modules.identity.contracts import UserReader


def user_references(
    users: UserReader | None, ctx: TableContext, data: dict[str, Any]
) -> list[tuple[str, UUID]]:
    """Every USER-typed value in one row, as ``(column, user id)``.

    In the row's own column order, because that is the order the error
    below reports in: the column a person is told about should be the first
    one they would find reading their own file.

    Empty when there is no user directory to check against. Collecting is
    the half that costs something -- it converts the row -- and a caller
    that cannot validate should not pay for it.
    """
    if users is None:
        return []
    references: list[tuple[str, UUID]] = []
    for key, value in convert_record(ctx.columns, data, skip_auto=False).items():
        column = ctx.get_column(key)
        if column is None or column.type != DatastoreDataType.USER or value is None:
            continue
        references.append((key, value if isinstance(value, UUID) else UUID(str(value))))
    return references


async def reject_unknown_user_references(
    users: UserReader | None, references: list[tuple[str, UUID]]
) -> None:
    """Confirm every collected reference names a real user, in one read.

    A dedup set made this cheap for the batch that names one person over
    and over, and left the batch that names a different person per row
    exactly as expensive -- one round trip per distinct id, which for an
    import is one per row. Asking which of the ids exist answers both in a
    single statement.

    Deactivated and deleted users still count as existing, as they did when
    this was ``user_repository.get``: a USER column records who something
    belongs to, and someone leaving must not make an old row unwritable.
    """
    if users is None or not references:
        return
    existing = await users.existing_ids({user_id for _, user_id in references})
    for key, user_id in references:
        if user_id not in existing:
            raise DatastoreValidationError(f"User does not exist for column '{key}'")
