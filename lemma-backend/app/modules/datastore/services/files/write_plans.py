"""DTOs crossing short database and connection-free storage phases."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.modules.datastore.domain.file_entities import DatastoreFileEntity


@dataclass(frozen=True, slots=True)
class CreateFilePlan:
    entity: DatastoreFileEntity
    storage_key: str
    requester_user_id: UUID
    emit_created_event: bool


@dataclass(frozen=True, slots=True)
class MarkdownAttachPlan:
    entity: DatastoreFileEntity
    requester_user_id: UUID
