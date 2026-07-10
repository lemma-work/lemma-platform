"""Datastore adapters used by agent context, pod tools, and skill loading."""

from app.modules.datastore.api.dependencies import (
    build_file_service,
    build_record_service,
    build_table_service,
)
from app.modules.datastore.infrastructure.repositories import DatastoreFileRepository
from app.modules.datastore.infrastructure.storage import create_datastore_storage
from app.modules.datastore.services.file_service import DatastoreFileService
from app.modules.datastore.services.files.file_url import (
    build_file_app_url,
    build_object_url,
)
from app.modules.datastore.services.record_service import RecordService
from app.modules.datastore.services.table_service import TableService


def create_agent_skill_file_service(uow, *, authorization_service) -> DatastoreFileService:
    return DatastoreFileService(
        file_repository=DatastoreFileRepository(uow),
        storage=create_datastore_storage(),
        authorization_service=authorization_service,
    )


__all__ = [
    "DatastoreFileService",
    "RecordService",
    "TableService",
    "build_file_app_url",
    "build_file_service",
    "build_object_url",
    "build_record_service",
    "build_table_service",
    "create_agent_skill_file_service",
]
