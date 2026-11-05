"""A record create that is safe to repeat: its id is derived from a key.

An agent turn interrupted between writing a record and recording the tool's
return re-runs the call when it resumes, under the same call id. Keyed on that
id, the second create lands on the first one's row: the id is
``uuid5(key)`` when the table generates UUID ids, and the insert returns the
row already there instead of failing on the primary key. A table whose key the
caller supplies is idempotent the same way, by its own key; one with a serial
key cannot be, and is not made to look as though it were.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid5

from app.modules.datastore.domain.datastore_entities import DatastoreDataType
from app.modules.datastore.services.table_context import TableContext

#: Fixed, so a key maps to the same id in every process and every release.
_KEY_NAMESPACE = UUID("9c1f6f4e-2d8b-5b8e-9a51-6f0c6f2a7e11")


def keyed_id(idempotency_key: str) -> str:
    return str(uuid5(_KEY_NAMESPACE, idempotency_key))


def keyed_primary_key(
    ctx: TableContext, data: dict[str, Any], idempotency_key: str
) -> dict[str, Any]:
    """``data`` with its primary key derived from ``idempotency_key`` when it can be."""
    pk = ctx.primary_key_column
    if data.get(pk) is not None:
        return data
    column = ctx.get_column(pk)
    if column is None or column.type is not DatastoreDataType.UUID:
        return data
    return {**data, pk: keyed_id(idempotency_key)}


__all__ = ["keyed_id", "keyed_primary_key"]
