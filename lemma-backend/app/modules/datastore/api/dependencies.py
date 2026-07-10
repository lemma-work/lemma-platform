from __future__ import annotations

from typing import Annotated, Optional

from fastapi import Depends

from app.core.api.dependencies import UoWDep, get_uow_factory
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.infrastructure.events.message_bus import get_message_bus
from app.modules.datastore.application.file_use_cases import FileUseCases
from app.modules.datastore.infrastructure.repositories import (
    DatastoreFileRepository,
    DatastoreTableRepository,
)
from app.modules.datastore.infrastructure.record_repository import (
    DatastoreRecordRepository,
)
from app.modules.datastore.infrastructure.schema_manager import SchemaManager
from app.modules.datastore.services.file_service import DatastoreFileService
from app.modules.datastore.services.record_service import RecordService
from app.modules.datastore.services.table_service import TableService
from app.modules.datastore.infrastructure.storage import create_datastore_storage
from app.modules.datastore.infrastructure.transactional_events import (
    dispatch_datastore_outbox_once,
)
from app.composition.authorization import create_authorization_service
from app.composition.identity_notifications import create_user_reader

_schema_manager_instance: Optional[SchemaManager] = None


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


SchemaManagerDep = Annotated[SchemaManager, Depends(get_schema_manager)]


def build_table_service(uow) -> TableService:
    """Construct a TableService from a unit of work (single wiring source)."""
    message_bus = get_message_bus()
    return TableService(
        table_repository=DatastoreTableRepository(uow, message_bus=message_bus),
        schema_manager=get_schema_manager(),
        authorization_service=create_authorization_service(uow),
    )


def build_record_service(uow) -> RecordService:
    """Construct a RecordService from a unit of work (single wiring source)."""
    message_bus = get_message_bus()
    return RecordService(
        record_repository=DatastoreRecordRepository(
            schema_manager=get_schema_manager()
        ),
        message_bus=message_bus,
        authorization_service=create_authorization_service(uow),
        user_repository=create_user_reader(uow, message_bus=message_bus),
        transactional_events=True,
        event_dispatcher=dispatch_datastore_outbox_once,
    )


def build_file_service(uow) -> DatastoreFileService:
    """Construct a DatastoreFileService from a unit of work (single wiring source)."""
    message_bus = get_message_bus()
    return DatastoreFileService(
        file_repository=DatastoreFileRepository(uow, message_bus=message_bus),
        storage=create_datastore_storage(),
        authorization_service=create_authorization_service(uow),
    )


def get_table_service(
    uow: UoWDep,
    schema_manager: SchemaManagerDep,
) -> TableService:
    return build_table_service(uow)


def get_record_service(
    uow: UoWDep,
    schema_manager: SchemaManagerDep,
) -> RecordService:
    return build_record_service(uow)


def get_file_service(
    uow: UoWDep,
) -> DatastoreFileService:
    return build_file_service(uow)


def build_file_use_cases(uow_factory: UnitOfWorkFactory) -> FileUseCases:
    """Construct the datastore file use-case layer (factory mode). The API and
    the worker build the same object so they share one saga implementation."""
    return FileUseCases(uow_factory, build_file_service)


def get_file_use_cases(
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> FileUseCases:
    return build_file_use_cases(uow_factory)


TableServiceDep = Annotated[TableService, Depends(get_table_service)]
RecordServiceDep = Annotated[RecordService, Depends(get_record_service)]
FileServiceDep = Annotated[DatastoreFileService, Depends(get_file_service)]
FileUseCasesDep = Annotated[FileUseCases, Depends(get_file_use_cases)]
