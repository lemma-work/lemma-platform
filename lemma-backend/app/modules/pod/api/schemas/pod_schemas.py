from typing import Optional
from datetime import datetime
from uuid import UUID
from pydantic import EmailStr, Field, model_validator

from app.core.api.schemas import BaseSchema
from app.core.authorization.grants import ensure_grant_uses_resource_name
from app.modules.identity.contracts import OrganizationRole, UserResponse
from app.modules.pod.domain.pod_entities import (
    PodConfig,
    PodJoinRequestStatus,
    PodRole,
)
from app.core.authorization.context import ResourceType


class PodCreateRequest(BaseSchema):
    """Pod creation request schema."""

    organization_id: UUID
    name: str
    description: str | None = None
    icon_url: str | None = None
    config: PodConfig = Field(default_factory=PodConfig)


class PodUpdateRequest(BaseSchema):
    """Pod update request schema."""

    name: str | None = None
    description: str | None = None
    icon_url: str | None = None
    config: PodConfig | None = None


class PodResponse(BaseSchema):
    """Pod response schema."""

    id: UUID
    user_id: UUID
    organization_id: UUID
    name: str
    description: str | None = None
    icon_url: str | None = None
    config: PodConfig = Field(default_factory=PodConfig)
    created_at: datetime
    updated_at: datetime


class PodListResponse(BaseSchema):
    """Pod list response."""

    items: list[PodResponse]
    limit: int
    total: int
    next_page_token: Optional[str] = None


class PodMemberAddRequest(BaseSchema):
    """Pod member add request schema."""

    organization_member_id: UUID
    roles: list[str] = Field(..., min_length=1)


class PodMemberUpdateRoleRequest(BaseSchema):
    """Pod member role update request schema."""

    roles: list[str] = Field(..., min_length=1)


class PodMemberResponse(BaseSchema):
    """Pod member response schema."""

    pod_member_id: UUID
    user_id: UUID
    email: EmailStr
    user_email: EmailStr
    user_name: str | None = None
    roles: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class PodMemberDetailResponse(PodMemberResponse):
    """Pod member detail response schema."""

    user: UserResponse | None = None


class PodMemberListResponse(BaseSchema):
    """Pod member list response."""

    items: list[PodMemberResponse]
    limit: int
    total: int
    next_page_token: Optional[str] = None


class PodRoleCreateRequest(BaseSchema):
    """Pod custom role creation request."""

    name: str
    description: str | None = None
    permission_ids: list[str] = []


class PodRoleResponse(BaseSchema):
    """Pod role response."""

    id: UUID
    organization_id: UUID | None = None
    pod_id: UUID
    name: str
    description: str | None = None
    is_system: bool
    created_by_user_id: UUID | None = None
    created_at: datetime
    permission_ids: list[str] = []


class PodRoleListResponse(BaseSchema):
    """Pod role list response."""

    items: list[PodRoleResponse]


class PodPermissionResponse(BaseSchema):
    id: str
    scope: str
    resource_type: str | None = None
    description: str
    system_only: bool


class PodPermissionCatalogResponse(BaseSchema):
    items: list[PodPermissionResponse]


class PodEffectivePermissionsResponse(BaseSchema):
    pod_id: UUID
    actions: list[str]


class PodRoleResourcePermissionRequest(BaseSchema):
    resource_type: ResourceType
    resource_name: str
    permission_ids: list[str] = []

    @model_validator(mode="before")
    @classmethod
    def _require_resource_name(cls, data: object) -> object:
        return ensure_grant_uses_resource_name(data)


class PodRolePermissionsReplaceRequest(BaseSchema):
    grants: list[PodRoleResourcePermissionRequest] = []


class PodRoleResourcePermissionResponse(BaseSchema):
    resource_type: ResourceType
    resource_name: str
    permission_ids: list[str] = []


class PodRolePermissionsResponse(BaseSchema):
    role_id: UUID
    role_name: str
    grants: list[PodRoleResourcePermissionResponse] = []


class ResourceAccessGrantRequest(BaseSchema):
    permission_ids: list[str] = []


class ResourceAccessGrantResponse(BaseSchema):
    resource_type: ResourceType
    resource_name: str
    grantee_type: str
    grantee_id: UUID
    permission_ids: list[str] = []
    role_name: str | None = None
    user_id: UUID | None = None
    email: EmailStr | None = None
    display_name: str | None = None


class ResourceAccessResponse(BaseSchema):
    resource_type: ResourceType
    resource_name: str
    grants: list[ResourceAccessGrantResponse] = []


class PodJoinRequestCreateResponse(BaseSchema):
    id: UUID
    pod_id: UUID
    organization_id: UUID
    user_id: UUID
    status: PodJoinRequestStatus
    requested_at: datetime
    approved_at: datetime | None = None
    approved_by_user_id: UUID | None = None
    org_role: OrganizationRole | None = None
    pod_role: PodRole | None = None
    user_email: str | None = None
    user_name: str | None = None
    created_at: datetime
    updated_at: datetime


class PodJoinRequestApproveRequest(BaseSchema):
    org_role: OrganizationRole = OrganizationRole.ORG_MEMBER
    pod_role: PodRole = PodRole.USER


class PodJoinRequestListResponse(BaseSchema):
    items: list[PodJoinRequestCreateResponse]
    limit: int
    total: int
    next_page_token: Optional[str] = None
