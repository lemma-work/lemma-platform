"""Job-state models for pod bundle operations.

All import, export, and publish snapshots and checkpoints are authoritative in
PostgreSQL and mirrored to Redis. Every accepted compare-and-swap write bumps
``version`` and ``seq`` so workers and realtime clients can reject stale state.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

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


def part_key(kind: StepKind, name: str) -> str:
    """How :attr:`ImportState.created` names one part: ``AGENT:triage``."""
    return f"{kind.value}:{name}"


#: The steps that create one named part. A FILE step is left out: a file has
#: no later step that writes to it, and a bundle may carry thousands.
CREATING_KINDS = frozenset(
    {
        StepKind.TABLE,
        StepKind.TABLE_DATA,
        StepKind.FUNCTION,
        StepKind.AGENT,
        StepKind.WORKFLOW,
        StepKind.SCHEDULE,
        StepKind.APP,
        StepKind.SURFACE,
    }
)


class VariableSpec(BaseModel):
    """A ``${var}`` placeholder declared by the bundle that the importer may
    (or must) resolve — pod member references, app slugs."""

    name: str
    kind: Literal["pod_member", "free"] = "free"
    description: str | None = None
    required: bool = False
    default: str | None = None


class OutcomePlanAction(str, Enum):
    """What an import does with what its bundle's pod owned."""

    #: The pod owns nothing yet: the import sets the bundle's.
    SET = "SET"
    #: The pod already owns something else, and keeps it: an import adds to a
    #: pod, it does not change what the pod is for.
    KEEP = "KEEP"
    #: The pod already owns this one (sentence, kind, target, unit and due).
    SAME = "SAME"


class PlannedOutcome(BaseModel):
    """What the bundle's pod owned (``pod.json -> outcome``), and what the
    import will do with it -- shown so the person approving sees it first.

    Its measure is not repeated: it is a query only the apply reads, as the
    person importing, and one they cannot read is left off then.
    """

    sentence: str
    kind: Literal["week", "date"]
    target: float | None = None
    unit: str | None = None
    due: date | None = None
    has_measure: bool = False
    action: OutcomePlanAction


class ImportPlan(BaseModel):
    format_version: int
    bundle_name: str | None = None
    # `pod.json`'s description, so the person importing can see what the bundle
    # is for before approving it.
    description: str | None = None
    steps: list[PlanStep] = Field(default_factory=list)
    variables: list[VariableSpec] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    #: The bundle's pod connectors and the choices each offers the importer.
    connectors: list[ConnectorPlan] = Field(default_factory=list)
    #: What the bundle's pod owned and what the import does with it; ``None``
    #: when it owned nothing, its block is invalid (said in ``warnings``), or
    #: the import is a template's, whose start sets it.
    outcome: PlannedOutcome | None = None

    @field_validator("description", mode="before")
    @classmethod
    def _text_or_nothing(cls, value: object) -> object:
        """A ``pod.json`` description that is not text is no description: the
        bundle is someone else's file, and one stray field must not fail a plan."""
        return value if isinstance(value, str) else None

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
    repo fetched via the connector path; ``TEMPLATE`` is a bundle that ships
    with the backend under ``pod_bundle/templates/``, named rather than located.
    """

    URL = "URL"
    GITHUB = "GITHUB"
    TEMPLATE = "TEMPLATE"


#: A template's name is its directory under ``templates/``. No dots and no
#: separators, so a name cannot address anything outside that directory.
TEMPLATE_NAME_PATTERN = r"^[a-z0-9][a-z0-9-]{0,62}$"

#: A folder inside a repository, e.g. ``install/mail``: relative, ordinary
#: characters only, and no segment made of dots alone, so neither ``.`` nor
#: ``..`` can address anything outside the repository. Written without
#: look-around, which pydantic's regex engine does not support.
_PATH_SEGMENT = r"[A-Za-z0-9._-]*[A-Za-z0-9_-][A-Za-z0-9._-]*"
BUNDLE_PATH_PATTERN = rf"^{_PATH_SEGMENT}(?:/{_PATH_SEGMENT})*$"
BUNDLE_PATH_MAX_LENGTH = 255


class BundleSource(BaseModel):
    kind: BundleSourceKind
    # For kind=URL: the signed lemma download URL the bundle was imported from
    # (kept for the recipe/provenance). For kind=GITHUB: repo_url is set instead,
    # and `path` when the bundle is a folder inside the repository.
    # For kind=TEMPLATE: `template:<name>`, which is what the recipe records.
    url: str | None = None
    repo_url: str | None = None
    path: str | None = None
    template: str | None = None
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


class InstallSlot(BaseModel):
    """An import's place in a plugin install: several bundles one request
    queued for one pod, which the server works through in order with the
    install page's defaults (``application/install_use_cases``)."""

    install_id: UUID
    position: int
    #: The question the bundle's install page would ask, when the install
    #: withdrew the import rather than guess the answer. Nothing was applied.
    asks: str | None = None


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
    #: The parts this import created (:func:`part_key`), and the tables whose
    #: rows it loaded (``TABLE_DATA:``). Everything else the pod has is kept as
    #: it is, so this is what tells a grants step or a seed step that the part
    #: it writes to is the import's own -- across a crash, a failed step, and a
    #: replan, which plans afresh against a pod already holding them.
    created: list[str] = Field(default_factory=list)
    #: Set when a plugin install queued this import; ``None`` for one a person
    #: started and applies themselves.
    install: InstallSlot | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in IMPORT_TERMINAL_STATUSES

    def apply_failure_message(self, step: PlanStep, exc: Exception) -> str:
        """Say what the pod now holds, not just which step broke.

        Apply commits each step in its own unit of work and there is no
        rollback, so a failure part-way through an approved plan leaves the pod
        changed. Telling the person only "Step X failed" leaves them with half a
        pod, no idea that it is half a pod, and no stated way forward -- while
        re-applying does in fact resume from the failed step rather than
        duplicating the work already done.
        """
        applied = len(self.committed_steps)
        total = len(self.plan.steps) if self.plan else 0
        head = f"Step '{step.name}' failed: {exc}"
        if not applied:
            return f"{head} Nothing was applied to the pod."
        return (
            f"{head} {applied} of {total} steps were already applied to this pod "
            f"and were not undone; apply this import again to continue from step "
            f"'{step.name}'."
        )

    def cancelled_part_way_message(self) -> str:
        """What a person who cancelled mid-apply is told: the steps that
        landed were not undone, and importing again will not finish them.

        A new import keeps everything already there, this one's half-made
        parts included, and plans its grants and rows only for what it creates.
        """
        applied = len(self.committed_steps)
        total = len(self.plan.steps) if self.plan else 0
        return (
            f"Cancelled after {applied} of {total} steps had been applied to this "
            "pod. Those changes were not undone, and importing the bundle again "
            "adds only what is still missing: to start clean, remove what this "
            "import created first."
        )

    def remember_finished_creates(self) -> None:
        """Add each part a finished CREATE step made to :attr:`created`.

        An applier records a part as it creates it; this covers a step that
        finished before the record existed, and keeps what a plan made before
        a replan discards it. A CREATE step only finishes DONE on a part it
        made: one it finds someone else's is marked SKIPPED."""
        for step in self.plan.steps if self.plan else ():
            key = part_key(step.kind, step.name)
            if (
                step.kind in CREATING_KINDS
                and step.action is StepAction.CREATE
                and step.status is StepStatus.DONE
                and key not in self.created
            ):
                self.created.append(key)


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
