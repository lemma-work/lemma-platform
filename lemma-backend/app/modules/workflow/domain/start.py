"""Workflow start (trigger) configuration."""

from enum import Enum
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field, field_validator

from app.modules.schedule.contracts import (
    DatastoreOperation,
    normalize_datastore_operations,
)


class WorkflowStartType(str, Enum):
    MANUAL = "MANUAL"
    SCHEDULED = "SCHEDULED"
    EVENT = "EVENT"
    DATASTORE_EVENT = "DATASTORE_EVENT"


class ScheduledWorkflowStartConfigType(str, Enum):
    ONCE = "ONCE"
    CRON = "CRON"


class DataStoreWorkflowStartConfig(BaseModel):
    table_name: str = Field(
        ...,
        description="Table name inside the datastore to subscribe to.",
    )
    operations: List[DatastoreOperation] = Field(
        default_factory=list,
        description=(
            "Datastore operations that should trigger this flow. "
            "One or more of INSERT, UPDATE, DELETE."
        ),
    )

    @field_validator("operations", mode="before")
    @classmethod
    def normalize_operations(cls, value: Any) -> List[DatastoreOperation]:
        if value is None:
            return []

        if not isinstance(value, list):
            raise ValueError("operations must be a list")

        return normalize_datastore_operations(value)


class ScheduledWorkflowStartConfig(BaseModel):
    schedule_type: ScheduledWorkflowStartConfigType = Field(
        ...,
        description=(
            "Time trigger mode for this workflow definition. "
            "Concrete schedule values are provided by pod schedules."
        ),
    )


class EventWorkflowStartConfig(BaseModel):
    connector_trigger_id: str = Field(
        ...,
        description="Connector trigger identifier to subscribe to.",
    )
    connector_id: str = Field(
        ...,
        description="Connector connector identifier.",
    )
    trigger_config: Dict[str, Any] = Field(default_factory=dict)


class WorkflowStart(BaseModel):
    type: WorkflowStartType = Field(
        ...,
        description="Workflow start mode: MANUAL, SCHEDULED, EVENT, or DATASTORE_EVENT.",
    )
    config: Optional[Union[ScheduledWorkflowStartConfig, EventWorkflowStartConfig, DataStoreWorkflowStartConfig]] = (
        Field(
            default=None,
            description=(
                "Start mode configuration payload. Required for non-manual start types."
            ),
        )
    )
