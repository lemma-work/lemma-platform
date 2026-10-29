"""Job-state models for pod bundle operations.

All import, export, and publish snapshots and checkpoints are authoritative in
PostgreSQL and mirrored to Redis. Every accepted compare-and-swap write bumps
``version`` and ``seq`` so workers and realtime clients can reject stale state.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.pod_bundle.domain.connector_plan import ConnectorChoice, ConnectorPlan


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ImportStatus(str, Enum):
    QUEUED = "QUEUED"
    FETCHING = "FETCHING"
    PLANNING = "PLANNING"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    APPLYING = "APPLYING"
    CANCELLING = "CANCELLING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    PARTIALLY_CANCELLED = "PARTIALLY_CANCELLED"


class ExportStatus(str, Enum):
    QUEUED = "QUEUED"
    EXPORTING = "EXPORTING"
    READY = "READY"
    FAILED = "FAILED"


class PublishStatus(str, Enum):
    QUEUED = "QUEUED"
    EXPORTING = "EXPORTING"
    PUBLISHING = "PUBLISHING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class PublishMode(str, Enum):
    CREATE = "CREATE"
    UPDATE = "UPDATE"


class BundleJobKind(str, Enum):
    IMPORT = "IMPORT"
    EXPORT = "EXPORT"
    PUBLISH = "PUBLISH"


IMPORT_TERMINAL_STATUSES = frozenset(
    {
        ImportStatus.COMPLETED,
        ImportStatus.FAILED,
        ImportStatus.CANCELLED,
        ImportStatus.PARTIALLY_CANCELLED,
    }
)


class StepAction(str, Enum):
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    SKIP = "SKIP"


class StepStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class StepKind(str, Enum):
    """Apply-step kinds, in no particular order (ordering lives in the plan)."""

    #: A pod connector the bundle declares; planned first, since everything
    #: else may name one by slug.
    CONNECTOR = "CONNECTOR"
    TABLE = "TABLE"
    TABLE_DATA = "TABLE_DATA"
    FUNCTION = "FUNCTION"
    FUNCTION_GRANTS = "FUNCTION_GRANTS"
    AGENT = "AGENT"
    AGENT_GRANTS = "AGENT_GRANTS"
    WORKFLOW = "WORKFLOW"
    SCHEDULE = "SCHEDULE"
    APP = "APP"
    SURFACE = "SURFACE"
    FILE = "FILE"
    MACHINE = "MACHINE"


class PlanStep(BaseModel):
    """One unit of apply work. ``detail`` carries kind-specific diff info
    (e.g. table column changes) for UI display and destructive-change review;
    the applier re-derives the actual writes from the staged bundle + current
    pod state, never from ``detail``."""

    index: int
    kind: StepKind
    name: str
    action: StepAction
    destructive: bool = False
    detail: dict[str, Any] = Field(default_factory=dict)
    status: StepStatus = StepStatus.PENDING
    error: str | None = None


class VariableSpec(BaseModel):
    """A ``${var}`` placeholder declared by the bundle that the importer may
    (or must) resolve — pod member references, app slugs."""

    name: str
    kind: Literal["pod_member", "free"] = "free"
    description: str | None = None
    required: bool = False
    default: str | None = None


class ImportPlan(BaseModel):
    format_version: int
    bundle_name: str | None = None
    steps: list[PlanStep] = Field(default_factory=list)
    variables: list[VariableSpec] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    #: The bundle's pod connectors and the choices each offers the importer.
    connectors: list[ConnectorPlan] = Field(default_factory=list)

    def next_pending_step(self) -> PlanStep | None:
        for step in self.steps:
            if step.status in (StepStatus.PENDING, StepStatus.RUNNING):
                return step
        return None

    @property
    def grant_refusals(self) -> list[str]:
        """Each grant the importer could not make, named with its holder."""
        return [
            f"{step.kind.value.removesuffix('_GRANTS').lower()} '{step.name}': {item}"
            for step in self.steps
            for item in step.detail.get("refused") or []
        ]

    @property
    def has_destructive_steps(self) -> bool:
        return any(s.destructive and s.status == StepStatus.PENDING for s in self.steps)


class BundleSourceKind(str, Enum):
    """Where an imported bundle comes from — a CAPS wire enum.

    ``URL`` covers any lemma-origin signed download URL (an export or an
    uploaded ``.zip`` staged into our object storage); ``GITHUB`` is a public
    repo fetched via the connector path.
    """

    URL = "URL"
    GITHUB = "GITHUB"


class BundleSource(BaseModel):
    kind: BundleSourceKind
    # For kind=URL: the signed lemma download URL the bundle was imported from
    # (kept for the recipe/provenance). For kind=GITHUB: repo_url is set instead.
    url: str | None = None
    repo_url: str | None = None
    ref: str | None = None
    bundle_filename: str | None = None
    bundle_sha256: str | None = None


class Progress(BaseModel):
    done: int = 0
    total: int = 0


def connect_url_of(error: object) -> str | None:
    """The connect link a typed connector refusal carries, if it carries one."""
    details = getattr(error, "details", None)
    url = details.get("connect_url") if isinstance(details, dict) else None
    return url if isinstance(url, str) else None


class _BundleJobState(BaseModel):
    """Shared shape of the three job-state documents."""

    pod_id: UUID
    user_id: UUID
    error: str | None = None
    error_type: str | None = None
    error_code: str | None = None
    #: Where to connect the account a refusal named (`CONNECTION_REQUIRED`,
    #: `REAUTH_REQUIRED`), so the remedy reaches the person with the error.
    connect_url: str | None = None
    retryable: bool = False
    version: int = 0
    attempt: int = 1
    seq: int = 0
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    completed_at: datetime | None = None
    heartbeat_at: datetime | None = None

    def touch(self) -> None:
        """Bump the write sequence and timestamp. Call exactly once per
        state-store save (the store enforces this by calling it itself)."""
        self.seq += 1
        self.updated_at = _utcnow()


class ImportState(_BundleJobState):
    import_id: UUID
    source: BundleSource
    status: ImportStatus = ImportStatus.QUEUED
    staging_key: str | None = None
    plan: ImportPlan | None = None
    progress: Progress = Field(default_factory=Progress)
    variables_provided: dict[str, str] = Field(default_factory=dict)
    #: The importer's connector choices, checked against the plan and made
    #: whole (`connector_plan.resolve_connector_choices`), by slug.
    connector_choices: dict[str, ConnectorChoice] = Field(default_factory=dict)
    confirm_destructive: bool = False
    cancel_requested_at: datetime | None = None
    current_step: int | None = None
    committed_steps: list[int] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def is_terminal(self) -> bool:
        return self.status in IMPORT_TERMINAL_STATUSES


class ExportState(_BundleJobState):
    export_id: UUID
    status: ExportStatus = ExportStatus.QUEUED
    # Explicit selection, both ways round: row data is seeded only for the
    # tables named here, and files are included only for the folders named here.
    # There is no "everything" switch -- see ExportStartRequest for why.
    data_tables: list[str] | None = None
    file_folders: list[str] | None = None
    include: list[str] | None = None
    ttl_seconds: int | None = None
    staging_key: str | None = None
    bundle_filename: str | None = None
    download_url: str | None = None
    expires_at: datetime | None = None
    warnings: list[str] = Field(default_factory=list)
    progress: Progress = Field(default_factory=Progress)

    @property
    def is_terminal(self) -> bool:
        return self.status in (ExportStatus.READY, ExportStatus.FAILED)


class PublishFileProgress(BaseModel):
    path: str
    status: StepStatus = StepStatus.PENDING
    parts_done: int = 0
    parts_total: int = 1
    error: str | None = None


class PublishState(_BundleJobState):
    publish_id: UUID
    status: PublishStatus = PublishStatus.QUEUED
    repo_name: str
    mode: PublishMode = PublishMode.CREATE
    private: bool = False
    #: The repository lock this publish holds (`publish_lock.publish_lock_key`
    #: of the account the pod's `github` connector resolved to), kept only to
    #: release it. Not an input: nobody names the account a publish runs as.
    publish_lock_key: str | None = None
    ai_readme: bool = False
    staging_key: str | None = None
    repo_url: str | None = None
    repo_created: bool = False
    repo_owner: str | None = None
    repo_slug: str | None = None
    repo_branch: str | None = None
    readme: str | None = None
    files: list[PublishFileProgress] = Field(default_factory=list)
    progress: Progress = Field(default_factory=Progress)
    warnings: list[str] = Field(default_factory=list)

    @property
    def is_terminal(self) -> bool:
        return self.status in (PublishStatus.COMPLETED, PublishStatus.FAILED)
