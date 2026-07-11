"""Request/response models for the pod bundle API."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.pod_bundle.domain.state import (
    BundleSourceKind,
    ExportState,
    ExportStatus,
    ImportPlan,
    ImportState,
    ImportStatus,
    Progress,
    PublishState,
    PublishStatus,
)


class ExportStartRequest(BaseModel):
    """Body for starting a pod export."""

    with_data: bool = Field(
        default=False,
        description=(
            "Opt-in: include row data for EVERY table (data.csv per table) as "
            "seed/default data. Off by default — an export carries only pod "
            "resources, which recreate the pod in an empty-table state. Prefer "
            "`data_tables` to seed only specific setup tables; enable this only to "
            "seed the whole pod. Row data is capped (per-table + total) regardless."
        ),
    )
    data_tables: list[str] | None = Field(
        default=None,
        description=(
            "Opt-in per-table seed selection: include row data only for these named "
            "tables (the common case — ship a few setup/config tables' rows). "
            "Ignored for names that aren't real tables (a warning is surfaced). "
            "Combined with `with_data` as a union; omit both for a resources-only "
            "export."
        ),
    )
    with_files: bool = Field(
        default=False,
        description=(
            "Opt-in: include the pod's POD-visible file storage (folders + file "
            "bytes) in the bundle. Off by default. File bytes share a conservative "
            "size budget with table row data (meant for small skill/script/seed "
            "files, not a bulk file dump)."
        ),
    )
    include: list[str] | None = Field(
        default=None,
        description=(
            "Optional list of resource types to include (e.g. ['tables', "
            "'agents']). Omit to export every supported resource type."
        ),
    )
    ttl_seconds: int | None = Field(
        default=None,
        description=(
            "Requested lifetime (seconds) of the signed download URL + archive "
            "retention. Clamped to the configured maximum; omit for the default."
        ),
    )


class ExportProgressResponse(BaseModel):
    done: int = 0
    total: int = 0

    @classmethod
    def from_domain(cls, progress: Progress) -> "ExportProgressResponse":
        return cls(done=progress.done, total=progress.total)


class ExportStatusResponse(BaseModel):
    """Status of a pod export job (pure Redis read)."""

    export_id: UUID
    status: ExportStatus
    progress: ExportProgressResponse = Field(default_factory=ExportProgressResponse)
    bundle_filename: str | None = None
    download_url: str | None = Field(
        default=None,
        description=(
            "Signed, authenticated download URL; present once the export is "
            "READY. Requires a logged-in lemma user to fetch."
        ),
    )
    expires_at: datetime | None = Field(
        default=None, description="When the download URL (and archive) expires."
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Data/asset-cap notices (e.g. truncated seed rows, skipped files).",
    )
    error: str | None = None

    @classmethod
    def from_state(cls, state: ExportState) -> "ExportStatusResponse":
        return cls(
            export_id=state.export_id,
            status=state.status,
            progress=ExportProgressResponse.from_domain(state.progress),
            bundle_filename=state.bundle_filename,
            download_url=state.download_url,
            expires_at=state.expires_at,
            warnings=state.warnings,
            error=state.error,
        )


# --- import ------------------------------------------------------------------


class PlanStepResponse(BaseModel):
    index: int
    kind: str
    name: str
    action: str
    destructive: bool = False
    detail: dict = Field(default_factory=dict)
    status: str = "PENDING"
    error: str | None = None


class VariableSpecResponse(BaseModel):
    name: str
    kind: str
    description: str | None = None
    required: bool = False
    default: str | None = None
    connector: str | None = Field(
        default=None,
        description=(
            "For a connector account variable, the connector the account must "
            "belong to (e.g. 'slack'), so the importer can connect the right "
            "connector. Null for non-connector variables."
        ),
    )
    provider: str | None = Field(
        default=None,
        description=(
            "For a connector account variable, the auth provider backing the "
            "connector ('LEMMA' or 'COMPOSIO'), so the importer connects/selects "
            "an account through the right provider. Null for non-connector "
            "variables."
        ),
    )


class ImportPlanResponse(BaseModel):
    format_version: int
    bundle_name: str | None = None
    steps: list[PlanStepResponse] = Field(default_factory=list)
    variables: list[VariableSpecResponse] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    has_destructive_steps: bool = False

    @classmethod
    def from_domain(cls, plan: ImportPlan) -> "ImportPlanResponse":
        return cls(
            format_version=plan.format_version,
            bundle_name=plan.bundle_name,
            steps=[
                PlanStepResponse(
                    index=s.index,
                    kind=s.kind.value,
                    name=s.name,
                    action=s.action.value,
                    destructive=s.destructive,
                    detail=s.detail,
                    status=s.status.value,
                    error=s.error,
                )
                for s in plan.steps
            ],
            variables=[
                VariableSpecResponse(
                    name=v.name,
                    kind=v.kind,
                    description=v.description,
                    required=v.required,
                    default=v.default,
                    connector=v.connector,
                    provider=v.provider,
                )
                for v in plan.variables
            ],
            warnings=plan.warnings,
            has_destructive_steps=plan.has_destructive_steps,
        )


class ImportStartRequest(BaseModel):
    """Body for starting a URL-based import."""

    kind: BundleSourceKind = Field(
        ..., description="URL (a lemma signed download URL) or GITHUB (a public repo)."
    )
    url: str | None = Field(
        default=None,
        description=(
            "For URL: a lemma bundle download URL (from an export or an upload). "
            "For GITHUB: the repo URL (alternative to owner+repo)."
        ),
    )
    owner: str | None = Field(default=None, description="GITHUB repo owner.")
    repo: str | None = Field(default=None, description="GITHUB repo name.")
    ref: str | None = Field(default=None, description="GITHUB branch/tag/sha (optional).")
    account_id: UUID | None = Field(
        default=None, description="Connector account for a private GitHub repo."
    )


class UploadResponse(BaseModel):
    """Result of staging a local .zip: a signed URL to feed the URL-based import."""

    url: str = Field(..., description="Signed lemma download URL (pass as kind=URL).")
    expires_at: datetime


class ApplyImportRequest(BaseModel):
    """Body for applying a planned import."""

    variables: dict[str, str] = Field(
        default_factory=dict,
        description="Resolved values for the plan's ${var} placeholders.",
    )
    confirm_destructive: bool = Field(
        default=False,
        description="Required to proceed when the plan has destructive steps.",
    )


class ImportStatusResponse(BaseModel):
    """Status of a durable pod import job."""

    import_id: UUID
    pod_id: UUID
    status: ImportStatus
    source_kind: str
    plan: ImportPlanResponse | None = None
    progress: ExportProgressResponse = Field(default_factory=ExportProgressResponse)
    events_url: str
    error: str | None = None
    cancel_requested_at: datetime | None = None
    current_step: int | None = None
    committed_steps: list[int] = Field(default_factory=list)

    @classmethod
    def from_state(cls, state: ImportState) -> "ImportStatusResponse":
        return cls(
            import_id=state.import_id,
            pod_id=state.pod_id,
            status=state.status,
            source_kind=state.source.kind.value,
            plan=ImportPlanResponse.from_domain(state.plan) if state.plan else None,
            progress=ExportProgressResponse.from_domain(state.progress),
            events_url=(
                f"/pods/{state.pod_id}/bundle/imports/{state.import_id}/events"
            ),
            error=state.error,
            cancel_requested_at=state.cancel_requested_at,
            current_step=state.current_step,
            committed_steps=state.committed_steps,
        )


# --- publish -----------------------------------------------------------------


class PublishStartRequest(BaseModel):
    """Body for publishing a pod to GitHub."""

    repo_name: str = Field(..., min_length=1, description="Name for the new GitHub repo.")
    private: bool = Field(default=False, description="Create the repo as private.")
    account_id: UUID | None = Field(
        default=None, description="GitHub connector account to publish as (optional)."
    )
    ai_readme: bool = Field(
        default=False, description="Polish the generated README with the system model."
    )


class PublishStatusResponse(BaseModel):
    """Status of a pod publish job (pure Redis read)."""

    publish_id: UUID
    pod_id: UUID
    status: PublishStatus
    repo_name: str
    repo_url: str | None = None
    progress: ExportProgressResponse = Field(default_factory=ExportProgressResponse)
    events_url: str
    error: str | None = None

    @classmethod
    def from_state(cls, state: PublishState) -> "PublishStatusResponse":
        return cls(
            publish_id=state.publish_id,
            pod_id=state.pod_id,
            status=state.status,
            repo_name=state.repo_name,
            repo_url=state.repo_url,
            progress=ExportProgressResponse.from_domain(state.progress),
            events_url=f"/pods/{state.pod_id}/bundle/publishes/{state.publish_id}/events",
            error=state.error,
        )
