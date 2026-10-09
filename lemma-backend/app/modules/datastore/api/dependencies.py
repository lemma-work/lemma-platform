from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.api.dependencies import UoWDep, get_uow_factory
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.infrastructure.events.message_bus import get_message_bus
from app.modules.datastore.application.file_use_cases import FileUseCases
from app.modules.datastore.infrastructure.repositories import DatastoreFileRepository
from app.modules.datastore.infrastructure.schema_manager import SchemaManager
from app.modules.datastore.services.file_service import DatastoreFileService
from app.modules.datastore.services.record_service import RecordService
from app.modules.datastore.services.table_service import TableService
from app.modules.datastore.infrastructure.storage import create_datastore_storage
from app.core.authorization.factory import create_authorization_data_service
from app.modules.datastore.composition import get_datastore_composition
from app.modules.datastore.services.wiring import (
    build_record_service,
    build_table_service,
    close_schema_manager,
    get_schema_manager,
    reset_schema_manager,
)

__all__ = [
    "FileServiceDep",
    "FileUseCasesDep",
    "RecordServiceDep",
    "SchemaManagerDep",
    "TableServiceDep",
    "build_file_service",
    "build_file_use_cases",
    "build_record_service",
    "build_table_service",
    "close_schema_manager",
    "get_schema_manager",
    "reset_schema_manager",
]


SchemaManagerDep = Annotated[SchemaManager, Depends(get_schema_manager)]


def build_file_service(uow) -> DatastoreFileService:
    """Construct a DatastoreFileService from a unit of work (single wiring source)."""
    message_bus = get_message_bus()
    return DatastoreFileService(
        file_repository=DatastoreFileRepository(uow, message_bus=message_bus),
        storage=create_datastore_storage(),
        authorization_service=create_authorization_data_service(uow),
        search_service_factory=get_datastore_composition().build_search_service,
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
