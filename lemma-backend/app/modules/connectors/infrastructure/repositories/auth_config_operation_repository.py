"""Storage for operations discovered against one install.

Re-discovery is an upsert followed by a scoped delete, in one transaction. The
obvious alternative -- delete everything for the install, then insert the new
set -- loses the entire operation list the moment one insert fails, because the
delete has already happened. That is not hypothetical: two tools whose names
normalize to the same slug collide on the unique index, and the install is left
with nothing.
"""

from __future__ import annotations

from typing import Any, Sequence
from uuid import UUID, uuid7

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert

from app.modules.connectors.domain.connector_operation import InstallOperationEntity
from app.modules.connectors.infrastructure.models.auth_config_operation import (
    AuthConfigOperation,
)


class AuthConfigOperationRepository:
    def __init__(self, session):
        self.session = session

    async def list_by_auth_config(
        self,
        auth_config_id: UUID,
        *,
        search_query: str | None = None,
        limit: int | None = None,
    ) -> Sequence[InstallOperationEntity]:
        stmt = select(AuthConfigOperation).where(
            AuthConfigOperation.auth_config_id == auth_config_id
        )
        if search_query:
            pattern = f"%{search_query.strip().lower()}%"
            stmt = stmt.where(
                or_(
                    func.lower(AuthConfigOperation.name).like(pattern),
                    func.lower(func.coalesce(AuthConfigOperation.description, "")).like(
                        pattern
                    ),
                )
            )
        stmt = stmt.order_by(AuthConfigOperation.name.asc())
        if limit is not None:
            stmt = stmt.limit(limit)
        result = await self.session.execute(stmt)
        return [row.to_entity() for row in result.scalars().all()]

    async def get_by_auth_config_and_name(
        self, auth_config_id: UUID, name: str
    ) -> InstallOperationEntity | None:
        stmt = select(AuthConfigOperation).where(
            AuthConfigOperation.auth_config_id == auth_config_id,
            func.lower(AuthConfigOperation.name) == name.strip().lower(),
        )
        result = await self.session.execute(stmt)
        row = result.scalars().first()
        return row.to_entity() if row else None

    async def replace_for_auth_config(
        self,
        *,
        auth_config_id: UUID,
        organization_id: UUID,
        operations: list[dict[str, Any]],
    ) -> int:
        """Make the stored set match ``operations`` exactly, atomically.

        Upsert first, then delete whatever is no longer present. Ordered that
        way on purpose: if anything fails, the transaction rolls back and the
        install keeps the operation set it had, rather than being left empty.
        """
        if operations:
            rows = [
                {
                    "id": uuid7(),
                    "auth_config_id": auth_config_id,
                    "organization_id": organization_id,
                    "name": op["name"],
                    "provider_operation_name": op.get("provider_operation_name"),
                    "display_name": op.get("display_name"),
                    "description": op.get("description"),
                    "search_document": op.get("search_document"),
                    "input_schema": op.get("input_schema"),
                    "output_schema": op.get("output_schema"),
                    "execution": op["execution"],
                    "created_at": func.now(),
                    "updated_at": func.now(),
                }
                for op in operations
            ]
            statement = insert(AuthConfigOperation).values(rows)
            await self.session.execute(
                statement.on_conflict_do_update(
                    index_elements=["auth_config_id", "name"],
                    set_={
                        "provider_operation_name": statement.excluded.provider_operation_name,
                        "display_name": statement.excluded.display_name,
                        "description": statement.excluded.description,
                        "search_document": statement.excluded.search_document,
                        "input_schema": statement.excluded.input_schema,
                        "output_schema": statement.excluded.output_schema,
                        "execution": statement.excluded.execution,
                        "updated_at": func.now(),
                    },
                )
            )

        keep = [op["name"] for op in operations]
        removal = delete(AuthConfigOperation).where(
            AuthConfigOperation.auth_config_id == auth_config_id
        )
        if keep:
            removal = removal.where(AuthConfigOperation.name.not_in(keep))
        await self.session.execute(removal)
        return len(operations)

    async def delete_by_auth_config(self, auth_config_id: UUID) -> int:
        result = await self.session.execute(
            delete(AuthConfigOperation).where(
                AuthConfigOperation.auth_config_id == auth_config_id
            )
        )
        return int(result.rowcount or 0)
