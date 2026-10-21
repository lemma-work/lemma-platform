"""What Lemma's own Slack app is told, once read, and how a webhook is known to be its.

Lemma's app manages a workspace's teammates and answers nobody. Everything it
receives is one of a short list (``ManagerInputKind``); anything else is
``OTHER`` and dropped. A ``ManagerInput`` carries only ids -- never the raw
payload, and never what was typed into its "Add to Slack" modal, which may be a
secret and is kept apart (``services/slack_manager_form``) -- so it is what the
``surface.slack_manager`` work item carries.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, SecretStr


#: The connect-request purpose of a teammate's Allow started from Lemma's Home
#: tab in Slack: the grant it ends in becomes the teammate's surface.
SLACK_TEAMMATE_APP_PURPOSE = "slack_teammate_app"
#: The connect-request purpose of "Add to Slack" for Lemma's own app, started
#: from the web (``services/slack_install``): the grant it ends in makes Lemma's
#: app the manager of the workspace it names. An ordinary Slack connect has no
#: purpose and never does.
SLACK_WORKSPACE_INSTALL_PURPOSE = "slack_workspace_install"


class ManagerInputKind(StrEnum):
    HOME_OPENED = "HOME_OPENED"
    JOINED_CHANNEL = "JOINED_CHANNEL"
    #: The manager taken out of a channel, or leaving one itself.
    LEFT_CHANNEL = "LEFT_CHANNEL"
    DIRECT_MESSAGE = "DIRECT_MESSAGE"
    MENTIONED = "MENTIONED"
    ADD_CLICKED = "ADD_CLICKED"
    JOIN_CLICKED = "JOIN_CLICKED"
    ADD_SUBMITTED = "ADD_SUBMITTED"
    UNINSTALLED = "UNINSTALLED"
    OTHER = "OTHER"


class ManagerInput(BaseModel):
    """One thing the manager was told, as ids."""

    model_config = ConfigDict(frozen=True)

    kind: ManagerInputKind
    team_id: str | None = None
    #: The Slack user who did it: the viewer, the inviter, the writer, the tapper.
    user_id: str | None = None
    channel_id: str | None = None
    #: Where a tapped button sat. A join button's own value names a channel
    #: too, and the two must agree.
    container_channel_id: str | None = None
    trigger_id: str | None = None
    response_url: str | None = None
    view_id: str | None = None
    #: The teammate a button or modal is about: a pod and one of its agents.
    pod_id: UUID | None = None
    agent_id: UUID | None = None
    surface_id: UUID | None = None
    #: UNINSTALLED from ``tokens_revoked``: the bot users whose tokens went.
    #: None for ``app_uninstalled``, which takes every one.
    revoked_bots: tuple[str, ...] | None = None
    #: An ADD_SUBMITTED's typed values, stored encrypted apart from the item.
    form_ref: str | None = None


@dataclass(frozen=True, slots=True)
class AddForm:
    """What the "Add to Slack" modal was submitted with. Every field optional,
    and every one a secret but the client id."""

    refresh_token: SecretStr = SecretStr("")
    client_id: str = ""
    client_secret: SecretStr = SecretStr("")
    signing_secret: SecretStr = SecretStr("")

    @property
    def has_own_app(self) -> bool:
        return bool(
            self.client_id
            and self.client_secret.get_secret_value()
            and self.signing_secret.get_secret_value()
        )

    @property
    def has_part_of_own_app(self) -> bool:
        return bool(
            self.client_id
            or self.client_secret.get_secret_value()
            or self.signing_secret.get_secret_value()
        )

    @property
    def has_refresh_token(self) -> bool:
        return bool(self.refresh_token.get_secret_value())

    @property
    def is_empty(self) -> bool:
        return not (self.has_refresh_token or self.has_part_of_own_app)


@dataclass(frozen=True, slots=True)
class ParsedManagerDelivery:
    """A delivery read in the webhook request: the ids, and the typed values
    an ADD_SUBMITTED carries, which never leave the request unencrypted."""

    input: ManagerInput
    form: AddForm | None = None


@dataclass(frozen=True, slots=True)
class InboundVerification:
    """Who a verified shared-endpoint delivery is for.

    ``receivers``: the surfaces the signature selected, or ``None`` for an
    unscoped fan-in -- the inbound pipeline's reading, unchanged. An empty
    list never stands for "the manager". ``manager_team``: set only when
    Lemma's own Slack app signed the delivery, no teammate's surface receives
    it, and Lemma's app manages that workspace. Such a delivery is the
    manager's, and never enters the inbound pipeline.
    """

    receivers: list[UUID] | None
    manager_team: str | None = None

    @property
    def is_managers(self) -> bool:
        return self.manager_team is not None


__all__ = [
    "SLACK_TEAMMATE_APP_PURPOSE",
    "SLACK_WORKSPACE_INSTALL_PURPOSE",
    "AddForm",
    "ManagerInput",
    "ManagerInputKind",
    "ParsedManagerDelivery",
    "InboundVerification",
]
