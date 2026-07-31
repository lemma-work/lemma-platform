from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.core.authorization.context import ResourceVisibility
from app.modules.workflow.domain.workflow import (
    WorkflowEntity,
    WorkflowMode,
)
from app.modules.workflow.domain.graph import WorkflowEdge
from app.modules.workflow.domain.run import (
    WorkflowRunEntity,
    WorkflowRunStatus,
    StepStatus,
)
from app.modules.workflow.domain.wait import (
    WorkflowRunWaitEntity,
    WorkflowRunWaitStatus,
    WorkflowRunWaitType,
)
from app.modules.workflow.domain.start import (
    DataStoreWorkflowStartConfig,
    EventWorkflowStartConfig,
    WorkflowStart,
    WorkflowStartType,
    ScheduledWorkflowStartConfig,
)
from app.modules.workflow.domain.nodes import (
    AgentNode,
    DecisionNode,
    EndNode,
    FormNode,
    FunctionNode,
    LoopNode,
    WaitUntilNode,
    WorkflowNode,
)


class ScheduledWorkflowStartConfigInput(ScheduledWorkflowStartConfig):
    model_config = ConfigDict(title="ScheduledWorkflowStartConfigInput")


class EventWorkflowStartConfigInput(EventWorkflowStartConfig):
    model_config = ConfigDict(title="EventWorkflowStartConfigInput")


class DataStoreWorkflowStartConfigInput(DataStoreWorkflowStartConfig):
    model_config = ConfigDict(title="DataStoreWorkflowStartConfigInput")


class ManualWorkflowStartInput(BaseModel):
    type: Literal[WorkflowStartType.MANUAL] = Field(
        default=WorkflowStartType.MANUAL,
        description="Manual workflow start with no configuration payload.",
    )
    config: None = Field(
        default=None,
        description="Always `null` for manual workflow starts.",
    )

    model_config = ConfigDict(title="ManualWorkflowStartInput")


class ScheduledWorkflowStartInput(BaseModel):
    type: Literal[WorkflowStartType.SCHEDULED] = Field(
        default=WorkflowStartType.SCHEDULED,
        description="Scheduled workflow start.",
    )
    config: ScheduledWorkflowStartConfigInput = Field(
        ...,
        description="Scheduled workflow definition payload.",
    )

    model_config = ConfigDict(title="ScheduledWorkflowStartInput")


class EventWorkflowStartInput(BaseModel):
    type: Literal[WorkflowStartType.EVENT] = Field(
        default=WorkflowStartType.EVENT,
        description="Event-triggered workflow start.",
    )
    config: EventWorkflowStartConfigInput = Field(
        ...,
        description="Connector trigger configuration for this workflow.",
    )

    model_config = ConfigDict(title="EventWorkflowStartInput")


class DataStoreWorkflowStartInput(BaseModel):
    type: Literal[WorkflowStartType.DATASTORE_EVENT] = Field(
        default=WorkflowStartType.DATASTORE_EVENT,
        description="Datastore-event workflow start.",
    )
    config: DataStoreWorkflowStartConfigInput = Field(
        ...,
        description="Datastore trigger configuration for this workflow.",
    )

    model_config = ConfigDict(title="DataStoreWorkflowStartInput")


WorkflowStartInput = Annotated[
    (
        ManualWorkflowStartInput
        | ScheduledWorkflowStartInput
        | EventWorkflowStartInput
        | DataStoreWorkflowStartInput
    ),
    Field(discriminator="type"),
]


class ScheduledWorkflowStartConfigOutput(ScheduledWorkflowStartConfig):
    model_config = ConfigDict(from_attributes=True, title="ScheduledWorkflowStartConfigOutput")


class EventWorkflowStartConfigOutput(EventWorkflowStartConfig):
    model_config = ConfigDict(from_attributes=True, title="EventWorkflowStartConfigOutput")


class DataStoreWorkflowStartConfigOutput(DataStoreWorkflowStartConfig):
    model_config = ConfigDict(from_attributes=True, title="DataStoreWorkflowStartConfigOutput")


class ManualWorkflowStartOutput(BaseModel):
    type: Literal[WorkflowStartType.MANUAL] = Field(
        default=WorkflowStartType.MANUAL,
        description="Manual workflow start with no configuration payload.",
    )
    config: None = Field(
        default=None,
        description="Always `null` for manual workflow starts.",
    )

    model_config = ConfigDict(from_attributes=True, title="ManualWorkflowStartOutput")


