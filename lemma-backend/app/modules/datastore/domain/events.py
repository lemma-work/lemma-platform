"""Datastore module domain events.

All datastore domain events publish to a single unified stream
(:data:`DATASTORE_EVENTS_STREAM`) and share a common base
(:class:`DatastoreDomainEvent`) carrying ``pod_id`` and an optional ``actor_id``.
"""

from __future__ import annotations

from enum import Enum
from uuid import UUID

from app.core.domain.events import DomainEvent


DATASTORE_EVENTS_STREAM = "datastore.events"


class DatastoreRecordOperation(str, Enum):
    INSERT = "INSERT"
    UPDATE = "UPDATE"
    DELETE = "DELETE"


class DatastoreDomainEvent(DomainEvent):
    """Base for every datastore domain event.

    Carries the owning pod and the acting user. All datastore events publish to
    the single unified :data:`DATASTORE_EVENTS_STREAM`.
    """

    pod_id: UUID
    actor_id: UUID | None = None

    @classmethod
    def stream_name(cls) -> str:
        return DATASTORE_EVENTS_STREAM


class DatastoreCreatedEvent(DatastoreDomainEvent):
    event_type: str = "datastore.created"
    name: str


class DatastoreUpdatedEvent(DatastoreDomainEvent):
    event_type: str = "datastore.updated"


class DatastoreDeletedEvent(DatastoreDomainEvent):
    event_type: str = "datastore.deleted"


class DatastoreTableCreatedEvent(DatastoreDomainEvent):
    event_type: str = "datastore.table.created"
    table_id: UUID
    table_name: str


class DatastoreTableUpdatedEvent(DatastoreDomainEvent):
    event_type: str = "datastore.table.updated"
    table_id: UUID
    table_name: str


class DatastoreTableDeletedEvent(DatastoreDomainEvent):
    event_type: str = "datastore.table.deleted"
    table_id: UUID
    table_name: str


class DatastoreTableColumnAddedEvent(DatastoreDomainEvent):
    event_type: str = "datastore.table.column.added"
    table_id: UUID
    table_name: str
    column_name: str


class DatastoreTableColumnRemovedEvent(DatastoreDomainEvent):
    event_type: str = "datastore.table.column.removed"
    table_id: UUID
    table_name: str
    column_name: str


class DatastoreFileCreatedEvent(DatastoreDomainEvent):
    event_type: str = "datastore.file.created"
    file_id: UUID
    path: str | None = None
    metadata: dict | None = None


class DatastoreFileUpdatedEvent(DatastoreDomainEvent):
    event_type: str = "datastore.file.updated"
    file_id: UUID
    path: str | None = None
    metadata: dict | None = None


class DatastoreFileDeletedEvent(DatastoreDomainEvent):
    event_type: str = "datastore.file.deleted"
    file_id: UUID
    path: str | None = None
    metadata: dict | None = None


class DatastoreRecordEvent(DatastoreDomainEvent):
    """Event emitted when a datastore record is inserted, updated, or deleted."""

    event_type: str
    table_name: str
    record_id: str
    operation: DatastoreRecordOperation
    # The row as it stands after the write; for DELETE, the row as it stood
    # before it was removed. Always the whole row, so a subscriber can read a
    # column the writer did not happen to submit.
    payload: dict
    # Columns this write actually set. ``payload`` alone cannot answer "did
    # this field change" — every column is present on every event — so the
    # written set is carried separately. ``None`` for DELETE, where nothing
    # was written.
    changed: list[str] | None = None
    # Prior values, restricted to ``changed``: the whole previous row would
    # double the size of every event to answer a question nobody asks about
    # untouched columns. ``None`` when there is no prior image (INSERT,
    # DELETE), which is what distinguishes "became X" from "was already X".
    previous: dict | None = None
    # Row owner for RLS-enabled tables; ``None`` for non-RLS tables. This lets
    # change subscribers (e.g. the datastore changes websocket) scope per-user
    # rows without a database read: a ``None`` owner fans out to every member
    # who can read the table, while a set owner is delivered only to that user.
    owner_user_id: UUID | None = None

    @classmethod
    def create(
        cls,
        *,
        pod_id: UUID,
        table_name: str,
        record_id: str,
        operation: DatastoreRecordOperation,
        payload: dict,
        changed: list[str] | None = None,
        previous: dict | None = None,
        actor_id: UUID | None = None,
        owner_user_id: UUID | None = None,
    ) -> "DatastoreRecordEvent":
        return cls(
            # Event-type names follow the lowercase dotted convention; the
            # operation enum value itself is CAPS (datastore.record.update).
            event_type=f"datastore.record.{operation.value.lower()}",
            pod_id=pod_id,
            table_name=table_name,
            record_id=record_id,
            operation=operation,
            payload=payload,
            changed=changed,
            previous=previous,
            actor_id=actor_id,
            owner_user_id=owner_user_id,
        )
