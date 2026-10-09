"""The rules for marking a table contact-owned, and the change itself.

Kept beside ``TableService`` rather than in it: what a contact-owned table may
be -- never per-user, never Public, always with an explicit list of what a
contact may read -- is one idea, applied the same way on create and on update.

Never Public, because Public is read by anybody the pod's bot answers, and a
contact-owned table is the one place a pod keeps rows *about* those people.
Its rows reach a contact only through the row policy and the column list.
"""

from __future__ import annotations

from app.core.authorization.context import ResourceVisibility
from app.modules.datastore.domain.datastore_entities import (
    ColumnSchema,
    DatastoreDataType,
    DatastoreTableEntity,
    materialize_table_columns,
)
from app.modules.datastore.domain.errors import (
    DatastoreDomainError,
    DatastoreInfrastructureError,
    DatastoreValidationError,
)
from app.modules.datastore.domain.ports import DatastoreSchemaPort
from app.modules.datastore.domain.row_security import CONTACT_COLUMN
from app.modules.datastore.infrastructure.contact_rows import set_contact_owned

CONTACT_OWNED_AND_RLS = (
    "A table is either per-user (each member sees their own rows) or "
    "contact-owned (every member sees every contact's rows), not both"
)
CONTACT_OWNED_AND_PUBLIC = (
    "A contact-owned table can't be Public: its rows are about the people a "
    "Public table is shown to"
)


def refuse_conflicts(*, per_user: bool, contact_owned: bool, visibility: str) -> None:
    """Refuse a table that would end up per-user and contact-owned, or Public."""
    if not contact_owned:
        return
    if per_user:
        raise DatastoreValidationError(CONTACT_OWNED_AND_RLS)
    if visibility == ResourceVisibility.PUBLIC.value:
        raise DatastoreValidationError(CONTACT_OWNED_AND_PUBLIC)


def with_contact_column(columns: list[ColumnSchema]) -> list[ColumnSchema]:
    """The columns with ``contact_id`` declared: a plain, writable UUID the pod fills.

    A table that already has one keeps it, and it must be a UUID.
    """
    existing = next((c for c in columns if c.name == CONTACT_COLUMN), None)
    if existing is None:
        return [
            *columns,
            ColumnSchema(name=CONTACT_COLUMN, type=DatastoreDataType.UUID),
        ]
    if existing.type is not DatastoreDataType.UUID:
        raise DatastoreValidationError(
            f"A contact-owned table's '{CONTACT_COLUMN}' column must be a UUID"
        )
    return columns


def resolve_contact_columns(
    columns: list[ColumnSchema],
    *,
    contact_owned: bool,
    chosen: list[str] | None,
    current: list[str],
) -> list[str]:
    """What a contact may read once this change lands, checked.

    ``None`` keeps the current choice. Required, and non-empty, for a
    contact-owned table -- like an open table's columns, the choice is the
    member's to make, so nothing defaults to every column.
    """
    if not contact_owned:
        if chosen:
            raise DatastoreValidationError(
                "contact_columns applies only to a contact-owned table"
            )
        return []
    picked = list(current if chosen is None else chosen)
    if not picked:
        raise DatastoreValidationError(
            "Choose the columns a contact may read of their own rows (contact_columns)"
        )
    if len(set(picked)) != len(picked):
        raise DatastoreValidationError("Each contact column can be chosen once")
    known = {column.name for column in columns}
    unknown = [name for name in picked if name not in known]
    if unknown:
        raise DatastoreValidationError(
            f"The table has no column {', '.join(unknown)} to show contacts"
        )
    return picked


async def apply_contact_owned(
    schema: DatastoreSchemaPort, table: DatastoreTableEntity, *, enable: bool
) -> None:
    """Turn the contact policy on or off, and keep the stored columns true.

    Turning it off leaves row security on while the table is per-user: the
    policy that remains still needs it.
    """
    if enable:
        table.columns = with_contact_column(table.columns)
    try:
        await set_contact_owned(
            schema,
            table.pod_id,
            table.table_name,
            enable=enable,
            per_user=table.enable_rls,
        )
    except DatastoreDomainError:
        raise
    except Exception as exc:
        raise DatastoreInfrastructureError(
            "Failed to change whether the table is contact-owned"
        ) from exc
    table.contact_owned = enable


async def apply_row_policies(
    schema: DatastoreSchemaPort,
    table: DatastoreTableEntity,
    *,
    enable_rls: bool | None,
    contact_owned: bool | None,
    contact_columns: list[str] | None,
) -> None:
    """Change per-user isolation and contact ownership, off before on.

    Both policies live on the same table switch -- row security -- so the order
    decides where a PATCH that swaps one for the other ends up. Turning one off
    disables row security and turning the other on enables it again; the other
    way round, the second step switched off what the first had just switched on.
    """
    per_user = table.enable_rls if enable_rls is None else enable_rls
    owned = table.contact_owned if contact_owned is None else contact_owned
    refuse_conflicts(
        per_user=per_user, contact_owned=owned, visibility=table.visibility
    )
    chosen = resolve_contact_columns(
        with_contact_column(table.columns) if owned else table.columns,
        contact_owned=owned,
        chosen=contact_columns,
        current=table.contact_columns,
    )
    if table.contact_owned and not owned:
        await apply_contact_owned(schema, table, enable=False)
    if per_user != table.enable_rls:
        await _set_per_user(schema, table, per_user)
    if owned and not table.contact_owned:
        await apply_contact_owned(schema, table, enable=True)
    table.contact_columns = chosen


async def _set_per_user(
    schema: DatastoreSchemaPort, table: DatastoreTableEntity, enable: bool
) -> None:
    try:
        await schema.set_table_rls(table.pod_id, table.table_name, enable)
    except DatastoreDomainError:
        raise
    except Exception as exc:
        raise DatastoreInfrastructureError(
            "Failed to toggle row-level security"
        ) from exc
    table.enable_rls = enable
    # Re-derive system columns so the stored schema matches the physical table
    # (user_id appears only while RLS is on).
    table.columns = materialize_table_columns(
        table.primary_key_column,
        [column for column in table.columns if not column.system],
        enable_rls=enable,
    )
