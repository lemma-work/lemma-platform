"""Request and response shapes for pod connectors and pod accounts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.modules.connectors.api.schemas.strict_patch import StrictPatch

from app.modules.connectors.domain.pod_connector import (
    SLUG_PATTERN,
    PodAccountSource,
    PodAccountVisibility,
    PodConnectorMode,
    PodConnectorSource,
    PodConnectorStatus,
    PodConnectorVisibility,
)


class PodConnectorCreateRequest(BaseModel):
    slug: str | None = Field(
        default=None,
        description=(
            "Pod-local name apps, agents and bundles address it by, e.g. "
            "`gmail` or `support-inbox`. Left out: the installation's name, "
            "made free in this pod."
        ),
    )
    auth_config_id: UUID | None = Field(
        default=None,
        description=(
            "The organization installation this connector uses. Give this or "
            "`connector_id`."
        ),
    )
    connector_id: str | None = Field(
        default=None,
        description=(
            "A catalog connector (`gmail`) instead of an installation: its "
            "organization default, or -- when the organization has none and you "
            "may install connectors -- Lemma's own app for it, installed now."
        ),
    )
    mode: PodConnectorMode = Field(
        default=PodConnectorMode.PER_MEMBER,
        description=(
            "PER_MEMBER (the default): each call uses the acting member's own "
            "account. SHARED: every call uses the one account set as "
            "`shared_pod_account_id`."
        ),
    )
    shared_pod_account_id: UUID | None = Field(
        default=None,
        description=(
            "SHARED only: a pod account its owner has already shared with the pod."
        ),
    )
    required: bool = Field(
        default=False,
        description="PER_MEMBER: ask every member who has no account to connect one.",
    )
    purpose: str | None = Field(
        default=None,
        max_length=500,
        description="Shown to members when they are asked to connect.",
    )
    visibility: PodConnectorVisibility = PodConnectorVisibility.POD

    @field_validator("slug")
    @classmethod
    def _slug(cls, value: str | None) -> str | None:
        if value is not None and not SLUG_PATTERN.match(value):
            raise ValueError(
                "slug must be lowercase letters, digits, '-' or '_', starting "
                "with a letter or digit, at most 63 characters"
            )
        return value

    @model_validator(mode="after")
    def _one_installation(self) -> PodConnectorCreateRequest:
        if (self.auth_config_id is None) == (self.connector_id is None):
            raise ValueError("give exactly one of auth_config_id or connector_id")
        return self


class PodConnectorUpdateRequest(StrictPatch):
    """Fields left out are unchanged; `shared_pod_account_id: null` clears it.
    Its slug and installation are fixed: a different one is a different
    connector, which apps, agents and bundles already address."""

    fixed_fields = frozenset({"slug", "auth_config_id", "connector_id"})
    fixed_remedy = "remove the connector and add it again"

    mode: PodConnectorMode | None = None
    shared_pod_account_id: UUID | None = None
    required: bool | None = None
    purpose: str | None = Field(default=None, max_length=500)
    visibility: PodConnectorVisibility | None = None


class SharedAccountSummary(BaseModel):
    pod_account_id: UUID
    owner_user_id: UUID
    display_name: str | None = None
    email: str | None = None


class MemberConnectStanding(StrEnum):
    #: Has an account on the installation that a call can run as.
    CONNECTED = "CONNECTED"
    #: Has none.
    MISSING = "MISSING"
    #: Has one, and it needs its owner to sign in again.
    REAUTH_REQUIRED = "REAUTH_REQUIRED"


class MemberAwaited(BaseModel):
    """A member a required per-member connector is still waiting on.

    Who, and why -- never which account: nobody but its owner is shown that."""

    user_id: UUID
    pod_member_id: UUID
    name: str | None = None
    email: str | None = None
    standing: MemberConnectStanding


class PodConnectorCoverage(BaseModel):
    """How many of the members who may use a connector can run it as their own."""

    eligible: int = Field(description="Members of the pod who may use this connector.")
    connected: int = Field(description="Of those, how many have a working account.")
    waiting_on: list[MemberAwaited] = Field(
        default_factory=list,
        description="The rest: missing an account, or theirs needs reconnecting.",
    )
    truncated: bool = Field(
        default=False,
        description="The pod has more members than were counted.",
    )


class PodConnectorResponse(BaseModel):
    id: UUID
    pod_id: UUID
    slug: str
    auth_config_id: UUID
    installation_name: str | None = None
    connector_id: str | None = None
    mode: PodConnectorMode
    shared_pod_account_id: UUID | None = None
    shared_account: SharedAccountSummary | None = None
    required: bool
    purpose: str | None = None
    visibility: PodConnectorVisibility
    #: How it was declared: AUTO on a person's first use of their own account.
    source: PodConnectorSource = PodConnectorSource.MANUAL
    status: PodConnectorStatus
    created_by_user_id: UUID | None = None
    created_at: datetime
    updated_at: datetime
    can_use: bool = Field(
        default=False,
        description=(
            "Whether the caller may run operations through it: what decides if "
            "connecting their own account would let them use it. False for a "
            "viewer, and for anyone a RESTRICTED connector leaves out."
        ),
    )
    member_coverage: PodConnectorCoverage | None = Field(
        default=None,
        description=(
            "Not filled in on listings, which stay cheap: read it from "
            "`GET /pods/{pod_id}/connectors/{slug}/coverage`. Kept so a "
            "client reading it gets null rather than a missing key."
        ),
    )


class PodConnectorListResponse(BaseModel):
    items: list[PodConnectorResponse]


class GranteeRef(BaseModel):
    grantee_type: Literal["ROLE", "POD_MEMBER"]
    grantee_id: UUID


class PodAccountCreateRequest(BaseModel):
    account_id: UUID = Field(
        description="One of your own accounts in this organization."
    )
    visibility: PodAccountVisibility = PodAccountVisibility.PERSONAL
    share_with: list[GranteeRef] = Field(
        default_factory=list,
        max_length=100,
        description="RESTRICTED only: the members or roles who may use it.",
    )


class PodAccountUpdateRequest(StrictPatch):
    fixed_fields = frozenset({"account_id", "auth_config_id", "owner_user_id"})
    fixed_remedy = "take the account out of the pod and add another"

    visibility: PodAccountVisibility | None = Field(
        default=None,
        description=(
            "Owner only. PERSONAL withdraws it: SHARED connectors using it need "
            "another account at once."
        ),
    )
    share_with: list[GranteeRef] | None = Field(
        default=None,
        max_length=100,
        description="RESTRICTED only: replaces who may use it.",
    )
    is_member_default: bool | None = Field(
        default=None,
        description="Owner only: make this the account your own calls use here.",
    )
    allow_sandbox_use: bool | None = Field(
        default=None,
        description=(
            "Owner only: allow this account's credential inside sandboxes, so "
            "a SHARED connector on it (the pod's `github`) can give other "
            "members' workspaces its raw token for git and gh, which they can "
            "copy. Off by default; withdrawing the account turns it off."
        ),
    )


class PodAccountResponse(BaseModel):
    id: UUID
    pod_id: UUID
    account_id: UUID
    auth_config_id: UUID
    connector_id: str | None = None
    owner_user_id: UUID
    visibility: PodAccountVisibility
    is_member_default: bool
    source: PodAccountSource
    last_used_at: datetime | None = None
    allow_sandbox_use: bool = False
    display_name: str | None = None
    email: str | None = None
    used_by: list[str] = Field(
        default_factory=list,
        description="Slugs of the SHARED pod connectors that run as this account.",
    )
    created_at: datetime
    notice: str | None = Field(
        default=None,
        description=(
            "Something the change did not do at once, for the owner to read: "
            "after turning `allow_sandbox_use` off, that sandboxes already "
            "given the token keep a copy until it is swept."
        ),
    )


class PodAccountShareAsRequest(BaseModel):
    """Share one of your pod accounts with the pod as a SHARED connector."""

    slug: str = Field(
        description=(
            "The SHARED connector to run as it: an existing one on the same "
            "installation is pointed at it, otherwise one is declared."
        )
    )
    purpose: str | None = Field(default=None, max_length=500)

    @field_validator("slug")
    @classmethod
    def _slug(cls, value: str) -> str:
        if not SLUG_PATTERN.match(value):
            raise ValueError(
                "slug must be lowercase letters, digits, '-' or '_', starting "
                "with a letter or digit, at most 63 characters"
            )
        return value


class PodAccountListResponse(BaseModel):
    items: list[PodAccountResponse]


class AccountPodUsage(BaseModel):
    pod_id: UUID
    pod_account_id: UUID
    visibility: PodAccountVisibility
    source: PodAccountSource
    is_member_default: bool
    last_used_at: datetime | None = None
    used_by: list[str] = Field(default_factory=list)


class AccountPodUsageResponse(BaseModel):
    account_id: UUID
    items: list[AccountPodUsage]


class ImpactedPodConnector(BaseModel):
    pod_id: UUID
    slug: str
    mode: PodConnectorMode


class ImpactedPod(BaseModel):
    """One pod the caller can open, and what in it stops."""

    pod_id: UUID
    name: str
    pod_connectors: list[str] = Field(
        default_factory=list, description="Slugs of its connectors on it."
    )
    surface_count: int = 0
    schedule_count: int = 0
    event_subscription_count: int = 0


class InstallationImpactResponse(BaseModel):
    """What deleting or disabling an installation stops, before it is done.

    The counts are the whole organization's; `pods` and `pod_connectors` name
    only the pods the caller can open, and the rest are counted in
    `unnamed_pod_count`."""

    auth_config_id: UUID
    account_count: int
    pod_account_count: int
    pod_connectors: list[ImpactedPodConnector] = Field(
        description="Its pod connectors in the pods the caller can open."
    )
    unnamed_pod_count: int = Field(
        default=0,
        description=(
            "Pods that lose a connector and are not named here: the caller "
            "cannot open them, or (with `complete: false`) whether they can "
            "could not be checked."
        ),
    )
    unnamed_pod_connector_count: int = Field(
        default=0,
        description="The pod connectors those unnamed pods lose.",
    )
    surface_count: int = Field(
        default=0,
        description="Surfaces that answer through one of its pod connectors.",
    )
    schedule_count: int = Field(
        default=0,
        description="Schedules that listen through one of its pod connectors.",
    )
    event_subscription_count: int = Field(
        default=0,
        description="MCP event subscriptions held on its accounts.",
    )
    pods: list[ImpactedPod] = Field(
        default_factory=list,
        description="Per pod the caller can open: what stops there.",
    )
    complete: bool = Field(
        default=True,
        description=(
            "False when the surfaces, schedules and subscriptions could not be "
            "counted just now: the counts above them are zero for that reason, "
            "not because nothing stops. Removing is not blocked by it."
        ),
    )


class AskMembersRequest(BaseModel):
    user_ids: list[UUID] | None = Field(
        default=None,
        max_length=500,
        description=(
            "Only these members, of those the connector is waiting on. Left out: "
            "everyone it is waiting on."
        ),
    )


class AskMembersResponse(BaseModel):
    """Who was asked to connect, and who was asked too recently to ask again."""

    asked: list[MemberAwaited]
    asked_recently: list[MemberAwaited] = Field(
        default_factory=list,
        description=(
            "Already asked about this connector in the last day; not asked again."
        ),
    )
    not_asked: list[MemberAwaited] = Field(
        default_factory=list,
        description=(
            "Not asked, because whether they were asked today could not be "
            "checked, or the line to them could not be posted, just now. Try "
            "again shortly."
        ),
    )
    connect_url: str
