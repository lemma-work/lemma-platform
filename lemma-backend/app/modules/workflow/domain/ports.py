"""Domain ports for the workflow module."""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict
from uuid import UUID

from app.core.authorization.context import Context
from app.modules.workflow.domain.workflow import WorkflowEntity
from app.modules.workflow.domain.run import WorkflowRunEntity
from app.modules.workflow.domain.wait import (
    WorkflowRunWaitEntity,
    WorkflowRunWaitType,
)


class WorkflowRepository(ABC):
    @abstractmethod
    async def create(self, flow: WorkflowEntity) -> WorkflowEntity: ...

    @abstractmethod
    async def get(self, flow_id: UUID, ctx: Context | None = None) -> WorkflowEntity | None: ...

    @abstractmethod
    async def get_for_update(self, flow_id: UUID) -> WorkflowEntity | None: ...

    @abstractmethod
    async def get_by_name(
        self, pod_id: UUID, name: str, ctx: Context | None = None
    ) -> WorkflowEntity | None: ...

    @abstractmethod
    async def update(self, flow: WorkflowEntity) -> WorkflowEntity: ...

    @abstractmethod
    async def delete(self, flow_id: UUID) -> None: ...

    @abstractmethod
    async def list_by_pod(
        self,
        pod_id: UUID,
        *,
        limit: int = 100,
        cursor: UUID | None = None,
    ) -> tuple[list[WorkflowEntity], UUID | None]: ...

    @abstractmethod
    async def list_visible_by_pod(
        self,
        pod_id: UUID,
        *,
        ctx: Context,
        limit: int = 100,
        cursor: UUID | None = None,
    ) -> tuple[list[WorkflowEntity], UUID | None]: ...


class WorkflowRunRepository(ABC):
    @abstractmethod
    async def create(self, run: WorkflowRunEntity) -> WorkflowRunEntity: ...

    @abstractmethod
    async def get(self, run_id: UUID) -> WorkflowRunEntity | None: ...

    @abstractmethod
    async def get_for_update(self, run_id: UUID) -> WorkflowRunEntity | None: ...

    @abstractmethod
    async def update(self, run: WorkflowRunEntity) -> WorkflowRunEntity: ...

    @abstractmethod
    async def list_by_flow(
        self,
        flow_id: UUID,
        *,
        limit: int = 100,
        cursor: UUID | None = None,
    ) -> tuple[list[WorkflowRunEntity], UUID | None]: ...

    @abstractmethod
    async def find_by_schedule_event(
        self,
        *,
        flow_id: UUID,
        user_id: UUID,
        schedule_event_id: str,
    ) -> WorkflowRunEntity | None: ...


class WorkflowRunWaitRepository(ABC):
    @abstractmethod
    async def create(self, wait: WorkflowRunWaitEntity) -> WorkflowRunWaitEntity: ...

    @abstractmethod
    async def update(self, wait: WorkflowRunWaitEntity) -> WorkflowRunWaitEntity: ...

    @abstractmethod
    async def get_active_for_run(
        self, run_id: UUID
    ) -> WorkflowRunWaitEntity | None: ...

    @abstractmethod
    async def find_active_by_external_ref(
        self,
        wait_type: WorkflowRunWaitType,
        external_ref: str,
    ) -> WorkflowRunWaitEntity | None: ...

    @abstractmethod
    async def list_active_for_assignee(
        self,
        *,
        pod_id: UUID,
        assigned_pod_member_id: UUID,
        limit: int = 100,
        cursor: UUID | None = None,
    ) -> tuple[list[WorkflowRunWaitEntity], UUID | None]: ...

    @abstractmethod
    async def list_active_older_than(
        self,
        *,
        wait_types: list[WorkflowRunWaitType],
        created_before: datetime,
        limit: int = 100,
    ) -> list[WorkflowRunWaitEntity]: ...


class AgentPort(ABC):
    """Port for interacting with the Agent module."""

    @abstractmethod
    async def run_agent(
        self,
        agent_name: str,
        input_data: Dict[str, Any],
        pod_id: UUID,
        user_id: UUID,
        workflow_run_id: UUID | None = None,
        source: str = "WORKFLOW_RUN",
        conversation_metadata: Dict[str, Any] | None = None,
    ) -> UUID:
        """Starts an agent conversation execution and returns the conversation ID."""
        ...

    @abstractmethod
    async def get_conversation_status(self, conversation_id: UUID) -> Dict[str, Any]:
        """Gets status and output from the latest internal run in a conversation."""
        ...


class FunctionPort(ABC):
    """Port for interacting with the Function module."""

    @abstractmethod
    async def execute_function(
        self,
        function_name: str,
        inputs: Dict[str, Any],
        pod_id: UUID,
        user_id: UUID,
        ctx: Context | None = None,
    ) -> Any: ...

    @abstractmethod
    async def get_run_status(self, function_run_id: UUID) -> Dict[str, Any]:
        """Gets status and output of a function run (for reconciliation)."""
        ...


class SchedulePort(ABC):
    """Port for interacting with the scheduler."""

    @abstractmethod
    async def schedule_workflow_wake(
        self,
        run_id: UUID,
        scheduled_at: str,
        pod_id: UUID,
        user_id: UUID,
    ) -> UUID: ...
