"""Events about the app itself rather than about a message.

Being added to a channel, opening the App Home, being installed: none of these
become conversations, and all of them end either in a configuration change or in
an explanation of why one cannot be made.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.authorization.permissions import Permissions
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import (
    ParsedSurfaceLifecycleEvent,
    SurfaceLifecycleKind,
)
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfaceDirectWebhookIngress,
    SurfacePlatformWebhookIngress,
)

logger = get_logger(__name__)


class SurfaceLifecycleMixin:
    """Split out of :class:`SurfaceConfigurationMixin`; see the module docstring."""

    async def try_handle_lifecycle(
        self,
        request: SurfacePlatformWebhookIngress | SurfaceDirectWebhookIngress,
    ) -> bool:
        """Parse + route an event about the app itself rather than a message.

        Returns True when the payload was a lifecycle event, so the caller stops
        — these never become conversations. False means fall through to the
        interaction and message paths.
        """
        surface = None
        if isinstance(request, SurfaceDirectWebhookIngress):
            surface = await self.surface_repository.get(request.surface_id)
            if surface is None:
                return False
            adapter = self.adapter_registry.get(surface.surface_type)
            platform = surface.surface_type
        else:
            platform = self._resolve_platform(request.source)
            adapter = self.adapter_registry.get(platform) if platform else None
        if adapter is None:
            return False

        # Parsing can reach the platform (Slack parses locally, but Teams and
        # Telegram fetch a profile or a token here), so treat it as egress.
        async with connection_released(self.uow.session):
            parsed = await adapter.parse_inbound_lifecycle(
                request.payload, request.headers
            )
        if parsed is None:
            return False

        candidates, user_id, authorized = await self._authorized_configuration_surfaces(
            request,
            tenant_id=parsed.tenant_id,
            platform=platform,
            actor_external_user_id=parsed.actor_external_user_id,
            adapter=adapter,
            action=(
                Permissions.AGENT_UPDATE
                if parsed.kind is SurfaceLifecycleKind.JOINED_CHANNEL
                else Permissions.AGENT_READ
            ),
        )
        selected = await self._pick_configuration_surface(
            authorized,
            explicit_surface_id=str(surface.id) if surface is not None else None,
            user_id=user_id,
            platform=platform,
        )
        if selected is None:
            await self._answer_unroutable_lifecycle(
                adapter=adapter,
                parsed=parsed,
                candidates=candidates,
                authorized=authorized,
            )
            return True

        surface, ctx = selected
        try:
            await self._handle_lifecycle_event(surface=surface, parsed=parsed, ctx=ctx)
        except SQLAlchemyError:
            logger.debug(
                "agent_surfaces.ingress_service.surface_lifecycle_handling.diagnostic",
                surface_id=str(surface.id),
                exc_info=True,
            )
        return True

    async def _answer_unroutable_lifecycle(
        self,
        *,
        adapter: Any,
        parsed: ParsedSurfaceLifecycleEvent,
        candidates: list[Any],
        authorized: list[Any],
    ) -> None:
        """Say something useful when the event cannot be pinned to one surface.

        Two reasons it cannot: the actor may edit nothing here, or they may edit
        several pods and have not said which. Both are answered in the same two
        places -- the App Home, or the channel they just joined -- so the only
        difference is what is said.
        """
        if not candidates or not parsed.actor_external_user_id:
            return
        prompt_surface = authorized[0][0] if authorized else candidates[0]
        credentials = await self._resolve_credentials(prompt_surface)
        choices = await self._surface_choice_labels(authorized) if authorized else None
        await self._prompt_for_configuration(
            adapter=adapter,
            parsed=parsed,
            credentials=credentials,
            surface_choices=choices,
        )

    async def _prompt_for_configuration(
        self,
        *,
        adapter: Any,
        parsed: ParsedSurfaceLifecycleEvent,
        credentials: dict[str, Any],
        surface_choices: list[tuple[str, str]] | None,
    ) -> None:
        """Ask which pod, or explain why nothing can be shown.

        ``surface_choices`` of None means the actor is not authorized anywhere,
        which is the only thing that changes between the two messages.
        """
        no_access = surface_choices is None
        if parsed.kind is SurfaceLifecycleKind.HOME_OPENED:
            async with connection_released(self.uow.session):
                await adapter.publish_home_view(
                    credentials=credentials,
                    user_id=parsed.actor_external_user_id,
                    pod_name=None,
                    channel_routes=[],
                    agents=[],
                    apps=[],
                    surface_choices=surface_choices,
                    access_message=(
                        "You need access to a connected Lemma pod before this app can show agents or settings."
                        if no_access
                        else None
                    ),
                )
            return

        if (
            parsed.kind is SurfaceLifecycleKind.JOINED_CHANNEL
            and parsed.external_channel_id
        ):
            async with connection_released(self.uow.session):
                await adapter.send_channel_setup_prompt(
                    credentials=credentials,
                    channel_id=parsed.external_channel_id,
                    user_id=parsed.actor_external_user_id,
                    surface_choices=surface_choices,
                    configuration_error=(
                        "Only a Lemma pod editor can configure this channel. Ask a pod admin to set it up."
                        if no_access
                        else None
                    ),
                )

    async def _handle_lifecycle_event(
        self,
        *,
        surface,
        parsed: ParsedSurfaceLifecycleEvent,
        ctx,
    ) -> None:
        """React to the app's own situation changing.

        Today the one reaction is offering to configure a freshly joined
        channel. A channel that already has a route needs no prompt — the
        invite was someone re-adding the bot, not setting it up.
        """
        adapter = self.adapter_registry.get(surface.surface_type)
        if adapter is None:
            return
        credentials = await self._resolve_credentials(surface)

        if parsed.kind is SurfaceLifecycleKind.HOME_OPENED:
            # Slack spins forever until a view is published, so this must answer
            # every open — including the very first, before anything is set up.
            if not parsed.actor_external_user_id:
                return
            await self._publish_home(
                surface=surface,
                adapter=adapter,
                credentials=credentials,
                external_user_id=parsed.actor_external_user_id,
                ctx=ctx,
            )
            return

        if parsed.kind is not SurfaceLifecycleKind.JOINED_CHANNEL:
            return
        if not parsed.actor_external_user_id or not parsed.external_channel_id:
            return
        if surface.channel_route_for(
            channel_id=parsed.external_channel_id, channel_name=""
        ):
            return
        async with connection_released(self.uow.session):
            await adapter.send_channel_setup_prompt(
                credentials=credentials,
                channel_id=parsed.external_channel_id,
                user_id=parsed.actor_external_user_id,
                channel_name=await adapter.channel_name(
                    credentials=credentials, channel_id=parsed.external_channel_id
                ),
            )
