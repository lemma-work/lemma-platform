"""App domain entities."""

from datetime import datetime
from enum import Enum
from typing import ClassVar
from uuid import UUID

from pydantic import BaseModel, Field

from app.core.authorization.context import ResourceType


class AppStatus(str, Enum):
    DRAFT = "DRAFT"
    READY = "READY"


class AppReleaseEntity(BaseModel):
    id: UUID | None = None
    app_id: UUID
    version: str
    dist_root_path: str
    dist_archive_path: str | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class AppEntity(BaseModel):
    resource_type: ClassVar[ResourceType] = ResourceType.APP

    id: UUID | None = None
    pod_id: UUID
    user_id: UUID
    name: str
    public_slug: str
    description: str | None = None
    source_archive_path: str | None = None
    current_release_id: UUID | None = None
    status: AppStatus = AppStatus.DRAFT
    # PUBLIC, not POD -- see the note on ``AppModel.visibility``.
    visibility: str = "PUBLIC"
    allowed_actions: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class AppUpdateEntity(BaseModel):
    description: str | None = None
    public_slug: str | None = None
    visibility: str | None = None


class AppBundleInfo(BaseModel):
    source_archive_path: str | None = None
    current_release_id: UUID | None = None
    status: AppStatus
    model_config = {"from_attributes": True}


class AppAssetDocument(BaseModel):
    content: bytes | str | None = None
    media_type: str = "application/octet-stream"
    etag: str | None = None
    not_modified: bool = False
    is_entrypoint: bool = False


def public_app_url(public_slug: str) -> str:
    """Where an app is served: ``<public_slug>.<app_base_domain>``.

    One definition, because two copies of a URL rule drift and each caller then
    describes a slightly different app. Host-based routing
    (``apps/api/host_routing.py``) is the other half of this contract.
    """
    from urllib.parse import urlparse

    from app.core.config import settings

    scheme = urlparse(settings.api_url).scheme or "https"
    return f"{scheme}://{public_slug}.{settings.app_base_domain}"