class ScheduledWorkflowStartOutput(BaseModel):
    type: Literal[WorkflowStartType.SCHEDULED] = Field(
        default=WorkflowStartType.SCHEDULED,
        description="Scheduled workflow start.",
    )
    config: ScheduledWorkflowStartConfigOutput = Field(
        ...,
        description="Scheduled workflow definition payload.",
    )

    model_config = ConfigDict(
        from_attributes=True,
        title="ScheduledWorkflowStartOutput",
    )


class EventWorkflowStartOutput(BaseModel):
    type: Literal[WorkflowStartType.EVENT] = Field(
        default=WorkflowStartType.EVENT,
        description="Event-triggered workflow start.",
    )
    config: EventWorkflowStartConfigOutput = Field(
        ...,
        description="Connector trigger configuration for this workflow.",
    )

    model_config = ConfigDict(
        from_attributes=True,
        title="EventWorkflowStartOutput",
    )


class DataStoreWorkflowStartOutput(BaseModel):
    type: Literal[WorkflowStartType.DATASTORE_EVENT] = Field(
        default=WorkflowStartType.DATASTORE_EVENT,
        description="Datastore-event workflow start.",
    )
    config: DataStoreWorkflowStartConfigOutput = Field(
        ...,
        description="Datastore trigger configuration for this workflow.",
    )

    model_config = ConfigDict(
        from_attributes=True,
        title="DataStoreWorkflowStartOutput",
    )


WorkflowStartOutput = Annotated[
    (
        ManualWorkflowStartOutput
        | ScheduledWorkflowStartOutput
        | EventWorkflowStartOutput
        | DataStoreWorkflowStartOutput
    ),
    Field(discriminator="type"),
]


def workflow_start_input_to_domain(
    start: WorkflowStartInput | None,
) -> WorkflowStart | None:
    if start is None:
        return None

    if isinstance(start, ManualWorkflowStartInput):
        return WorkflowStart(type=WorkflowStartType.MANUAL, config=None)

    if isinstance(start, ScheduledWorkflowStartInput):
        return WorkflowStart(
            type=WorkflowStartType.SCHEDULED,
            config=ScheduledWorkflowStartConfig.model_validate(start.config.model_dump()),
        )

    if isinstance(start, EventWorkflowStartInput):
        return WorkflowStart(
            type=WorkflowStartType.EVENT,
            config=EventWorkflowStartConfig.model_validate(start.config.model_dump()),
        )

    return WorkflowStart(
        type=WorkflowStartType.DATASTORE_EVENT,
        config=DataStoreWorkflowStartConfig.model_validate(start.config.model_dump()),
    )


def workflow_start_output_from_domain(
    start: WorkflowStart | None,
) -> WorkflowStartOutput | None:
    if start is None:
        return None

    if start.type == WorkflowStartType.MANUAL:
        return ManualWorkflowStartOutput()

    if start.type == WorkflowStartType.SCHEDULED:
        return ScheduledWorkflowStartOutput(
            config=ScheduledWorkflowStartConfigOutput.model_validate(start.config),
        )

    if start.type == WorkflowStartType.EVENT:
        return EventWorkflowStartOutput(
            config=EventWorkflowStartConfigOutput.model_validate(start.config),
        )

    return DataStoreWorkflowStartOutput(
        config=DataStoreWorkflowStartConfigOutput.model_validate(start.config),
    )


class WorkflowCreateRequest(BaseModel):
    name: str = Field(..., description="Workflow name.")
    description: str | None = Field(
        default=None,
        description="Optional workflow description.",
    )
    icon_url: str | None = Field(
        default=None,
        description="Optional public icon URL for the workflow.",
    )
    start: WorkflowStartInput | None = Field(
        default=None,
        description=(
            "Start configuration. If omitted, the workflow can be started manually via `workflow.start`."
        ),
    )
    mode: WorkflowMode = Field(
        default=WorkflowMode.GLOBAL,
        description=(
            "Workflow schedule ownership mode. `GLOBAL` means one pod-level workflow "
            "schedule is allowed; `USER` is reserved for per-user schedule ownership."
        ),
    )
    visibility: ResourceVisibility = ResourceVisibility.POD
    nodes: list[WorkflowNode] = Field(
        default_factory=list,
        description=(
            "Optional initial graph nodes. When provided, the graph is stored at "
            "creation time so a separate `workflow.graph.update` call is not "
            "required. Omit (or pass an empty list) to create a shell and upload "
            "the graph later. Node `input_mapping` entries must use explicit typed "
            'bindings like `{"type": "expression", "value": "start.payload.x"}`.'
        ),
    )
    edges: list[WorkflowEdge] = Field(
        default_factory=list,
        description="Optional initial graph edges connecting the provided nodes.",
    )


