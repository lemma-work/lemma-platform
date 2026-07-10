"""Bind app widget-content reads to the agent-owned widget store."""

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.ports.widget_content import WidgetContentReader
from app.modules.agent.services.widget_asset_service import WidgetAssetService


def create_widget_content_reader(
    uow: SqlAlchemyUnitOfWork,
) -> WidgetContentReader:
    return WidgetAssetService(uow)
