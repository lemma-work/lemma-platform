"""How a workflow starts, as the API takes it in and hands it back.

Split out of `schemas.py`, which re-exports every name here: a discriminated
union per direction, four variants each, and the two converters to and from
the domain's single `WorkflowStart`.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.workflow.domain.start import (
    DataStoreWorkflowStartConfig,
    EventWorkflowStartConfig,
    ScheduledWorkflowStartConfig,
    WorkflowStart,
    WorkflowStartType,
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
    model_config = ConfigDict(
        from_attributes=True, title="ScheduledWorkflowStartConfigOutput"
    )


class EventWorkflowStartConfigOutput(EventWorkflowStartConfig):
    model_config = ConfigDict(
        from_attributes=True, title="EventWorkflowStartConfigOutput"
    )


class DataStoreWorkflowStartConfigOutput(DataStoreWorkflowStartConfig):
    model_config = ConfigDict(
        from_attributes=True, title="DataStoreWorkflowStartConfigOutput"
    )


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
            config=ScheduledWorkflowStartConfig.model_validate(
                start.config.model_dump()
            ),
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
