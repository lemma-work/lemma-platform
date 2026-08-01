from typing import List, Optional, Sequence, Tuple

from sqlalchemy import func, or_, select

from app.core.domain.message_bus import MessageBus
from app.core.infrastructure.db.repository import SqlAlchemyRepository
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.connectors.domain.connector_trigger import ConnectorTriggerEntity
from app.modules.connectors.domain.ports import ConnectorTriggerRepositoryPort
from app.modules.connectors.infrastructure.models import (
    Connector,
    ConnectorTrigger,
)


class ConnectorTriggerRepository(
    SqlAlchemyRepository[ConnectorTrigger, ConnectorTriggerEntity],
    ConnectorTriggerRepositoryPort,
):
    """Repository for ConnectorTrigger operations."""

    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        message_bus: MessageBus | None = None,
    ):
        super().__init__(uow, ConnectorTrigger, ConnectorTriggerEntity)
        if message_bus is not None:
            self.uow.set_message_bus(message_bus)

    async def get_by_app_and_event(
        self, connector_id: str, event_type: str
    ) -> Optional[ConnectorTriggerEntity]:
        stmt = select(ConnectorTrigger).where(
            ConnectorTrigger.connector_id == connector_id,
            ConnectorTrigger.event_type == event_type,
        )
        result = await self.session.execute(stmt)
        instance = result.scalars().first()
        return instance.to_entity() if instance else None

    async def list_by_connector(
        self, connector_id: str
    ) -> Sequence[ConnectorTriggerEntity]:
        stmt = select(ConnectorTrigger).where(
            ConnectorTrigger.connector_id == connector_id
        )
        result = await self.session.execute(stmt)
        return [instance.to_entity() for instance in result.scalars().all()]

    async def list_by_connector_kind(
        self,
        connector_id: str,
        kind: str,
        search_query: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> Sequence[ConnectorTriggerEntity]:
        stmt = select(ConnectorTrigger).where(
            ConnectorTrigger.connector_id == connector_id,
            ConnectorTrigger.kind == kind,
        )
        if search_query:
            stmt = stmt.where(ConnectorTrigger.description.ilike(f"%{search_query}%"))
        stmt = stmt.order_by(ConnectorTrigger.id.asc())
        if limit is not None:
            stmt = stmt.limit(limit)
        result = await self.session.execute(stmt)
        return [instance.to_entity() for instance in result.scalars().all()]

    async def get_by_connector_kind_and_name(
        self,
        connector_id: str,
        kind: str,
        trigger_name: str,
    ) -> Optional[ConnectorTriggerEntity]:
        normalized_name = trigger_name.strip().lower()
        stmt = select(ConnectorTrigger).where(
            ConnectorTrigger.connector_id == connector_id,
            ConnectorTrigger.kind == kind,
            or_(
                func.lower(ConnectorTrigger.event_type) == normalized_name,
                ConnectorTrigger.id == trigger_name,
            ),
        )
        result = await self.session.execute(stmt)
        instance = result.scalars().first()
        return instance.to_entity() if instance else None

    async def list_all(
        self,
        connector_id: Optional[str] = None,
        search_query: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Tuple[Sequence[ConnectorTriggerEntity], Optional[str]]:
        stmt = select(ConnectorTrigger)

        if connector_id is not None:
            stmt = stmt.where(ConnectorTrigger.connector_id == connector_id)

        if search_query:
            stmt = stmt.where(ConnectorTrigger.description.ilike(f"%{search_query}%"))

        if cursor:
            stmt = stmt.where(ConnectorTrigger.id > cursor)

        stmt = stmt.order_by(ConnectorTrigger.id.asc()).limit(limit + 1)
        result = await self.session.execute(stmt)
        triggers = list(result.scalars().all())

        has_next = len(triggers) > limit
        if has_next:
            triggers = triggers[:limit]
            next_cursor = triggers[-1].id
        else:
            next_cursor = None

        return [t.to_entity() for t in triggers], next_cursor

    async def get_by_connector_and_name(
        self, app_id: str, trigger_id: str
    ) -> Optional[ConnectorTriggerEntity]:
        stmt = select(ConnectorTrigger).where(
            ConnectorTrigger.id == trigger_id,
            ConnectorTrigger.connector_id == app_id,
        )
        result = await self.session.execute(stmt)
        trigger = result.scalar_one_or_none()

        return trigger.to_entity() if trigger else None

    async def get_by_app_name_and_event_type(
        self, app_name: str, event_type: str, kind: Optional[str] = None
    ) -> List[ConnectorTriggerEntity]:
        normalized = app_name.lower()
        stmt = (
            select(ConnectorTrigger)
            .join(Connector, Connector.id == ConnectorTrigger.connector_id)
            .where(
                ConnectorTrigger.event_type == event_type,
                (ConnectorTrigger.connector_id == app_name)
                | (func.lower(Connector.title) == normalized),
            )
        )
        if kind is not None:
            stmt = stmt.where(ConnectorTrigger.kind == kind)
        result = await self.session.execute(stmt)
        triggers = result.scalars().all()

        return [t.to_entity() for t in triggers]
