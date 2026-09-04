"""Domain ports for the workflow module."""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, Protocol
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
    async def get(
        self, flow_id: UUID, ctx: Context | None = None
    ) -> WorkflowEntity | None: ...

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
    """Port for interacting with the Agent module.

    Payloads are `dict[str, object]`: what a workflow hands an agent, and what
    it reads back, is JSON whose shape belongs to the workflow's author. `Any`
    said the same thing while letting every caller index into it unchecked.
    """

    @abstractmethod
    async def run_agent(
        self,
        agent_name: str,
        input_data: dict[str, object],
        pod_id: UUID,
        user_id: UUID,
        conversation_id: UUID | None = None,
        workflow_run_id: UUID | None = None,
        source: str = "WORKFLOW_RUN",
        conversation_metadata: dict[str, object] | None = None,
        instructions: str | None = None,
    ) -> UUID:
        """Starts an agent conversation execution and returns the conversation ID.

        ``instructions`` is what this particular run is for, as opposed to what
        the agent is for. It becomes the conversation's instructions, which the
        prompt layers after the agent's own.
        """
        ...

    @abstractmethod
    async def run_agent_by_id(
        self,
        agent_id: UUID,
        input_data: dict[str, object],
        pod_id: UUID,
        user_id: UUID,
        conversation_id: UUID | None = None,
        workflow_run_id: UUID | None = None,
        source: str = "WORKFLOW_RUN",
        conversation_metadata: dict[str, object] | None = None,
        instructions: str | None = None,
    ) -> UUID:
        """The same start, for a target named by id rather than by name.

        On the port because `schedule_start_service` has always called it
        through `engine.agent_adapter`. Left off, the type said the engine used
        four methods and the code used five, so a stand-in that satisfied the
        port failed at the fifth.
        """
        ...

    @abstractmethod
    async def get_conversation_status(self, conversation_id: UUID) -> dict[str, object]:
        """Gets status and output from the latest internal run in a conversation."""
        ...

    @abstractmethod
    async def stop_conversation(self, conversation_id: UUID, user_id: UUID) -> None:
        """Requests that a running agent conversation stop.

        Best effort: a conversation that already finished is a no-op. Called
        when a workflow run is cancelled, so the agent stops working on an
        answer nobody will read.
        """


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

    @abstractmethod
    async def cancel_run(self, function_run_id: UUID) -> None:
        """Cancels a dispatched function run. Best effort; see stop_conversation."""


class WorkflowNotificationPort(Protocol):
    """What the engine tells a person's inbox about a run.

    A Protocol rather than an ABC because the notifier is the one collaborator
    the engine holds that has no domain of its own here: it exists so that a
    form waiting on someone, a form they just answered, and a run that was
    cancelled under them all reach the same inbox. Named at all so the engine's
    constructor says what it needs instead of taking whatever it is handed.
    """

    async def notify_form_assignee(
        self,
        *,
        pod_id: UUID,
        run_id: UUID,
        flow_id: UUID,
        node_id: str,
        assigned_pod_member_id: UUID,
        flow_name: str | None,
        schema: dict[str, object] | None,
        actor_user_id: UUID | None,
    ) -> None: ...

    async def close_form_notification(
        self,
        *,
        pod_id: UUID,
        run_id: UUID,
        node_id: str,
        summary: str,
        data: dict[str, object] | None = None,
    ) -> None: ...

    async def cancel_for_run(self, *, run_id: UUID) -> None: ...


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
