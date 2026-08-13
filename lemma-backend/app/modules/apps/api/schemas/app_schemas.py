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


class AppMessageResponse(BaseModel):
    message: str


class AppBundleUploadResponse(BaseModel):
    message: str
    app: AppDetailResponse


class UploadAppBundleForm(BaseModel):
    source_archive: str | None = Field(default=None)
    dist_archive: str | None = Field(default=None)
