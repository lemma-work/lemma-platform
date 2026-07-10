"""Datastore adapter for schedule module."""

from typing import List, TYPE_CHECKING
from uuid import UUID

from app.modules.datastore.domain.events import DatastoreRecordEvent
from app.modules.schedule.domain.value_objects import parse_datastore_operation
from app.modules.schedule.repositories.schedule_repository import ScheduleRepository
from app.composition.schedule_filter import create_schedule_processor

if TYPE_CHECKING:
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork


class DatastoreAdapter:
    """Adapter to listen to datastore events and process them."""

    def __init__(self, uow: "SqlAlchemyUnitOfWork"):
        self.uow = uow
        self.schedule_repository = ScheduleRepository(uow=uow)
        self.schedule_processor = create_schedule_processor()

    async def handle_datastore_event(self, event: DatastoreRecordEvent) -> List[UUID]:
        """
        Handle datastore record event.

        1. Find matching schedules using optimized repository method.
        2. Process each schedule.
        """
        operation = parse_datastore_operation(event.operation.value)
        schedules = await self.schedule_repository.find_by_pod_table_event(
            pod_id=event.pod_id,
            table_name=event.table_name,
            operation=operation,
        )

        fired_schedule_ids = []
        for schedule in schedules:
            fired = await self.schedule_processor.process_event(
                schedule=schedule,
                payload=event.payload,
                metadata={
                    "table_name": event.table_name,
                    "record_id": event.record_id,
                    "operation": operation.value,
                },
                source_event_id=str(event.event_id),
            )
            if fired:
                fired_schedule_ids.append(schedule.id)

        return fired_schedule_ids
