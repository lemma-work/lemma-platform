"""The people of a Slack workspace the pod's organization holds.

A workspace Lemma's own app manages belongs to one organization
(``slack_manager_installs``). The people in it are that organization's, so a
teammate's bot there answers the ones outside its pod as **contacts** -- in a
private chat, and in a group that welcomes people from outside the pod --
rather than sending them to sign up first. Members still route as themselves:
whether somebody is in the pod is resolution's to say, and everything here is
asked only once it has said "not this pod".

One predicate, *held by this pod's organization*, answers for every place that
asks it: both doors (``ContactDoor``, ``OutsiderDoor``), onboarding stepping
aside, and a new bot's default contact policy. A bot of one organization in a
workspace another organization holds is not answering that organization's
people, and none of the four treats it as if it were.

* **Who counts.** A Slack sender whose own team is the workspace the bot is in
  -- not a Slack Connect visitor, whom ``group_handle`` already knows by their
  own team -- named by ``workspace_handle``.
* **Guests** (Slack's ``is_restricted`` / ``is_ultra_restricted``) were let
  into a channel or a few, not into the organization. In a group that welcomes
  outsiders they are answered as anybody from outside is; in a private chat,
  never. Only a profile in which Slack says they are neither is a full member:
  no profile, no private answer.
"""

from __future__ import annotations

from collections.abc import Collection
from uuid import UUID

from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
    ResolvedSurfaceUser,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.models import SurfaceSenderProfile
from app.modules.agent_surfaces.domain.surface_config import (
    ContactAnswer,
    SurfaceContactPolicy,
)
from app.modules.agent_surfaces.infrastructure.repositories.slack_manager_repository import (  # noqa: E501
    SlackManagerRepository,
)
from app.modules.agent_surfaces.services.sender_handles import (
    SenderHandle,
    workspace_handle,
)
from app.modules.connectors.contracts.pod_connectors import (
    PodConnectorRefusal,
    resolve_standing_pod_connector,
)
from app.modules.pod.contracts.agent_access import pod_organization_ids
from app.modules.pod.contracts.members import pod_member_holds


async def held_for_pods(
    uow: SqlAlchemyUnitOfWork, team_id: str | None, pod_ids: Collection[UUID]
) -> bool:
    """Whether Lemma's own app manages this workspace for the organization
    every one of these pods is in."""
    wanted = set(pod_ids)
    if not team_id or not wanted:
        return False
    install = await SlackManagerRepository(uow.session).install_for_team(team_id)
    if install is None:
        return False
    organizations = await pod_organization_ids(uow, wanted)
    return len(organizations) == len(wanted) and set(organizations.values()) == {
        install.organization_id
    }


def slack_guest(profile: SurfaceSenderProfile | None) -> bool | None:
    """Slack's word on whether this person is a guest; None where it gave none."""
    raw = profile.raw_profile if profile is not None else None
    if not raw:
        return None
    restricted = raw.get("is_restricted")
    ultra = raw.get("is_ultra_restricted")
    if restricted is True or ultra is True:
        return True
    if restricted is False and ultra is False:
        return False
    return None


def sender_team(parsed: ParsedInboundSurfaceEvent) -> str | None:
    """The sender's own Slack team, where it is the workspace the bot is in."""
    team = parsed.metadata.get("sender_team_id")
    if not isinstance(team, str) or not team or team != parsed.tenant_id:
        return None
    return team


class WorkspacePeople:
    """Asks, of a sender already found outside the pod, whether they are one of
    the people of a workspace the pod's organization holds."""

    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow

    async def colleague_handle(
        self,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
        sender: ResolvedSurfaceUser,
        *,
        private: bool,
    ) -> SenderHandle | None:
        """The handle they are answered under as a contact, or None.

        ``private`` for a private chat, where a guest -- or somebody Slack did
        not describe -- is not answered.
        """
        if (
            parsed.platform is not SurfacePlatform.SLACK
            or surface.surface_type is not SurfacePlatform.SLACK
        ):
            return None
        team = sender_team(parsed)
        if team is None or not surface.matches_tenant(team):
            return None
        if private and sender.is_guest is not False:
            return None
        handle = workspace_handle(parsed)
        if handle is None or not await held_for_pods(self.uow, team, [surface.pod_id]):
            return None
        return handle

    async def answer_by_default(
        self,
        entity: AgentSurfaceEntity,
        *,
        chosen: bool | None = None,
        config_given: bool = False,
    ) -> None:
        """A new Slack bot in a workspace its pod's organization holds answers
        the workspace's people, looked after by whoever connected it.

        Only when its creator sent no contact policy (``chosen``): "off" sent
        on purpose has the default's value, so the value cannot say. A
        creator that does not say is taken to have chosen when it brought a
        config of its own (``config_given``). Only a new bot: one made before
        the workspace was held keeps what it had. Whoever looks after contacts
        reads their chats, so the person who connected the bot must be one who
        may (``contact.read``), as turning contacts on by hand requires.
        """
        if (config_given if chosen is None else chosen) or (
            entity.surface_type is not SurfacePlatform.SLACK
            or entity.pod_connector_slug is None
            or entity.config.contacts != SurfaceContactPolicy()
        ):
            return
        if not await held_for_pods(
            self.uow, entity.external_workspace_id, [entity.pod_id]
        ):
            return
        try:
            connected = await resolve_standing_pod_connector(
                self.uow,
                pod_id=entity.pod_id,
                slug=entity.pod_connector_slug,
                record_use=False,
            )
        except PodConnectorRefusal:
            return
        owner = connected.owner_user_id
        if not await pod_member_holds(
            self.uow, entity.pod_id, owner, Permissions.CONTACT_READ
        ):
            return
        entity.config.contacts = entity.config.contacts.model_copy(
            update={"answer": ContactAnswer.ANYONE, "looked_after_by": owner}
        )
