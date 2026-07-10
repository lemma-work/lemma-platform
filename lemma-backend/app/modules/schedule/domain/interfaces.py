"""Interfaces for schedule module."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Any, Dict, Protocol
from uuid import UUID

from app.core.authorization.context import Context
from app.modules.schedule.domain.schedule import ScheduleEntity, ScheduleType
from app.modules.schedule.domain.value_objects import DatastoreOperation


@dataclass(frozen=True, slots=True)
class ScheduleTarget:
    id: UUID
    pod_id: UUID
    name: str
    is_global_workflow: bool = False
    event_trigger_id: str | None = None
    event_trigger_config: dict[str, object] | None = None


class ScheduleTargetResolver(Protocol):
    async def get_workflow(self, workflow_id: UUID) -> ScheduleTarget | None: ...

    async def get_workflow_by_name(
        self, pod_id: UUID, name: str
    ) -> ScheduleTarget | None: ...


class ScheduleEventFilter(Protocol):
    """Evaluate an optional schedule filter without exposing model infrastructure."""

    async def filter_event(
        self,
        *,
        instruction: str,
        output_schema: dict[str, Any] | None,
        event_payload: dict[str, Any],
        schedule: ScheduleEntity,
    ) -> tuple[bool, dict[str, Any] | None]: ...

    async def get_agent(self, agent_id: UUID) -> ScheduleTarget | None: ...

    async def get_agent_by_name(
        self, pod_id: UUID, name: str
    ) -> ScheduleTarget | None: ...


class ScheduleRepository(ABC):
    """Interface for schedule repository."""

    @abstractmethod
    async def create(self, entity: ScheduleEntity) -> ScheduleEntity:
        """Create a new schedule."""
        pass

    @abstractmethod
    async def get(
        self,
        schedule_id: UUID,
        ctx: Context | None = None,
    ) -> Optional[ScheduleEntity]:
        """Get a schedule by ID."""
        pass

    @abstractmethod
    async def get_by_name(
        self,
        *,
        pod_id: UUID,
        name: str,
        ctx: Context | None = None,
    ) -> Optional[ScheduleEntity]:
        """Get a schedule by pod-scoped name."""
        pass

    @abstractmethod
    async def update(self, schedule_id: UUID, **kwargs) -> Optional[ScheduleEntity]:
        """Update a schedule."""
        pass

    @abstractmethod
    async def delete(self, schedule_id: UUID) -> bool:
        """Delete a schedule."""
        pass

    @abstractmethod
    async def list(
        self,
        schedule_type: Optional[ScheduleType] = None,
        is_active: Optional[bool] = None,
        pod_id: Optional[UUID] = None,
        user_id: Optional[UUID] = None,
        agent_id: Optional[UUID] = None,
        workflow_id: Optional[UUID] = None,
        name: str | None = None,
        ctx: Context | None = None,
        limit: int = 100,
        cursor: UUID | None = None,
    ) -> tuple[List[ScheduleEntity], UUID | None]:
        """List schedules with filters."""
        pass

    @abstractmethod
    async def find_by_config(
        self, schedule_type: ScheduleType, criteria: dict[str, Any]
    ) -> List[ScheduleEntity]:
        """Find schedules matching criteria using JSONB contains operator.

        Args:
           schedule_type: The type of schedule (WEBHOOK, etc.)
           criteria: Dictionary of key-value pairs to match in the config
        """
        pass

    @abstractmethod
    async def find_active_by_workflow(
        self,
        *,
        pod_id: UUID,
        workflow_id: UUID,
        user_id: UUID | None = None,
    ) -> List[ScheduleEntity]:
        """Find active schedules for a pod workflow, optionally scoped to an owner."""
        pass

    @abstractmethod
    async def find_by_pod_table_event(
        self,
        pod_id: UUID,
        table_name: str,
        operation: DatastoreOperation | str,
    ) -> List[ScheduleEntity]:
        """Find pod table schedules matching the event properties.

        Should match:
        - schedule.pod_id == event.pod_id
        - schedule.config.table_name == event.table_name OR schedule.config.table_name is None
        - schedule.config.operations contains event.operation OR schedule.config.operations is None
        """
        pass

    @abstractmethod
    async def list_all_by_pod(self, pod_id: UUID) -> List[ScheduleEntity]:
        """List every schedule in a pod without RBAC filtering.

        System-level query used for pod-deletion cleanup; includes internal
        schedules (unlike ``list``, which excludes ``is_internal`` rows).
        """
        pass


class SchedulerService(ABC):
    """Interface for scheduler service."""

    @abstractmethod
    async def schedule_job(self, schedule: ScheduleEntity) -> None:
        """Schedule a job for the schedule."""
        pass

    @abstractmethod
    async def remove_job(self, schedule_id: UUID) -> None:
        """Remove a scheduled job."""
        pass


class ExternalScheduleWriter(ABC):
    """Port for provisioning/deprovisioning external webhook providers."""

    @abstractmethod
    async def create_provider_trigger(self, schedule: ScheduleEntity) -> str | None:
        """Create an external provider subscription and return its provider ID."""
        pass

    @abstractmethod
    async def delete_provider_trigger(self, schedule: ScheduleEntity) -> None:
        """Delete the external provider subscription associated with the schedule."""
        pass


class ScheduleEventPublisher(ABC):
    """Port for publishing ScheduleFired events."""

    @abstractmethod
    async def publish_schedule_fired(
        self,
        schedule: ScheduleEntity,
        payload: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
        llm_output: Optional[Dict[str, Any]] = None,
        source_event_id: str | None = None,
    ) -> None:
        """Publish a ScheduleFired event."""
        pass


class ScheduleFilterTaskQueue(ABC):
    """Port for queueing deferred LLM filtering for schedules."""

    @abstractmethod
    async def enqueue(
        self,
        schedule_id: UUID,
        payload: Dict[str, Any],
        metadata: Dict[str, Any],
        source_event_id: str,
    ) -> None:
        """Enqueue background LLM filter work for a schedule."""
        pass


class WebhookVerifier(ABC):
    """Port for verifying provider webhook signatures and parsing payloads."""

    @abstractmethod
    def verify(self, payload: str, headers: Dict[str, Any]) -> Dict[str, Any]:
        """Verify a webhook and return the provider verification result."""
        pass