class WorkflowUpdateRequest(BaseModel):
    description: str | None = Field(
        default=None,
        description="Updated workflow description.",
    )
    icon_url: str | None = Field(
        default=None,
        description="Updated public icon URL for the workflow.",
    )
    mode: WorkflowMode | None = Field(
        default=None,
        description="Updated workflow schedule ownership mode.",
    )
    start: WorkflowStartInput | None = Field(
        default=None,
        description="Updated start trigger configuration.",
    )
    visibility: ResourceVisibility | None = None


class WorkflowGraphUpdateRequest(BaseModel):
    """Named request body for replacing a workflow graph."""

    nodes: list[WorkflowNode] = Field(
        ...,
        description=(
            "Complete node list for the workflow graph. Agent/function `input_mapping` "
            "entries must use explicit typed bindings like "
            '`{"type": "expression", "value": "start.payload.issue.key"}` or '
            '`{"type": "literal", "value": "finance"}`.'
        ),
    )
    edges: list[WorkflowEdge] = Field(
        ...,
        description="Complete edge list connecting the provided nodes.",
    )
    start: WorkflowStartInput | None = Field(
        default=None,
        description="Optional replacement start configuration stored with the graph.",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "nodes": [
                    {
                        "id": "collect_context",
                        "type": "FUNCTION",
                        "config": {
                            "function_name": "summarize-ticket",
                            "input_mapping": {
                                "ticket_key": {
                                    "type": "expression",
                                    "value": "start.payload.ticket.key",
                                },
                                "channel": {"type": "literal", "value": "support"},
                            },
                        },
                    }
                ],
                "edges": [],
                "start": {
                    "type": "DATASTORE_EVENT",
                    "config": {
                        "table_name": "expenses",
                        "operations": ["INSERT", "UPDATE", "DELETE"],
                    },
                },
            }
        }
    }


class FormNodeResponse(FormNode):
    model_config = ConfigDict(from_attributes=True, title="FormNodeResponse")


class AgentNodeResponse(AgentNode):
    model_config = ConfigDict(from_attributes=True, title="AgentNodeResponse")


class FunctionNodeResponse(FunctionNode):
    model_config = ConfigDict(from_attributes=True, title="FunctionNodeResponse")


class DecisionNodeResponse(DecisionNode):
    model_config = ConfigDict(from_attributes=True, title="DecisionNodeResponse")


class LoopNodeResponse(LoopNode):
    model_config = ConfigDict(from_attributes=True, title="LoopNodeResponse")


class WaitUntilNodeResponse(WaitUntilNode):
    model_config = ConfigDict(from_attributes=True, title="WaitUntilNodeResponse")


class EndNodeResponse(EndNode):
    model_config = ConfigDict(from_attributes=True, title="EndNodeResponse")


WorkflowNodeResponse = Annotated[
    (
        FormNodeResponse
        | AgentNodeResponse
        | FunctionNodeResponse
        | DecisionNodeResponse
        | LoopNodeResponse
        | WaitUntilNodeResponse
        | EndNodeResponse
    ),
    Field(discriminator="type"),
]


class WorkflowResponse(BaseModel):
    id: UUID
    created_at: datetime | None = None
    updated_at: datetime | None = None
    name: str
    description: str | None = None
    icon_url: str | None = None
    pod_id: UUID
    nodes: list[WorkflowNodeResponse] = Field(default_factory=list)
    edges: list[WorkflowEdge] = Field(default_factory=list)
    start: WorkflowStartOutput | None = None
    is_active: bool = True
    mode: WorkflowMode = WorkflowMode.GLOBAL
    visibility: str = "POD"

    model_config = ConfigDict(from_attributes=True, title="WorkflowResponse")


class WorkflowDetailResponse(WorkflowResponse):
    allowed_actions: list[str] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True, title="WorkflowDetailResponse")


