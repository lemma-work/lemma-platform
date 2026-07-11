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

    TABLE = "TABLE"
    TABLE_DATA = "TABLE_DATA"
    FUNCTION = "FUNCTION"
    AGENT = "AGENT"
    AGENT_GRANTS = "AGENT_GRANTS"
    WORKFLOW = "WORKFLOW"
    SCHEDULE = "SCHEDULE"
    APP = "APP"
    SURFACE = "SURFACE"
    FILE = "FILE"


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
    (or must) resolve — connector accounts, pod member references."""

    name: str
    kind: Literal["account", "pod_member", "free"] = "free"
    description: str | None = None
    required: bool = False
    default: str | None = None
    # For ``kind="account"`` variables: the connector the account must belong to
    # (e.g. "slack", "jira") and the auth provider backing it ("LEMMA" or
    # "COMPOSIO"), both resolved from the source account at export time — never
    # inferred from a resource's own name — so the importer UI can prompt for
    # and connect exactly the right connector/provider combination. Both are
    # required for every ``kind="account"`` variable; None only for non-account
    # variables.
    connector: str | None = None
    provider: str | None = None


class ImportPlan(BaseModel):
    format_version: int
    bundle_name: str | None = None
    steps: list[PlanStep] = Field(default_factory=list)
    variables: list[VariableSpec] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def next_pending_step(self) -> PlanStep | None:
        for step in self.steps:
            if step.status in (StepStatus.PENDING, StepStatus.RUNNING):
                return step
        return None

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


class _BundleJobState(BaseModel):
    """Shared shape of the three job-state documents."""

    pod_id: UUID
    user_id: UUID
    error: str | None = None
    error_type: str | None = None
    error_code: str | None = None
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
    confirm_destructive: bool = False
    cancel_requested_at: datetime | None = None
    current_step: int | None = None
    committed_steps: list[int] = Field(default_factory=list)

    @property
    def is_terminal(self) -> bool:
        return self.status in IMPORT_TERMINAL_STATUSES


class ExportState(_BundleJobState):
    export_id: UUID
    status: ExportStatus = ExportStatus.QUEUED
    with_data: bool = False
    # Opt-in per-table seed selection: seed row data only for these table names.
    # Independent of with_data (which seeds every table); the effective set is the
    # union of the two.
    data_tables: list[str] | None = None
    # Opt-in: include the pod's file storage (folders + file bytes).
    with_files: bool = False
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
    private: bool = False
    account_id: UUID | None = None
    ai_readme: bool = False
    staging_key: str | None = None
    repo_url: str | None = None
    repo_created: bool = False
    repo_owner: str | None = None
    repo_slug: str | None = None
    readme: str | None = None
    files: list[PublishFileProgress] = Field(default_factory=list)
    progress: Progress = Field(default_factory=Progress)

    @property
    def is_terminal(self) -> bool:
        return self.status in (PublishStatus.COMPLETED, PublishStatus.FAILED)
