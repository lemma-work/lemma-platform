"""Interfaces for schedule module."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
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
    #: The target's *standing* instruction -- what it is for, as against what a
    #: firing is for. A target without one has to be told by the schedule, which
    #: is the rule `validate_target_instruction` enforces; the pod's own
    #: assistant is the only agent that can have none.
    instruction: str | None = None
    is_global_workflow: bool = False
    event_trigger_id: str | None = None
    event_trigger_config: dict[str, object] | None = None


class ScheduleTargetResolver(Protocol):
    """The four lookups a schedule's target may need, by id or by name.

    All four, because `ScheduleService` calls all four. `get_agent` and
    `get_agent_by_name` were declared on `ScheduleEventFilter` below instead --
    a Protocol about evaluating an LLM filter, which no filter implements and
    which nothing reaches those two through. So the annotation said the
    resolver had two methods while `_get_agent_by_name` and `_validate_target`
    used the other two, and a stand-in that satisfied the type failed on the
    third call. The same shape `AgentPort.run_agent_by_id` was added to fix,
    on the other side of the same seam.
    """

    async def get_workflow(self, workflow_id: UUID) -> ScheduleTarget | None: ...

    async def get_workflow_by_name(
        self, pod_id: UUID, name: str
    ) -> ScheduleTarget | None: ...

    async def get_agent(self, agent_id: UUID) -> ScheduleTarget | None: ...

    async def get_agent_by_name(
        self, pod_id: UUID, name: str
    ) -> ScheduleTarget | None: ...


class DatastoreSchedulePolicy(Protocol):
    async def require_table_update(
        self, *, pod_id: UUID, table_name: str, ctx: Context
    ) -> None: ...

    async def can_view_all_runs(
        self, *, pod_id: UUID, table_name: str, ctx: Context
    ) -> bool: ...


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


class ScheduleRepository(ABC):
    """Interface for schedule repository."""

    @abstractmethod
    async def create(self, entity: ScheduleEntity) -> ScheduleEntity:
        """Create a new schedule."""

    @abstractmethod
    async def get(
        self,
        schedule_id: UUID,
        ctx: Context | None = None,
    ) -> Optional[ScheduleEntity]:
        """Get a schedule by ID."""

    @abstractmethod
    async def get_by_name(
        self,
        *,
        pod_id: UUID,
        name: str,
        ctx: Context | None = None,
    ) -> Optional[ScheduleEntity]:
        """Get a schedule by pod-scoped name."""

    @abstractmethod
    async def update(self, schedule_id: UUID, **kwargs) -> Optional[ScheduleEntity]:
        """Update a schedule."""

    @abstractmethod
    async def delete(self, schedule_id: UUID) -> bool:
        """Delete a schedule."""

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

    @abstractmethod
    async def find_by_config(
        self, schedule_type: ScheduleType, criteria: dict[str, Any]
    ) -> List[ScheduleEntity]:
        """Find schedules matching criteria using JSONB contains operator.

        Args:
           schedule_type: The type of schedule (WEBHOOK, etc.)
           criteria: Dictionary of key-value pairs to match in the config
        """

    @abstractmethod
    async def find_active_by_workflow(
        self,
        *,
        pod_id: UUID,
        workflow_id: UUID,
        user_id: UUID | None = None,
    ) -> List[ScheduleEntity]:
        """Find active schedules for a pod workflow, optionally scoped to an owner."""

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

    @abstractmethod
    async def list_all_by_pod(self, pod_id: UUID) -> List[ScheduleEntity]:
        """List every schedule in a pod without RBAC filtering.

        System-level query used for pod-deletion cleanup; includes internal
        schedules (unlike ``list``, which excludes ``is_internal`` rows).
        """


#: A schedule's `config`: a free-form JSON object whose keys are the routing
#: key its source matches on, so its shape belongs to the source rather than to
#: this module.
ScheduleConfig = dict[str, Any]


@dataclass(frozen=True, slots=True)
class ProvisionedTrigger:
    """What provisioning a schedule's external subscription produced.

    Both fields empty means *nothing needed provisioning*, and saying so is the
    point of this type. A GitHub App has one webhook URL and its installation
    decides which repositories it covers, so there is no remote subscription to
    create -- which used to be indistinguishable from finding no manager at all.
    In that case the row was written, nothing was provisioned, no error was
    raised, and the schedule could never fire. Slack's three triggers have been
    inert for exactly that reason since they were added.

    `bound_config` is merged into the schedule's config. It is where a source
    puts the routing key it can derive but the author cannot type -- GitHub's
    installation id comes from the account, not from the person filling in a
    form.
    """

    provider_trigger_id: str | None = None
    bound_config: ScheduleConfig = field(default_factory=dict)

    def apply_to(self, config: ScheduleConfig) -> bool:
        """Write what provisioning learned into the schedule's config.

        Returns whether anything changed, so the caller knows whether the row
        needs writing back. Here rather than at the call site because this type
        is the only thing that knows what its two fields mean.
        """
        if self.provider_trigger_id:
            config["provider_trigger_id"] = self.provider_trigger_id
        config.update(self.bound_config)
        return bool(self.provider_trigger_id or self.bound_config)


class ExternalScheduleWriter(ABC):
    """Port for provisioning/deprovisioning external webhook providers."""

    @abstractmethod
    async def create_provider_trigger(
        self, schedule: ScheduleEntity
    ) -> ProvisionedTrigger:
        """Provision this schedule's external subscription, if it needs one.

        Raises rather than returning quietly when the schedule names a connector
        trigger nothing knows how to provision *or* bind: a schedule that can
        never fire should not be created successfully.
        """

    @abstractmethod
    async def delete_provider_trigger(self, schedule: ScheduleEntity) -> None:
        """Delete the external provider subscription associated with the schedule."""


class ScheduleEventPublisher(ABC):
    """Port for publishing ScheduleFired events."""

    @abstractmethod
    async def publish_schedule_fired(
        self,
        schedule: ScheduleEntity,
        payload: Dict[str, Any],
        source_event_id: str,
        user_id: UUID,
        metadata: Optional[Dict[str, Any]] = None,
        llm_output: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Publish a ScheduleFired event."""


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
