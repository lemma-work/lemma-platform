"""Request/response models for the pod bundle API."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.pod_bundle.domain.state import ExportState, ExportStatus, Progress


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
