"""Datastore, as an agent's tools use it.

**This publishes types, not only operations, and that is deliberate.** Every
other contract in this codebase hands back a DTO or a verdict, because the
caller wants one answer. The agent's POD toolset is different in kind: it is a
remote control for the datastore, and the thing it needs is the datastore. An
operation-per-method contract here would be datastore's whole service surface
copied into a second file, and it would have to be extended every time a tool
learns a new verb.

What this still fixes is the part that mattered. `app/composition/agent_datastore.py`
reached six different datastore module paths — `api.dependencies`,
`infrastructure.repositories`, `infrastructure.storage`, `services.file_service`,
`services.files.file_url`, `services.record_service`, `services.table_service`,
and `composition` — so moving any one of them broke `agent`. This is one named
surface datastore owns and can keep stable.

`build_agent_skill_file_service` is the exception that proves the rule: it was
assembling a `DatastoreFileService` out of four datastore internals *inside the
composition root*, which is a construction only datastore should know how to do.

A submodule rather than `contracts/__init__`, which is a leaf: this reaches the
service layer, and everything importing any datastore contract would otherwise
pay for it.
"""

from __future__ import annotations

from app.modules.datastore.api.dependencies import (
    build_file_service,
    build_record_service,
    build_table_service,
)
from app.modules.datastore.composition import get_datastore_composition
from app.modules.datastore.infrastructure.repositories import DatastoreFileRepository
from app.modules.datastore.infrastructure.storage import create_datastore_storage
from app.modules.datastore.services.file_service import DatastoreFileService
from app.modules.datastore.services.files.file_url import (
    build_file_app_url,
    build_object_url,
)
from app.modules.datastore.services.record_service import RecordService
from app.modules.datastore.services.table_service import TableService


def build_agent_skill_file_service(
    uow, *, authorization_service
) -> DatastoreFileService:
    """A file service for skill loading, wired the way datastore wires one."""
    return DatastoreFileService(
        file_repository=DatastoreFileRepository(uow),
        storage=create_datastore_storage(),
        authorization_service=authorization_service,
        search_service_factory=get_datastore_composition().build_search_service,
    )


__all__ = [
    "DatastoreFileService",
    "RecordService",
    "TableService",
    "build_agent_skill_file_service",
    "build_file_app_url",
    "build_file_service",
    "build_object_url",
    "build_record_service",
    "build_table_service",
]
