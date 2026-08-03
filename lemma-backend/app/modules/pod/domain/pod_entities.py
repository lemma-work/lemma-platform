from datetime import datetime, timezone
from enum import Enum
from typing import Mapping, Optional
from uuid import UUID
from pydantic import BaseModel, Field, ValidationError, field_validator, model_serializer, model_validator
from app.core.domain.aggregate import AggregateRoot
from app.core.domain.runtime import AgentRuntimeConfig
from app.modules.identity.contracts import OrganizationRole, UserEntity
from app.modules.pod.domain.roles import PodRole


class PodJoinPolicy(str, Enum):
    """Who may self-join a pod, ordered from closed to open."""

    INVITE_ONLY = "INVITE_ONLY"  # default — invite or approved join-request only
    ORG_MEMBERS = "ORG_MEMBERS"  # any member of the pod's org may self-join
    PUBLIC = "PUBLIC"  # any Lemma user may self-join (auto-added to the org)


class PodRecipe(BaseModel):
    """A record of a bundle installed into this pod (the durable trace of an
    import; the ephemeral import job state is not kept). ``kind`` distinguishes an
    uploaded bundle from a GitHub-sourced one; ``repo_url`` is set for GitHub."""

    kind: str  # "upload" | "github"
    name: str | None = None
    repo_url: str | None = None
    format_version: int | None = None
    imported_at: datetime
    imported_by: UUID


class PodConfig(BaseModel):
    """Typed pod-level configuration."""

    # ``default_profile_id`` is the legacy provider-only default (no model). It is
    # kept for backward compatibility — old pods, old clients, and any code still
    # reading the raw key — and is mirrored from ``default_runtime`` on write.
    # New code should set/read ``default_runtime`` (profile + optional model).
    default_profile_id: str | None = Field(default=None, min_length=1)
    default_runtime: AgentRuntimeConfig | None = None
    join_policy: PodJoinPolicy = PodJoinPolicy.INVITE_ONLY
    # Bundles installed into this pod, appended on each successful import. Omitted
    # from the serialized config when empty so legacy config blobs are unchanged.
    recipes: list[PodRecipe] = Field(default_factory=list)

    @field_validator("default_profile_id")
    @classmethod
    def normalize_default_profile_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        profile_id = value.strip()
        if not profile_id:
            raise ValueError("default_profile_id cannot be empty")
        return profile_id

    @classmethod
    def from_raw(cls, config: object) -> "PodConfig":
        """Parse a raw stored config blob, tolerating legacy/malformed shapes."""
        if isinstance(config, Mapping):
            try:
                return cls.model_validate(dict(config))
            except ValidationError:
                return cls()
        return cls()

    def resolved_default_runtime(self) -> AgentRuntimeConfig | None:
        """The pod's default runtime, preferring the full ``default_runtime``.

        Falls back to the legacy ``default_profile_id`` (model unset == use the
        profile's own default model), so old pods resolve exactly as before.
        Returns ``None`` when the pod pins no default.
        """
        if self.default_runtime is not None:
            return self.default_runtime
        if self.default_profile_id:
            return AgentRuntimeConfig(profile_id=self.default_profile_id)
        return None

    @model_serializer(mode="wrap")
    def serialize_without_empty_defaults(self, handler):
        data = handler(self)
        if data.get("default_profile_id") is None:
            data.pop("default_profile_id", None)
        if data.get("default_runtime") is None:
            data.pop("default_runtime", None)
        if not data.get("recipes"):
            data.pop("recipes", None)
        return data


class PodEntity(AggregateRoot):
    """Pod entity."""

    user_id: UUID
    organization_id: UUID
    name: str
    description: Optional[str] = None
    icon_url: Optional[str] = None
    config: PodConfig = Field(default_factory=PodConfig)
    is_deleted: bool = False

    def mark_created(self, creator_id: UUID) -> None:
        """Add pod created event to aggregate."""
        from app.modules.pod.domain.events import PodCreatedEvent

        self.add_event(
            PodCreatedEvent(
                pod_id=self.id,
                organization_id=self.organization_id,
                creator_id=creator_id,
                name=self.name,
            )
        )

    def mark_deleted(self) -> None:
        """Soft delete aggregate and emit deletion event for downstream cleanup."""
        from app.modules.pod.domain.events import PodDeletedEvent

        self.is_deleted = True
        self.add_event(
            PodDeletedEvent(
                pod_id=self.id,
                organization_id=self.organization_id,
            )
        )

class PodUpdateEntity(BaseModel):
    """Pod update entity."""

    name: Optional[str] = None
    description: Optional[str] = None
    icon_url: Optional[str] = None
    config: PodConfig | None = None


