from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field, computed_field

from app.modules.apps.domain.entities import AppStatus, public_app_url


class CreateAppRequest(BaseModel):
    name: str
    public_slug: str | None = None
    description: Optional[str] = None
    visibility: str | None = None


class UpdateAppRequest(BaseModel):
    description: Optional[str] = None
    public_slug: Optional[str] = None
    visibility: str | None = None


class CreateAppFromWidgetRequest(BaseModel):
    """Promote a conversation widget into a persisted app.

    The widget's stored source fragment (addressed by conversation + tool call) is
    preserved, wrapped into a standalone document without embed-only chrome, and
    deployed as the app's bundle.
    """

    conversation_id: UUID
    tool_call_id: str
    name: str
    public_slug: str | None = None
    description: Optional[str] = None
    visibility: str | None = None


class AppResponse(BaseModel):
    id: UUID
    pod_id: UUID
    user_id: UUID
    name: str
    public_slug: str
    description: Optional[str] = None
    source_archive_path: Optional[str] = None
    current_release_id: Optional[UUID] = None
    status: AppStatus
    visibility: str = "PUBLIC"
    created_at: Any
    updated_at: Any

    model_config = {"from_attributes": True}

    @computed_field(return_type=str)
    @property
    def url(self) -> str:
        return public_app_url(self.public_slug)


class AppDetailResponse(AppResponse):
    allowed_actions: list[str] = Field(default_factory=list)


class AppListResponse(BaseModel):
    items: list[AppDetailResponse]
    limit: int
    next_page_token: Optional[str] = None


class AppReleaseResponse(BaseModel):
    """One entry in an app's release history."""

    id: UUID
    app_id: UUID
    release_number: int
    version: str = Field(description="sha256 digest of the release's dist archive.")
    label: Optional[str] = None
    created_by: Optional[UUID] = None
    created_at: Any
    is_live: bool = Field(
        description="True for the release this app currently serves."
    )
    has_source: bool = Field(
        description="Whether this release's own source archive is still stored."
    )
    pruned_at: Any = Field(
        default=None,
        description=(
            "Set when retention removed this release's build. The entry stays "
            "in the history, but it can no longer be previewed or promoted."
        ),
    )

    @computed_field(return_type=str)
    @property
    def preview_url(self) -> str:
        # Through `public_app_url`, not a second copy of the scheme-and-domain
        # rule: a preview host is the live host with the release in its label,
        # so the two must never be able to disagree about the rest of it.
        return public_app_url(f"{self.app_public_slug}--r{self.release_number}")

    # Carried so `preview_url` can be computed without a second app lookup.
    app_public_slug: str = Field(exclude=True)


class AppReleaseListResponse(BaseModel):
    items: list[AppReleaseResponse]


class AppMessageResponse(BaseModel):
    message: str


class AppBundleUploadResponse(BaseModel):
    message: str
    app: AppDetailResponse


class UploadAppBundleForm(BaseModel):
    source_archive: str | None = Field(default=None)
    dist_archive: str | None = Field(default=None)
