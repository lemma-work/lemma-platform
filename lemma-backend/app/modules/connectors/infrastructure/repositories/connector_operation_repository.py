import re
from typing import Optional, Sequence

from sqlalchemy import case, desc, func, or_, select

from app.core.domain.message_bus import MessageBus
from app.core.infrastructure.db.repository import SqlAlchemyRepository
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.connectors.domain.connector_operation import (
    ConnectorOperationEntity,
)
from app.modules.connectors.domain.ports import ConnectorOperationRepositoryPort
from app.modules.connectors.infrastructure.models import ConnectorOperation


def _normalize_search_query(query: str) -> str:
    """Make operation ids and natural-language queries search the same way."""

    return " ".join(re.sub(r"[-_/\\]+", " ", query.strip()).lower().split())


class ConnectorOperationRepository(
    SqlAlchemyRepository[ConnectorOperation, ConnectorOperationEntity],
    ConnectorOperationRepositoryPort,
):
    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        message_bus: MessageBus | None = None,
    ):
        super().__init__(uow, ConnectorOperation, ConnectorOperationEntity)
        if message_bus is not None:
            self.uow.set_message_bus(message_bus)

    async def list_by_connector(
        self,
        connector_id: str,
        search_query: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> Sequence[ConnectorOperationEntity]:
        stmt = select(ConnectorOperation).where(
            ConnectorOperation.connector_id == connector_id
        )

        normalized_query = _normalize_search_query(search_query) if search_query else ""

        if normalized_query:
            search_document = func.concat_ws(
                " ",
                func.coalesce(ConnectorOperation.name, ""),
                func.coalesce(ConnectorOperation.display_name, ""),
                func.coalesce(ConnectorOperation.description, ""),
                func.coalesce(ConnectorOperation.search_document, ""),
            )
            ts_query = func.plainto_tsquery("english", normalized_query)
            ts_vector = func.to_tsvector("english", search_document)
            ts_rank = func.ts_rank_cd(ts_vector, ts_query).label("search_rank")
            lowered_name = func.lower(ConnectorOperation.name)
            lowered_display_name = func.lower(
                func.coalesce(ConnectorOperation.display_name, "")
            )
            exact_match_rank = case(
                (lowered_name == normalized_query, 4),
                (lowered_display_name == normalized_query, 3),
                (ConnectorOperation.name.ilike(f"%{normalized_query}%"), 2),
                (ConnectorOperation.display_name.ilike(f"%{normalized_query}%"), 1),
                else_=0,
            ).label("exact_match_rank")
            tokens = [
                token for token in normalized_query.split(" ") if len(token) >= 2
            ]
            token_match_conditions = [
                or_(
                    ConnectorOperation.name.ilike(f"%{token}%"),
                    ConnectorOperation.display_name.ilike(f"%{token}%"),
                    ConnectorOperation.description.ilike(f"%{token}%"),
                    ConnectorOperation.search_document.ilike(f"%{token}%"),
                )
                for token in tokens
            ]
            stmt = (
                select(ConnectorOperation, exact_match_rank, ts_rank)
                .where(ConnectorOperation.connector_id == connector_id)
                .where(
                    or_(
                        ts_vector.op("@@")(ts_query),
                        ConnectorOperation.name.ilike(f"%{normalized_query}%"),
                        ConnectorOperation.display_name.ilike(f"%{normalized_query}%"),
                        ConnectorOperation.description.ilike(f"%{normalized_query}%"),
                        ConnectorOperation.search_document.ilike(
                            f"%{normalized_query}%"
                        ),
                        *token_match_conditions,
                    )
                )
                .order_by(
                    desc(exact_match_rank),
                    desc(ts_rank),
                    ConnectorOperation.name.asc(),
                )
            )
        else:
            stmt = stmt.order_by(ConnectorOperation.name.asc())
        if limit is not None:
            stmt = stmt.limit(limit)

        result = await self.session.execute(stmt)
        if normalized_query:
            return [row[0].to_entity() for row in result.all()]
        return [instance.to_entity() for instance in result.scalars().all()]

    async def list_by_connector_kind(
        self,
        connector_id: str,
        kind: str,
        search_query: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> Sequence[ConnectorOperationEntity]:
        operations = await self.list_by_connector(
            connector_id,
            search_query=search_query,
            limit=None,
        )
        kind_operations = [
            operation
            for operation in operations
            if getattr(operation.kind, "value", operation.kind) == kind
        ]
        if limit is not None:
            return kind_operations[:limit]
        return kind_operations

    async def get_by_connector_and_name(
        self,
        connector_id: str,
        operation_name: str,
    ) -> Optional[ConnectorOperationEntity]:
        normalized_name = operation_name.strip().lower()
        match_rank = case(
            (ConnectorOperation.name == operation_name, 4),
            (func.lower(ConnectorOperation.name) == normalized_name, 3),
            (ConnectorOperation.provider_operation_name == operation_name, 2),
            (
                func.lower(func.coalesce(ConnectorOperation.provider_operation_name, ""))
                == normalized_name,
                1,
            ),
            else_=0,
        ).label("match_rank")
        stmt = select(ConnectorOperation).where(
            ConnectorOperation.connector_id == connector_id,
            or_(
                func.lower(ConnectorOperation.name) == normalized_name,
                func.lower(func.coalesce(ConnectorOperation.provider_operation_name, ""))
                == normalized_name,
            ),
        ).order_by(
            desc(match_rank),
            ConnectorOperation.name.asc(),
        )
        result = await self.session.execute(stmt)
        instance = result.scalars().first()
        return instance.to_entity() if instance else None

    async def get_by_connector_kind_and_name(
        self,
        connector_id: str,
        kind: str,
        operation_name: str,
    ) -> Optional[ConnectorOperationEntity]:
        normalized_name = operation_name.strip().lower()
        stmt = select(ConnectorOperation).where(
            ConnectorOperation.connector_id == connector_id,
            ConnectorOperation.kind == kind,
            func.lower(ConnectorOperation.name) == normalized_name,
        )
        result = await self.session.execute(stmt)
        instance = result.scalars().first()
        return instance.to_entity() if instance else None

    async def has_operations(self, connector_id: str) -> bool:
        stmt = (
            select(ConnectorOperation.id)
            .where(ConnectorOperation.connector_id == connector_id)
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None
