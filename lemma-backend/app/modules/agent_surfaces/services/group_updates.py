"""Recording what a platform says happened to a group itself.

Only WhatsApp says anything, because only there does the bot create groups (see
``platforms/whatsapp/group_updates``). A creation was recorded as pending when
it was asked for (``services/whatsapp_groups``); its confirmation gives the row
its chat id and invite link, a refusal removes it, and a deletion removes the
group and its log -- nobody can speak there again.

Applied before the message paths on every webhook, and never instead of them: a
body carrying a confirmation carries no message, and parsing it as one is
already a cheap None.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import platform_value_for_source
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
from app.modules.agent_surfaces.infrastructure.repositories.group_repository import (
    SurfaceGroupRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.agent_surfaces.platforms.common import PLATFORM_TRANSPORT_ERRORS
from app.modules.agent_surfaces.services.credential_resolver import (
    SurfaceCredentialResolver,
)

logger = get_logger(__name__)


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
    """Record every group update this webhook body carries. Most carry none."""
    if not isinstance(request, SurfacePlatformWebhookIngress):
        return
    platform = platform_value_for_source(request.source)
    adapter = adapters.get(platform) if platform else None
    if adapter is None:
        return
    updates = adapter.parse_group_updates(request.payload)
    if updates:
        await GroupUpdates(uow).apply(updates)


class GroupUpdates:
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow
        self.groups = SurfaceGroupRepository(uow.session)

    async def apply(self, updates: Sequence[ParsedGroupUpdate]) -> None:
        """Record every update, then fetch any link Meta left out.

        In that order and committed between, so the only call to Meta is made
        with no transaction open and no connection held.
        """
        linkless: list[tuple[SurfaceGroup, str | None]] = []
        for update in updates:
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
            await self.groups.forget_request(update.request_id)
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
        except (WhatsAppApiError, *PLATFORM_TRANSPORT_ERRORS):
            logger.info(
                "agent_surfaces.group_updates.invite_link_lookup.observed",
                exc_info=True,
            )
            return
        if link:
            await self.groups.set_invite_link(group.id, link)
            await self.uow.commit()
