"""Request/response models for the pod bundle API."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.pod_bundle.domain.state import (
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
        default=True,
        description="Include table row data (data.csv per table) in the bundle.",
    )
    include: list[str] | None = Field(
        default=None,
        description=(
            "Optional list of resource types to include (e.g. ['tables', "
            "'agents']). Omit to export every supported resource type."
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
        description="Relative download path; present once the export is READY.",
    )
    error: str | None = None

    @classmethod
    def from_state(cls, state: ExportState) -> "ExportStatusResponse":
        download_url: str | None = None
        if state.status == ExportStatus.READY:
            download_url = (
                f"/pods/{state.pod_id}/bundle/exports/{state.export_id}/download"
            )
        return cls(
            export_id=state.export_id,
            status=state.status,
            progress=ExportProgressResponse.from_domain(state.progress),
            bundle_filename=state.bundle_filename,
            download_url=download_url,
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
                )
                for v in plan.variables
            ],
            warnings=plan.warnings,
            has_destructive_steps=plan.has_destructive_steps,
        )


class GithubImportRequest(BaseModel):
    """Body for importing a pod from a public GitHub repo."""

    repo_url: str | None = Field(
        default=None, description="Public repo URL, e.g. https://github.com/owner/repo."
    )
    owner: str | None = Field(default=None, description="Repo owner (alternative to repo_url).")
    repo: str | None = Field(default=None, description="Repo name (alternative to repo_url).")
    ref: str | None = Field(default=None, description="Branch, tag, or commit sha (optional).")


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
    """Status of a pod import job (pure Redis read)."""

    import_id: UUID
    pod_id: UUID
    status: ImportStatus
    source_kind: str
    plan: ImportPlanResponse | None = None
    progress: ExportProgressResponse = Field(default_factory=ExportProgressResponse)
    events_url: str
    error: str | None = None

    @classmethod
    def from_state(cls, state: ImportState) -> "ImportStatusResponse":
        return cls(
            import_id=state.import_id,
            pod_id=state.pod_id,
            status=state.status,
            source_kind=state.source.kind,
            plan=ImportPlanResponse.from_domain(state.plan) if state.plan else None,
            progress=ExportProgressResponse.from_domain(state.progress),
            events_url=(
                f"/pods/{state.pod_id}/bundle/imports/{state.import_id}/events"
            ),
            error=state.error,
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
