from datetime import datetime
from uuid import UUID

from pydantic import EmailStr, field_validator

from app.core.api.schemas import BaseSchema
from app.modules.identity.domain.email import normalize_identity_email

from app.modules.identity.domain.organization_entities import (
    OrganizationInvitationStatus,
    OrganizationJoinPolicy,
    OrganizationRole,
)
from app.modules.identity.api.schemas.user_schemas import UserResponse


class OrganizationCreateRequest(BaseSchema):
    """Organization creation request schema."""

    name: str
    slug: str | None = None
    email_domain: str | None = None
    join_policy: OrganizationJoinPolicy = OrganizationJoinPolicy.INVITE_ONLY


class OrganizationUpdateRequest(BaseSchema):
    """Organization update request schema (owner-only)."""

    name: str | None = None
    email_domain: str | None = None
    join_policy: OrganizationJoinPolicy | None = None


class OrganizationResponse(BaseSchema):
    """Organization response schema."""

    id: UUID
    name: str
    slug: str
    email_domain: str | None = None
    join_policy: OrganizationJoinPolicy
    created_at: datetime
    updated_at: datetime


class OrganizationMemberResponse(BaseSchema):
    """Organization member response schema."""

    id: UUID
    user_id: UUID
    organization_id: UUID
    role: OrganizationRole
    user: UserResponse | None = None
    created_at: datetime
    updated_at: datetime


class OrganizationInvitationRequest(BaseSchema):
    """Organization invitation request schema."""

    email: EmailStr
    role: OrganizationRole
    pod_id: UUID | None = None
    pod_role: str | None = None
    redirect_uri: str | None = None

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: object) -> str:
        return normalize_identity_email(str(value))


class OrganizationInvitationResponse(BaseSchema):
    """Organization invitation response schema."""

    id: UUID
    email: EmailStr
    organization_id: UUID
    organization_name: str | None = None
    role: OrganizationRole
    pod_id: UUID | None = None
    pod_role: str | None = None
    redirect_uri: str | None = None
    pod_name: str | None = None
    pod_description: str | None = None
    status: OrganizationInvitationStatus
    expires_at: datetime
    accepted_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class UpdateMemberRoleRequest(BaseSchema):
    """Update member role request schema."""

    role: OrganizationRole


class OrganizationListResponse(BaseSchema):
    """Organization list response with pagination."""

    items: list[OrganizationResponse]
    limit: int
    next_page_token: str | None = None


class OrganizationSlugAvailabilityResponse(BaseSchema):
    """Organization slug availability response."""

    slug: str
    available: bool


class OrganizationMemberListResponse(BaseSchema):
    """Organization member list response with pagination."""

    items: list[OrganizationMemberResponse]
    limit: int
    next_page_token: str | None = None


class OrganizationInvitationListResponse(BaseSchema):
    """Organization invitation list response with pagination."""

    items: list[OrganizationInvitationResponse]
    limit: int
    next_page_token: str | None = None


class OrganizationMessageResponse(BaseSchema):
    """Generic organization message response."""

    message: str
    success: bool = True
    redirect_uri: str | None = None
