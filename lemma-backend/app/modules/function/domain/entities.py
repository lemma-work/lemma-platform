"""Function domain entities."""

from datetime import datetime
from enum import Enum
from typing import ClassVar, Literal
from uuid import UUID

from pydantic import BaseModel, Field, PrivateAttr

from app.core.authorization.context import ResourceType
from app.core.domain.events import DomainEvent
from app.modules.function.domain.types import JsonObject


class FunctionStatus(str, Enum):
    """Status of a function."""

    DRAFT = "DRAFT"
    CODE_GENERATION = "CODE_GENERATION"
    READY = "READY"
    ERROR = "ERROR"


class FunctionRunStatus(str, Enum):
    """Status of a function run."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class FunctionType(str, Enum):
    """Execution mode for a function."""

    API = "API"
    JOB = "JOB"


class FunctionDispatchMode(str, Enum):
    """How the backend waits for one already-persisted function run."""

    SYNCHRONOUS = "SYNCHRONOUS"
    ASYNCHRONOUS = "ASYNCHRONOUS"


class FunctionArtifact(BaseModel):
    revision_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    model_config = {"frozen": True}


class FunctionArtifactManifest(BaseModel):
    """Typed, hashed metadata embedded in every immutable function artifact."""

    format_version: Literal[1] = 1
    runtime_abi: str
    builder_digest: str
    dependency_lock: tuple[str, ...] = ()
    source_path: str = "function.py"
    input_model: str
    output_model: str
    entrypoint: str
    config_model: str | None = None
    dependency_path: str | None = None

    model_config = {"extra": "forbid", "frozen": True}


class FunctionSchemaSet(BaseModel):
    """Schemas extracted from one compiled function source."""

    input: JsonObject
    output: JsonObject
    config: JsonObject | None = None

    model_config = {"extra": "forbid", "frozen": True}


class FunctionExecutionDispatch(BaseModel):
    run_id: UUID
    pod_id: UUID
    function_id: UUID
    function_name: str
    user_id: UUID
    user_email: str | None
    config: JsonObject | None
    mode: FunctionDispatchMode
    deadline_at: datetime
    revision_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    input_data: JsonObject

    model_config = {"from_attributes": True}


class FunctionSessionPrincipal(BaseModel):
    """Authenticated delegated function identity on a runtime request."""

    user_id: UUID
    pod_id: UUID
    function_id: UUID
    session_id: str
    actor_name: str | None = None
    scope: tuple[str, ...] = ()

    model_config = {"frozen": True}


class FunctionRunRuntimeContext(BaseModel):
    run_id: UUID
    deadline_at: datetime
    revision_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    artifact_path: str
    input_data: JsonObject
    config: JsonObject | None
    user_id: UUID
    user_email: str | None
    pod_id: UUID
    function_id: UUID
    function_name: str
    model_config = {"from_attributes": True}


class FunctionEntity(BaseModel):
    """Function entity representing a programmatic task."""

    resource_type: ClassVar[ResourceType] = ResourceType.FUNCTION

    id: UUID | None = None
    pod_id: UUID
    user_id: UUID
    name: str
    description: str | None = None
    icon_url: str | None = None
    input_schema: JsonObject = Field(default_factory=dict)
    output_schema: JsonObject = Field(default_factory=dict)
    config_schema: JsonObject | None = None
    code_path: str | None = None
    revision_hash: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    code: str | None = None
    config: JsonObject | None = None
    type: FunctionType = FunctionType.API
    status: FunctionStatus = FunctionStatus.DRAFT
    visibility: str = "POD"
    pending_artifact: FunctionArtifact | None = Field(
        default=None, exclude=True, repr=False
    )
    allowed_actions: list[str] = Field(default_factory=list)
    # Timestamps
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class FunctionRevisionEntity(BaseModel):
    """One built revision of a function, with the contract its code implements."""

    id: UUID | None = None
    function_id: UUID
    revision_number: int
    revision_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    code_path: str
    input_schema: JsonObject = Field(default_factory=dict)
    output_schema: JsonObject = Field(default_factory=dict)
    config_schema: JsonObject | None = None
    created_by: UUID | None = None
    label: str | None = None
    pruned_at: datetime | None = None
    created_at: datetime | None = None
    # Populated only when a caller asked for the code; reading it is a storage
    # round trip, so listing revisions never pays for it.
    code: str | None = None

    model_config = {"from_attributes": True}

    @property
    def is_pruned(self) -> bool:
        return self.pruned_at is not None

    @property
    def artifact_path(self) -> str:
        return f"artifacts/{self.revision_hash.removeprefix('sha256:')}.zip"


class FunctionUpdateEntity(BaseModel):
    """Entity for updating function fields."""

    description: str | None = None
    icon_url: str | None = None
    code: str | None = None
    config: JsonObject | None = None
    type: FunctionType | None = None
    visibility: str | None = None
    model_config = {"from_attributes": True}


class RunAsWorkload(BaseModel):
    """Identifies the calling workload so its cached token is reused for execution."""

    workload_type: str
    workload_id: UUID
    workload_name: str | None = None


class FunctionRunEntity(BaseModel):
    """Function run entity representing an execution."""

    _domain_events: list[DomainEvent] = PrivateAttr(default_factory=list)

    id: UUID | None = None
    function_id: UUID
    revision_hash: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    user_id: UUID
    input_data: JsonObject | None = None
    output_data: JsonObject | None = None
    status: FunctionRunStatus = FunctionRunStatus.PENDING
    user_email: str | None = None
    job_id: str | None = None
    deadline_at: datetime | None = None
    error: str | None = None
    logs: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}

    def add_event(self, event: DomainEvent) -> None:
        self._domain_events.append(event)

    def collect_events(self) -> list[DomainEvent]:
        events = self._domain_events.copy()
        self._domain_events.clear()
        return events