class WorkflowSummaryResponse(BaseModel):
    """Lean workflow shape for list responses.

    Omits the full graph (`nodes`/`edges`/`start`) — fetch those from
    `workflow.get`. Carries cheap derived `node_count`/`node_types` so list
    views can show step counts and participant badges without the graph.
    """

    id: UUID
    created_at: datetime | None = None
    updated_at: datetime | None = None
    name: str
    description: str | None = None
    icon_url: str | None = None
    pod_id: UUID
    is_active: bool = True
    mode: WorkflowMode = WorkflowMode.GLOBAL
    visibility: str = "POD"
    node_count: int = 0
    node_types: list[str] = Field(default_factory=list)
    allowed_actions: list[str] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True, title="WorkflowSummaryResponse")


class WorkflowListResponse(BaseModel):
    items: list[WorkflowSummaryResponse]
    limit: int
    next_page_token: str | None = None

    model_config = ConfigDict(from_attributes=True)


class WorkflowRunSummaryResponse(BaseModel):
    id: UUID
    workflow_id: UUID = Field(
        validation_alias=AliasChoices("workflow_id", "flow_id")
    )
    pod_id: UUID
    user_id: UUID
    start_type: str = "MANUAL"
    schedule_event_id: str | None = None
    status: WorkflowRunStatus = WorkflowRunStatus.PENDING
    current_node_id: str | None = None
    error: str | None = None
    failed_node_id: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class StepRecordResponse(BaseModel):
    step_index: int
    node_id: str
    status: StepStatus
    started_at: datetime
    completed_at: datetime | None = None
    output_data: Any | None = None
    error: str | None = None

    model_config = ConfigDict(from_attributes=True)


class WorkflowRunWaitResponse(BaseModel):
    id: UUID
    run_id: UUID
    workflow_id: UUID = Field(
        validation_alias=AliasChoices("workflow_id", "flow_id")
    )
    pod_id: UUID
    node_id: str
    wait_type: WorkflowRunWaitType
    status: WorkflowRunWaitStatus
    assigned_pod_member_id: UUID | None = None
    external_ref: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    completed_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class WorkflowRunResponse(WorkflowRunSummaryResponse):
    """Full run state. `execution_context` is the same flat view that
    workflow expressions resolve against (`<node_id>.<field>`, `start.*`,
    `loop.*`). `active_wait` is set when the run is suspended, including
    WAITING form waits and RUNNING platform waits."""

    execution_context: dict[str, Any] = Field(default_factory=dict)
    step_history: list[StepRecordResponse] = Field(default_factory=list)
    active_wait: WorkflowRunWaitResponse | None = None


class WorkflowRunFormSubmitRequest(BaseModel):
    """Canonical form submission payload — identical across web, SDKs, CLI."""

    node_id: str = Field(
        ...,
        description=(
            "Id of the FORM node being submitted. Must match the run's active "
            "wait; mismatches return 422."
        ),
    )
    inputs: dict[str, Any] = Field(
        default_factory=dict,
        description="Form field values keyed by field name.",
    )

    model_config = ConfigDict(extra="forbid")


class WorkflowRunListResponse(BaseModel):
    items: list[WorkflowRunSummaryResponse]
    limit: int
    next_page_token: str | None = None


class WorkflowRunWaitAssignment(BaseModel):
    wait: WorkflowRunWaitResponse
    run: WorkflowRunSummaryResponse


class WorkflowRunWaitAssignmentListResponse(BaseModel):
    items: list[WorkflowRunWaitAssignment]
    limit: int
    next_page_token: str | None = None


def workflow_response_from_domain(workflow: WorkflowEntity) -> WorkflowResponse:
    payload = workflow.model_dump(mode="python")
    payload["start"] = workflow_start_output_from_domain(workflow.start)
    return WorkflowResponse.model_validate(payload)


def run_response_from_domain(
    run: WorkflowRunEntity,
    active_wait: WorkflowRunWaitEntity | None = None,
) -> WorkflowRunResponse:
    return WorkflowRunResponse(
        id=run.id,
        workflow_id=run.flow_id,
        pod_id=run.pod_id,
        user_id=run.user_id,
        start_type=run.start_type,
        schedule_event_id=run.schedule_event_id,
        status=run.status,
        current_node_id=run.current_node_id,
        error=run.error,
        failed_node_id=run.failed_node_id,
        started_at=run.started_at,
        completed_at=run.completed_at,
        created_at=run.created_at,
        updated_at=run.updated_at,
        execution_context=run.execution_context.to_view(),
        step_history=[
            StepRecordResponse.model_validate(step) for step in run.step_history
        ],
        active_wait=(
            WorkflowRunWaitResponse.model_validate(active_wait)
            if active_wait is not None
            else None
        ),
    )
