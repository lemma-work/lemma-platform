"""The one place the table and record services are put together.

Below the API layer on purpose: contracts and services build these too -- a
visitor's row, a contact's read -- and a contract reaching up into
``api.dependencies`` for its wiring drags the request layer along with it.
``api.dependencies`` wraps these for request injection and nothing more.
"""

from __future__ import annotations

from app.core.authorization.factory import create_authorization_data_service
from app.core.infrastructure.events.message_bus import get_message_bus
from app.modules.datastore.infrastructure.record_repository import (
    DatastoreRecordRepository,
)
from app.modules.datastore.infrastructure.repositories import DatastoreTableRepository
from app.modules.datastore.infrastructure.schema_manager import SchemaManager
from app.modules.datastore.services.record_service import RecordService
from app.modules.datastore.services.table_service import TableService
from app.modules.identity.contracts.organizations import build_user_directory

_schema_manager_instance: SchemaManager | None = None


def get_schema_manager() -> SchemaManager:
    """Get or create singleton SchemaManager."""
    global _schema_manager_instance
    if _schema_manager_instance is None:
        _schema_manager_instance = SchemaManager()
    return _schema_manager_instance


async def close_schema_manager() -> None:
    """Dispose SchemaManager resources (shutdown/tests)."""
    global _schema_manager_instance
    if _schema_manager_instance is None:
        return

    await _schema_manager_instance.close()
    _schema_manager_instance = None


def reset_schema_manager() -> None:
    """Reset singleton SchemaManager instance (tests)."""
    global _schema_manager_instance
    _schema_manager_instance = None


def build_table_service(uow) -> TableService:
    """Construct a TableService from a unit of work (single wiring source)."""
    return TableService(
        table_repository=DatastoreTableRepository(uow, message_bus=get_message_bus()),
        schema_manager=get_schema_manager(),
        authorization_service=create_authorization_data_service(uow),
    )


def build_record_service(uow) -> RecordService:
    """Construct a RecordService from a unit of work (single wiring source)."""
    return RecordService(
        record_repository=DatastoreRecordRepository(
            schema_manager=get_schema_manager()
        ),
        authorization_service=create_authorization_data_service(uow),
        user_repository=build_user_directory(uow),
    )
