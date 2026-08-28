from datetime import datetime
from uuid import UUID

from pydantic import EmailStr, Field, field_validator

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
    resolve_name_conflicts: bool = Field(
        default=False,
        description=(
            "Take the next free name instead of conflicting. For a name the "
            "user did not choose -- onboarding's derived first workspace -- "
            "where a 409 is a dead end for someone who never typed a name. "
            "Leave false for a name they typed, so a clash is reported."
        ),
    )


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
    """Organization slug availability response.

    ``available`` answers for the slug, which is the handle and is unique across
    the deployment. It is the only field that can refuse a create.

    ``name_available`` is answered whenever a candidate name is passed, and is
    now always ``true``: display names are labels and two organizations may
    share one (PS-ONB-014). Kept so callers that probe both fields keep one
    response shape, and deprecated -- do not gate a create on it.
    """

    slug: str
    available: bool
    name: str | None = None
    name_available: bool | None = Field(
        default=None,
        deprecated=True,
        description=(
            "Always true when a name is supplied: organization display names "
            "are not unique. Gate creates on `available` instead."
        ),
    )


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


class NavigationPodResponse(BaseSchema):
    """A pod as a listing entry — enough to draw it, label it, and link to it.

    The line this payload holds is scalars yes, collections no. A pod's own
    columns cost nothing to return: they ride along in the query that found the
    pod, so the response grows with the number of pods and not with what is
    inside them. Apps, agents and roles are the other side of that line, and
    live on ``/organizations/{org_id}/home``.
    """

    id: UUID
    name: str
    description: str | None = None
    icon_url: str | None = None
    #: When the pod record last changed — not when work last ran in it. The home
    #: screen sorts and labels on it, which is the only reason it is here.
    updated_at: datetime


class NavigationOrganizationResponse(BaseSchema):
    """An organization and the pods the caller can see inside it."""

    id: UUID
    name: str
    slug: str | None = None
    role: str
    pods: list[NavigationPodResponse]


class NavigationResponse(BaseSchema):
    """Everything a sidebar and a pod list need, for every organization, at once.

    Shallow in the sense that matters: it carries each pod's own columns, and
    nothing that would require looking inside a pod. Apps, agents and roles are
    the detail endpoint's job, because carrying them here would make the payload
    grow with the content of every organization a person happens to belong to,
    which is precisely the cost this endpoint exists to remove.
    """

    items: list[NavigationOrganizationResponse]


class HomeAppResponse(BaseSchema):
    id: UUID
    name: str
    description: str | None = None
    # None where the deployment serves no app host -- a desktop stack shared
    # over a tunnel serves the workspace and API on one public origin and no app
    # host at all. See `apps.domain.entities.public_app_url`.
    url: str | None = None
    status: str


class HomeAgentResponse(BaseSchema):
    id: UUID
    name: str
    description: str | None = None
    icon_url: str | None = None


class HomePodResponse(BaseSchema):
    """A pod with what it contains and what the caller is to it."""

    id: UUID
    name: str
    description: str | None = None
    icon_url: str | None = None
    #: Empty for an organization owner who can see the pod without having joined
    #: it — visibility and membership are not the same thing here.
    roles: list[str]
    apps: list[HomeAppResponse]
    agents: list[HomeAgentResponse]


class OrganizationHomeResponse(BaseSchema):
    """One organization's landing page in a single response."""

    organization_id: UUID
    name: str
    slug: str | None = None
    role: str
    pods: list[HomePodResponse]
