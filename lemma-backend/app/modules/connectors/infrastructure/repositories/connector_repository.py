from typing import Optional, Sequence, Tuple

from sqlalchemy import select

from app.core.domain.message_bus import MessageBus
from app.core.infrastructure.db.repository import SqlAlchemyRepository
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.connectors.domain.connector import ConnectorEntity
from app.modules.connectors.domain.ports import ConnectorRepositoryPort
from app.modules.connectors.infrastructure.models import Connector


class ConnectorRepository(
    SqlAlchemyRepository[Connector, ConnectorEntity],
    ConnectorRepositoryPort,
):
    """Repository for Connector operations."""

    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        message_bus: MessageBus | None = None,
    ):
        super().__init__(uow, Connector, ConnectorEntity)
        if message_bus is not None:
            self.uow.set_message_bus(message_bus)

    def _entity_data(self, entity: ConnectorEntity) -> dict:
        return entity.model_dump(
            mode="json",
            exclude={
                "oauth2_config",
                "composio_toolkit_slug",
                "created_at",
                "updated_at",
            },
        )

    def _to_model(self, entity: ConnectorEntity) -> Connector:
        return self.model_cls(**self._entity_data(entity))

    async def update(self, entity: ConnectorEntity) -> ConnectorEntity:
        stmt = select(Connector).where(Connector.id == entity.id)
        result = await self.session.execute(stmt)
        instance = result.scalars().first()
        if not instance:
            raise ValueError(f"Connector {entity.id} not found")

        for key, value in self._entity_data(entity).items():
            if key != "id" and hasattr(instance, key):
                setattr(instance, key, value)
        await self.session.flush()
        return instance.to_entity()

    async def titles_for(self, connector_ids: Sequence[str]) -> dict[str, str | None]:
        """The display titles for a set of connectors, in one query.

        The connectors landing page needs a title beside every install and
        every account it lists, and asked for them one `get()` at a time --
        one round trip per distinct connector, on the page's single call.
        Only the title is read, so the rest of the row (including the JSONB
        capability list) is left in the database.
        """
        if not connector_ids:
            return {}
        stmt = select(Connector.id, Connector.title).where(
            Connector.id.in_(set(connector_ids))
        )
        result = await self.session.execute(stmt)
        found = {row.id: row.title for row in result}
        return {connector_id: found.get(connector_id) for connector_id in connector_ids}

    async def kinds_for(
        self, connector_ids: Sequence[str]
    ) -> dict[str, list[dict[str, object]]]:
        """The kind specs for a set of connectors, in one query.

        A sibling of `titles_for`, and for the same reason: the connectors page
        needs one fact about the catalog entry behind every install it lists,
        and fetching whole connector rows one `get()` at a time is a round trip
        per install. This reads the capability list alone, which is what
        deciding how an install authenticates actually needs.
        """
        if not connector_ids:
            return {}
        stmt = select(Connector.id, Connector.kinds).where(
            Connector.id.in_(set(connector_ids))
        )
        result = await self.session.execute(stmt)
        return {row.id: list(row.kinds or []) for row in result}

    async def list_active(
        self, limit: int = 100, cursor: Optional[str] = None
    ) -> Tuple[Sequence[ConnectorEntity], Optional[str]]:
        """Get all active connectors with ID-based pagination."""
        stmt = select(Connector).where(Connector.is_active)

        if cursor:
            stmt = stmt.where(Connector.id > cursor)

        stmt = stmt.order_by(Connector.id.asc()).limit(limit + 1)
        result = await self.session.execute(stmt)
        connectors = list(result.scalars().all())

        has_next = len(connectors) > limit
        if has_next:
            connectors = connectors[:limit]
            next_cursor = connectors[-1].id
        else:
            next_cursor = None

        return [app.to_entity() for app in connectors], next_cursor
