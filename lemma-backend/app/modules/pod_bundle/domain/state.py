"""Ephemeral job-state documents for pod bundle operations.

These pydantic models ARE the Redis JSON schema (see
``docs/design/pod-bundle-share-import.md``). They are a UI/progress cache with
a TTL, never a source of truth: losing one is always recoverable by
re-uploading and re-planning, because the plan is a diff against the pod's
current resources and apply steps are idempotent upserts.

Write discipline: the API process writes the initial document (and a
cancellation marker); after the job is enqueued the worker is the single
writer — guaranteed by the streaq dedup job id — so read-modify-write needs no
locking. Every write bumps ``seq`` (monotonic per document) so SSE consumers
can order a replayed snapshot against live events.
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
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


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


IMPORT_TERMINAL_STATUSES = frozenset(
    {ImportStatus.COMPLETED, ImportStatus.FAILED, ImportStatus.CANCELLED}
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


class BundleSource(BaseModel):
    kind: Literal["upload", "github"]
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
    seq: int = 0
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    completed_at: datetime | None = None

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

    @property
    def is_terminal(self) -> bool:
        return self.status in IMPORT_TERMINAL_STATUSES


class ExportState(_BundleJobState):
    export_id: UUID
    status: ExportStatus = ExportStatus.QUEUED
    with_data: bool = True
    include: list[str] | None = None
    staging_key: str | None = None
    bundle_filename: str | None = None
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
    repo_url: str | None = None
    repo_created: bool = False
    readme: str | None = None
    files: list[PublishFileProgress] = Field(default_factory=list)
    progress: Progress = Field(default_factory=Progress)

    @property
    def is_terminal(self) -> bool:
        return self.status in (PublishStatus.COMPLETED, PublishStatus.FAILED)