class PodMemberEntity(AggregateRoot):
    """Pod member entity."""

    pod_id: UUID
    organization_member_id: UUID
    roles: list[str] = Field(default_factory=list)
    user_id: UUID | None = None
    user_email: str | None = None
    user_name: str | None = None
    user: UserEntity | None = None

    @property
    def pod_member_id(self) -> UUID:
        return self.id

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_role_input(cls, data):
        if isinstance(data, dict) and not data.get("roles") and data.get("role"):
            role = data.get("role")
            data = dict(data)
            data["roles"] = [role.value if isinstance(role, PodRole) else str(role)]
        return data

    @property
    def role(self) -> PodRole:
        from app.modules.pod.domain.visibility import highest_role, normalize_role_list

        normalized = normalize_role_list(self.roles)
        if not normalized:
            return PodRole.USER
        return PodRole(highest_role(normalized))

    @property
    def email(self) -> str | None:
        return self.user_email

    def assign_role(self, role: PodRole) -> None:
        self.roles = [role.value]

    def assign_roles(self, roles: list[str]) -> None:
        from app.modules.pod.domain.visibility import normalize_role_list

        self.roles = normalize_role_list(roles)

    def mark_added(
        self,
        *,
        user_id: UUID,
        email: str,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> None:
        from app.modules.pod.domain.events import PodMemberAddedEvent

        self.add_event(
            PodMemberAddedEvent(
                pod_id=self.pod_id,
                user_id=user_id,
                role=self.role.value,
                email=email,
                first_name=first_name,
                last_name=last_name,
            )
        )

    def mark_removed(self, *, user_id: UUID) -> None:
        from app.modules.pod.domain.events import PodMemberRemovedEvent

        self.add_event(
            PodMemberRemovedEvent(
                pod_id=self.pod_id,
                user_id=user_id,
            )
        )


class PodJoinRequestStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class PodJoinRequestEntity(AggregateRoot):
    pod_id: UUID
    organization_id: UUID
    user_id: UUID
    status: PodJoinRequestStatus = PodJoinRequestStatus.PENDING
    requested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    approved_at: datetime | None = None
    approved_by_user_id: UUID | None = None
    org_role: OrganizationRole | None = None
    pod_role: PodRole | None = None
    # Display-only — populated from the users table on read, never persisted here.
    user_email: str | None = None
    user_name: str | None = None

    def mark_requested(self) -> None:
        """Emit join-requested event so pod admins can be notified."""
        from app.modules.pod.domain.events import PodJoinRequestedEvent

        self.add_event(
            PodJoinRequestedEvent(
                pod_id=self.pod_id,
                organization_id=self.organization_id,
                requester_user_id=self.user_id,
                join_request_id=self.id,
            )
        )

    def mark_approved(
        self,
        *,
        approved_by_user_id: UUID,
        approved_at: datetime | None = None,
        org_role: OrganizationRole,
        pod_role: PodRole,
    ) -> None:
        self.status = PodJoinRequestStatus.APPROVED
        self.approved_by_user_id = approved_by_user_id
        self.approved_at = approved_at or datetime.now(timezone.utc)
        self.org_role = org_role
        self.pod_role = pod_role


class ResourceAccessRequestStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ResourceAccessRequestEntity(AggregateRoot):
    """Someone asking for one resource, rather than for the whole pod.

    ``PodJoinRequest`` was the only way to ask for anything, and approving it
    mints pod membership with a default role — wildly more than a sharer intends
    when the ask was "let me read this document". Approving one of these writes a
    single resource grant instead, and the requester stays a non-member.
    """

    pod_id: UUID
    resource_type: str
    resource_id: UUID
    # Denormalized so a listing can name what was asked for without resolving
    # every resource type; the id stays authoritative across renames.
    resource_name: str | None = None
    requester_user_id: UUID
    # What was asked for. Defaults to the read action at the API layer, which is
    # the only thing a guest can ask for today.
    requested_permission_ids: list[str] = Field(default_factory=list)
    status: ResourceAccessRequestStatus = ResourceAccessRequestStatus.PENDING
    message: str | None = None
    requested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    decided_at: datetime | None = None
    decided_by_user_id: UUID | None = None
    # Display-only — populated from the users table on read, never persisted.
    requester_email: str | None = None
    requester_name: str | None = None

    def mark_approved(
        self,
        *,
        decided_by_user_id: UUID,
        decided_at: datetime | None = None,
    ) -> None:
        self.status = ResourceAccessRequestStatus.APPROVED
        self.decided_by_user_id = decided_by_user_id
        self.decided_at = decided_at or datetime.now(timezone.utc)

    def mark_rejected(
        self,
        *,
        decided_by_user_id: UUID,
        decided_at: datetime | None = None,
    ) -> None:
        self.status = ResourceAccessRequestStatus.REJECTED
        self.decided_by_user_id = decided_by_user_id
        self.decided_at = decided_at or datetime.now(timezone.utc)


class ResourceAccessInviteStatus(str, Enum):
    PENDING = "PENDING"
    REDEEMED = "REDEEMED"
    REVOKED = "REVOKED"


class ResourceAccessInviteEntity(AggregateRoot):
    """A grant waiting for an account to attach itself to.

    Resource grants key on a user id, which someone who has never signed in does
    not have. Sharing with them would otherwise mean inviting them to the
    organization first — a much larger door than the one being opened. This holds
    the intended permissions against an email until an account with that address
    exists, then becomes an ordinary USER grant.
    """

    pod_id: UUID
    resource_type: str
    resource_id: UUID
    resource_name: str | None = None
    email: str
    permission_ids: list[str] = Field(default_factory=list)
    status: ResourceAccessInviteStatus = ResourceAccessInviteStatus.PENDING
    invited_by_user_id: UUID | None = None
    invited_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    redeemed_at: datetime | None = None
    redeemed_by_user_id: UUID | None = None

    def mark_redeemed(self, *, user_id: UUID) -> None:
        self.status = ResourceAccessInviteStatus.REDEEMED
        self.redeemed_by_user_id = user_id
        self.redeemed_at = datetime.now(timezone.utc)
