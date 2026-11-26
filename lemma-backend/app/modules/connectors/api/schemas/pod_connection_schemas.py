"""The connections read: one row per app a pod uses, judged for whoever asks."""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.connectors.domain.pod_connector import (
    PodConnectorMode,
    PodConnectorStatus,
)


class ConnectionStanding(StrEnum):
    """What the caller can do about a connector right now.

    By mode: PER_MEMBER is ``ready``, ``connect`` (no account of theirs on the
    installation anywhere), ``use_existing`` (one in the organization, not in
    this pod yet) or ``reconnect``; SHARED and APP are ``ready`` or ``waiting``
    (no account shared; the App not installed); any mode is ``waiting`` when
    its installation is disabled, and ``not_allowed`` without ``connector.use``.
    """

    READY = "ready"
    CONNECT = "connect"
    RECONNECT = "reconnect"
    USE_EXISTING = "use_existing"
    WAITING = "waiting"
    NOT_ALLOWED = "not_allowed"


class RunsAsKind(StrEnum):
    #: The caller's own account (PER_MEMBER).
    ME = "me"
    #: Somebody's shared account (SHARED).
    PERSON = "person"
    #: The organization's GitHub App (APP).
    APP = "app"
    #: Nothing right now: a SHARED connector with no account shared.
    NOBODY = "nobody"


class ConnectionRunsAs(BaseModel):
    """Who a call by the caller runs as, in words the page can show."""

    kind: RunsAsKind
    label: str | None = Field(
        default=None,
        description=(
            "The caller's account label (me), the shared account's (person), "
            "or the App's GitHub organization login(s), comma-separated (app)."
        ),
    )
    owner_user_id: UUID | None = Field(
        default=None, description="SHARED: who shared the account."
    )
    owner_name: str | None = None


class ConnectionAccount(BaseModel):
    """The caller's own account behind the connector here."""

    id: UUID = Field(description="The organization account's id.")
    label: str | None = None
    pod_account_id: UUID | None = None


class ExistingAccount(BaseModel):
    """The caller's organization account on the installation, not in the pod."""

    account_id: UUID
    label: str | None = None


class ConnectionMe(BaseModel):
    standing: ConnectionStanding
    account: ConnectionAccount | None = None
    existing: ExistingAccount | None = Field(
        default=None, description="Set with `use_existing`: the account to add."
    )


class ConnectionCoverage(BaseModel):
    """PER_MEMBER, for someone who may manage connectors: who has connected."""

    connected: int
    total: int


class PodConnectionItem(BaseModel):
    slug: str
    auth_config_id: UUID = Field(
        description="The organization installation; what a connect request names."
    )
    installation_name: str | None = None
    connector_id: str | None = None
    title: str | None = None
    icon: str | None = None
    mode: PodConnectorMode
    purpose: str | None = None
    required: bool = False
    status: PodConnectorStatus
    runs_as: ConnectionRunsAs
    me: ConnectionMe
    coverage: ConnectionCoverage | None = None


class GithubBotInstallation(BaseModel):
    login: str | None = None
    repository_selection: str | None = None


class GithubBot(BaseModel):
    """The organization's GitHub App installations an APP connector can use."""

    installations: list[GithubBotInstallation]


class PodConnectionsResponse(BaseModel):
    may_manage: bool = Field(description="The caller holds pod.connector.manage.")
    github_bot: GithubBot | None = Field(
        default=None,
        description=(
            "Null when the organization has no GitHub App installation an APP "
            "connector can use: proven, and on a GitHub organization."
        ),
    )
    items: list[PodConnectionItem]


__all__ = [
    "ConnectionAccount",
    "ConnectionCoverage",
    "ConnectionMe",
    "ConnectionRunsAs",
    "ConnectionStanding",
    "ExistingAccount",
    "GithubBot",
    "GithubBotInstallation",
    "PodConnectionItem",
    "PodConnectionsResponse",
    "RunsAsKind",
]
