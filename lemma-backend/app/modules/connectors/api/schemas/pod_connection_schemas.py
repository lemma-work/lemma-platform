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
    GitHub's PER_MEMBER and SHARED are ``install`` while the account in use
    reaches no GitHub App installation this organization holds; ``github``
    on ``me`` says why and what unblocks it.
    """

    READY = "ready"
    INSTALL = "install"
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


class GithubWait(StrEnum):
    """Why a GitHub account here reaches no installation, by who can fix it."""

    #: The App is installed on nothing the account can see: install it.
    INSTALL = "install"
    #: Several installations, none picked: pick one.
    CHOOSE = "choose"
    #: An admin of the GitHub account it is installed on has to connect it here.
    GITHUB_ADMIN = "github_admin"
    #: An owner of that GitHub organization has to accept the App's Members:
    #: read permission on GitHub first; until then nobody can prove control.
    GITHUB_PERMISSION = "github_permission"
    #: Someone who may install apps for this Lemma organization has to.
    LEMMA_ADMIN = "lemma_admin"
    #: GitHub did not answer in time for this page. Not ready as far as
    #: anyone knows; checking again asks it again.
    CHECK = "check"


class GithubInstallationOffer(BaseModel):
    installation_id: str
    login: str | None = None
    manage_url: str | None = Field(
        default=None,
        description="The installation's settings page on GitHub: where its "
        "repositories, and a pending permission, are changed.",
    )


class ConnectionGithub(BaseModel):
    """With ``install``: why GitHub is not usable here yet, and the way on."""

    wait: GithubWait
    login: str | None = Field(
        default=None,
        description="The GitHub account the installation in question is on.",
    )
    manage_url: str | None = None
    install_url: str | None = Field(
        default=None, description="With `install`: where the App is installed."
    )
    choices: list[GithubInstallationOffer] = Field(
        default_factory=list,
        description="With `choose`: the installations the account can see; "
        "otherwise the one in question.",
    )


class ConnectionMe(BaseModel):
    standing: ConnectionStanding
    account: ConnectionAccount | None = None
    existing: ExistingAccount | None = Field(
        default=None, description="Set with `use_existing`: the account to add."
    )
    github: ConnectionGithub | None = Field(
        default=None, description="Set with `install`."
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
    "ConnectionGithub",
    "ConnectionMe",
    "ConnectionRunsAs",
    "ConnectionStanding",
    "ExistingAccount",
    "GithubBot",
    "GithubBotInstallation",
    "GithubInstallationOffer",
    "GithubWait",
    "PodConnectionItem",
    "PodConnectionsResponse",
    "RunsAsKind",
]
