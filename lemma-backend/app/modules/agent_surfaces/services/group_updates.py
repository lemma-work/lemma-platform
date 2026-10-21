"""Recording what a platform says happened to a group itself.

A creation the bot asked WhatsApp for was recorded as pending when it was asked
for (``services/whatsapp_groups``); its confirmation gives the row its chat id
and invite link, a refusal removes it, and a deletion removes the group and its
log -- nobody can speak there again.

A mirrored place's people are the ones the platform shows are there, so the
platform saying somebody left (Slack, Teams, Telegram) takes them out of its
channel -- unless somebody in Lemma put them there. The bot being taken out
archives the channel; being put back brings it back. And a Slack channel turned
private makes its mirror private (one way only; see ``domain/place_privacy``).
Each is asked only of the places the request could be for: the receiving
surface, else the platform's places on that chat in the workspace or tenant
the event names.

Applied before the message paths on every webhook, and never instead of them: a
body carrying a confirmation carries no message, and parsing it as one is
already a cheap None.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import (
    SurfacePlatform,
    platform_value_for_source,
)
from app.modules.agent_surfaces.domain.groups import (
    GroupUpdateKind,
    ParsedGroupUpdate,
    SurfaceGroup,
)
from app.modules.agent_surfaces.domain.ingress_request import SurfaceIngressRequest
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.infrastructure.repositories.external_user_repository import (  # noqa: E501
    ExternalSurfaceUserRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.group_repository import (
    SurfaceGroupRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.agent_surfaces.platforms.common import platform_transport_errors
from app.modules.agent_surfaces.services.credential_resolver import (
    SurfaceCredentialResolver,
)
from app.modules.channels.contracts import Principal
from app.modules.channels.contracts.places import places_hearing
from app.modules.contacts.contracts import (
    IdentityKind,
    find_contact,
    find_contact_by_user,
)

logger = get_logger(__name__)

#: What the platform says changed about a place the bot is already in.
_PLACE_CHANGES = frozenset(
    {
        GroupUpdateKind.BECAME_PRIVATE,
        GroupUpdateKind.SHARED,
        GroupUpdateKind.UNSHARED,
        GroupUpdateKind.MEMBER_LEFT,
        GroupUpdateKind.BOT_LEFT,
        GroupUpdateKind.BOT_RETURNED,
    }
)
#: Platforms whose bots are installed per workspace or tenant, and whose
#: events name it.
_PER_WORKSPACE = frozenset({SurfacePlatform.SLACK.value, SurfacePlatform.TEAMS.value})


class CreationNotYetRecorded(LookupError):
    """A creation confirmed before the request for it was recorded. Retryable."""

    def __init__(self, request_id: str) -> None:
        super().__init__(f"No group creation is recorded for request {request_id}")


async def apply_group_updates(
    uow: SqlAlchemyUnitOfWork,
    request: SurfaceIngressRequest,
    *,
    adapters: SurfacePlatformAdapterRegistry,
) -> None:
    """Record every group update this webhook body carries. Most carry none.

    A number's own webhook names its surface rather than its platform. Only
    WhatsApp tells a bot about groups this way, and its bodies say so on their
    face, so that is read off the body -- no lookup, and nothing else matches.
    """
    surface_ids: list[UUID] | None
    if isinstance(request, SurfacePlatformWebhookIngress):
        platform = platform_value_for_source(request.source)
        surface_ids = request.receiver_surface_ids
    elif request.payload.get("object") == "whatsapp_business_account":
        platform = SurfacePlatform.WHATSAPP.value
        surface_ids = [request.surface_id]
    else:
        surface = await SurfaceRepository(uow).get(request.surface_id)
        if surface is None:
            return
        platform = surface.surface_type.value
        surface_ids = [surface.id]
    adapter = adapters.get(platform) if platform else None
    if adapter is None:
        return
    updates = adapter.parse_group_updates(request.payload)
    if updates:
        await GroupUpdates(uow).apply(updates, surface_ids=surface_ids)


class GroupUpdates:
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow
        self.groups = SurfaceGroupRepository(uow.session)

    async def apply(
        self,
        updates: Sequence[ParsedGroupUpdate],
        *,
        surface_ids: Sequence[UUID] | None = None,
    ) -> None:
        """Record every update, then fetch any link Meta left out.

        In that order and committed between, so the only call to Meta is made
        with no transaction open and no connection held. ``surface_ids``: the
        surfaces the request could be for, where it names them.
        """
        linkless: list[tuple[SurfaceGroup, str | None]] = []
        for update in updates:
            if update.kind in _PLACE_CHANGES:
                await self._place_changed(update, surface_ids=surface_ids)
                continue
            group = await self._apply(update)
            if group is not None and group.invite_link is None:
                linkless.append((group, update.phone_number_id))
        await self.uow.commit()
        for group, phone_number_id in linkless:
            await self._fetch_invite_link(group, phone_number_id=phone_number_id)

    async def _apply(self, update: ParsedGroupUpdate) -> SurfaceGroup | None:
        """Record one update; the group it confirmed, if it confirmed one."""
        if update.kind is GroupUpdateKind.CREATED:
            if not update.request_id or not update.external_channel_id:
                return None
            group = await self.groups.confirm_created(
                platform=update.platform,
                request_id=update.request_id,
                external_channel_id=update.external_channel_id,
                title=update.title,
                invite_link=update.invite_link,
            )
            if group is None:
                # The request is recorded only once Meta has answered it, so the
                # confirmation can overtake the row by a moment. Raising hands
                # the webhook back to the inbox, whose retry finds the row; a
                # creation that is genuinely not ours (another app on the same
                # number) runs out of attempts and is dropped there.
                raise CreationNotYetRecorded(update.request_id)
            return group
        if update.kind is GroupUpdateKind.CREATE_FAILED and update.request_id:
            await self.groups.forget_request(
                platform=update.platform, request_id=update.request_id
            )
            logger.warning(
                "agent_surfaces.group_updates.creation_refused.degraded",
                platform=update.platform,
            )
            return None
        if update.kind is GroupUpdateKind.DELETED and update.external_channel_id:
            await self.groups.forget_channel(
                platform=update.platform,
                external_channel_id=update.external_channel_id,
            )
        return None

    async def _place_changed(
        self, update: ParsedGroupUpdate, *, surface_ids: Sequence[UUID] | None
    ) -> None:
        """What the platform says changed about a place, on each of its mirrors."""
        for group in await self._mirrors(update, surface_ids=surface_ids):
            changed = await self._apply_place_change(group, update)
            logger.info(
                "agent_surfaces.group_updates.place_changed.observed",
                platform=update.platform,
                kind=update.kind.value,
                changed=changed,
            )

    async def _mirrors(
        self, update: ParsedGroupUpdate, *, surface_ids: Sequence[UUID] | None
    ) -> list[SurfaceGroup]:
        """The pod places on this chat the update can be about.

        On the receiving surfaces when the request names them. Otherwise the
        platform's places on the chat, kept to the workspace or tenant the
        event names on the platforms whose bots are installed per workspace.
        """
        chat = update.external_channel_id
        if not chat:
            return []
        found = await places_hearing(
            self.uow,
            external_channel_id=chat,
            surface_ids=surface_ids,
            platform=update.platform if surface_ids is None else None,
        )
        surfaces = SurfaceRepository(self.uow)
        mirrors: list[SurfaceGroup] = []
        for source in found:
            if source.surface_id is None:
                continue
            if update.platform in _PER_WORKSPACE:
                surface = await surfaces.get(source.surface_id)
                if surface is None or not surface.matches_tenant(update.tenant_id):
                    continue
            mirrors.append(SurfaceGroup.of(source))
        return mirrors

    async def _apply_place_change(
        self, group: SurfaceGroup, update: ParsedGroupUpdate
    ) -> bool:
        kind = update.kind
        if kind is GroupUpdateKind.BECAME_PRIVATE:
            return await self.groups.became_private(group.id)
        if kind in (GroupUpdateKind.SHARED, GroupUpdateKind.UNSHARED):
            shared = kind is GroupUpdateKind.SHARED
            if group.shared_externally is shared:
                return False
            await self.groups.set_shared_externally(group.id, shared=shared)
            return True
        if kind is GroupUpdateKind.BOT_LEFT:
            return await self.groups.bot_removed(group.id)
        if kind is GroupUpdateKind.BOT_RETURNED:
            return await self.groups.bot_returned(group.id)
        who = await self._who_left(group, update)
        return who is not None and await self.groups.member_left(group.id, who)

    async def _who_left(
        self, group: SurfaceGroup, update: ParsedGroupUpdate
    ) -> Principal | None:
        """The member -- else the contact -- the platform says left."""
        external_id = update.external_user_id
        if not external_id:
            return None
        resolved = await ExternalSurfaceUserRepository(
            self.uow
        ).resolved_users_by_external_ids(
            platform=group.platform,
            tenant_id=update.tenant_id,
            external_user_ids=[external_id],
        )
        user_id = resolved.get(external_id)
        if user_id is not None:
            return Principal.member(user_id)
        contact = None
        if group.platform == SurfacePlatform.TELEGRAM.value:
            contact = await find_contact(
                self.uow,
                pod_id=group.pod_id,
                kind=IdentityKind.TELEGRAM,
                value=external_id,
            )
        elif group.platform == SurfacePlatform.SLACK.value:
            contact = await find_contact_by_user(
                self.uow,
                pod_id=group.pod_id,
                kind=IdentityKind.SLACK,
                user_id=external_id,
            )
        return Principal.contact(contact.id) if contact is not None else None

    async def _fetch_invite_link(
        self, group: SurfaceGroup, *, phone_number_id: str | None
    ) -> None:
        """Ask for the link when the confirmation did not carry it.

        Meta's sample carries it and its spec does not promise it. Best-effort:
        a group without a link is still a group, and asking again later is
        what the create tool does for a link it does not have yet.
        """
        from app.modules.agent_surfaces.platforms.whatsapp.client import (
            WhatsAppApiError,
            WhatsAppClient,
        )

        surface = await SurfaceRepository(self.uow).get(group.surface_id)
        if surface is None or group.external_channel_id is None:
            return
        credentials = await SurfaceCredentialResolver(uow=self.uow).for_surface(
            surface, arrived_on=phone_number_id
        )
        client = WhatsAppClient.from_credentials(credentials)
        try:
            async with connection_released(self.uow.session):
                link = await client.get_invite_link(group.external_channel_id)
        except (WhatsAppApiError, *platform_transport_errors()):
            logger.info(
                "agent_surfaces.group_updates.invite_link_lookup.observed",
                exc_info=True,
            )
            return
        if link:
            await self.groups.set_invite_link(group.id, link)
            await self.uow.commit()
