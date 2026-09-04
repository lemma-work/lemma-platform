"""Turning a received event into a message the agent can be run on.

Preparation, not dispatch: unpack the webhook, build the context the run needs,
persist the inbound message, and fold in anything that arrived alongside it --
a voice note to transcribe, recent channel history for a group mention.
"""

from __future__ import annotations


from app.core.authorization.delegation import agent_display_name
from app.core.infrastructure.db.transaction_locks import connection_released

from app.modules.agent_surfaces.services.inbound_enrichment import enrich_or_drop
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
    ResolvedSurfaceUser,
    SurfaceCredentialMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfaceDirectWebhookIngress,
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.domain.ingress_context import (
    AgentSurfaceContext,
    SurfaceChatContext,
    SurfaceReplyContext,
)
from app.modules.agent_surfaces.domain.models import (
    SurfaceMessageMetadata,
)
from app.modules.agent_surfaces.domain.ports import (
    SurfaceEventDedupStorePort,
    SurfacePlatformAdapterPort,
)
from app.modules.agent_surfaces.services.fallback_reply_service import (
    identity_confirmation_context,
    nonmember_context,
    prepare_unrouted_context,
    surface_setup_context,
    unresolved_sender_context,
)
from app.core.log.log import get_logger

from app.modules.agent_surfaces.services.surface_inbound_message import (
    SurfaceInboundMessageMixin,
)

logger = get_logger(__name__)

# Recent thread/channel messages fetched per run for group-mention continuity.


async def release_ingress_claim(
    context: AgentSurfaceContext,
    *,
    event_dedup_store: SurfaceEventDedupStorePort,
) -> None:
    """Give back the delivery claim ``prepare_ingress`` took for this context.

    The claim is spent inside preparation but the work it guards -- the queued
    run -- is dispatched afterwards, so a caller that fails to dispatch has to
    hand the claim back. Otherwise the inbox's retry re-enters preparation, is
    told the message is already claimed, and drops it: the delivery is gone
    for good.

    Keyed off the context rather than the parsed event the claim was taken
    from, because that is what the dispatcher still holds -- and the two carry
    the same ids by construction (``context.event`` *is* the parsed event, and
    ``surface_id`` is the installation the claim named).
    """
    await event_dedup_store.release_message(
        surface_installation_id=context.surface_id,
        platform=context.platform.value,
        external_channel_id=context.event.external_channel_id,
        external_thread_id=context.event.external_thread_id,
        external_message_id=context.event.external_message_id,
    )


def _system_bot_surfaces(
    surfaces: list[AgentSurfaceEntity], platform: str
) -> list[AgentSurfaceEntity]:
    """Narrow a shared platform webhook to the surfaces it can legitimately be.

    A platform-wide webhook arrives on shared system credentials. Custom or
    bound bots have to come with `receiver_surface_ids` (a native receiver) or
    over a direct surface webhook; without this narrowing, continuity for the
    same external user or thread can pull a system-bot message into a custom-bot
    conversation.
    """
    if platform not in {
        SurfacePlatform.TELEGRAM.value,
        SurfacePlatform.WHATSAPP.value,
    }:
        return surfaces
    return [
        surface
        for surface in surfaces
        if surface.account_id is None
        and surface.credential_mode is SurfaceCredentialMode.SYSTEM
    ]


def _needs_mention_verification(
    platform: str,
    parsed: ParsedInboundSurfaceEvent,
    surfaces: list[AgentSurfaceEntity],
) -> bool:
    """Whether a group message might be an @mention of this bot.

    The parser records any @username / text_mention entities but does not set
    `mentioned_agent` for a generic mention -- a `mention` entity is a plain
    @username and does not say *which* user was meant. Settling that costs a
    getMe call, so it is only worth making when the message could plausibly be
    for us, and it has to happen before `allows_inbound_event` filters the event
    out.
    """
    if platform != SurfacePlatform.TELEGRAM.value:
        return False
    if parsed.is_dm or parsed.mentioned_agent or not surfaces:
        return False
    metadata = parsed.metadata or {}
    return bool(
        metadata.get("mentioned_usernames")
        or metadata.get("text_mention_user_ids")
        or "@" in (parsed.message_text or "")
    )


class SurfaceInboundMixin(SurfaceInboundMessageMixin):
    async def _prepare_platform_webhook_ingress(
        self, request: SurfacePlatformWebhookIngress
    ) -> AgentSurfaceContext | None:
        platform = self._resolve_platform(request.source)
        if not platform:
            return None

        adapter = self.adapter_registry.get(platform)
        if adapter is None:
            return None

        # No connection held for the platform call; see `connection_released`.
        async with connection_released(self.uow.session):
            parsed = await adapter.parse_inbound_event(request.payload, request.headers)
        if parsed is None:
            logger.debug(
                "agent_surfaces.ingress_service.agent_surface_ignored_webhook_because.observed",
                source=request.source,
            )
            return None

        surfaces = await self.surface_repository.list_active_by_type(platform)
        if request.receiver_surface_ids is not None:
            # Scope to the bot that actually delivered this event, when a native
            # receiver told us which surfaces it serves (Telegram polling / Slack
            # socket). Without it a custom bot's update can be attributed to a
            # different bot's surface.
            allowed_ids = set(request.receiver_surface_ids)
            surfaces = [surface for surface in surfaces if surface.id in allowed_ids]
            if not surfaces:
                return None
        else:
            # A shared system-bot platform webhook: platform-wide fan-in,
            # disambiguated per-sender below.
            surfaces = _system_bot_surfaces(surfaces, platform)

        if _needs_mention_verification(platform, parsed, surfaces):
            async with connection_released(self.uow.session):  # Telegram API
                parsed = await self._telegram_text_mention_enrich(parsed, surfaces[0])

        candidates = [
            surface for surface in surfaces if surface.allows_inbound_event(parsed)
        ]
        if not candidates:
            return await self._prepare_unrouted_platform_context(
                platform=platform,
                surface=self._scoped_fallback_surface(request, surfaces),
                parsed=parsed,
                adapter=adapter,
            )
        return await self._route_to_surface(
            platform=platform,
            adapter=adapter,
            parsed=parsed,
            candidates=candidates,
        )

    async def _route_to_surface(
        self,
        *,
        platform: str,
        adapter: SurfacePlatformAdapterPort,
        parsed: ParsedInboundSurfaceEvent,
        candidates: list[AgentSurfaceEntity],
    ) -> AgentSurfaceContext | None:
        """Pick the surface this event belongs to, and build its context.

        The sender is resolved once, on the first candidate's credentials, and
        then continuity -> pod membership -> user default -> deterministic
        tiebreak picks the surface. An unknown sender only proceeds when the
        target is unambiguous, which is what gets it the signup/link flow.
        """
        identity_surface = candidates[0]
        resolved_user = await self._resolve_sender_identity(
            adapter=adapter,
            parsed=parsed,
            credentials=await self._resolve_credentials(identity_surface),
        )
        matched_surface = await self._select_surface(
            candidates=candidates,
            resolved_user=resolved_user,
            parsed=parsed,
            platform=platform,
        )
        if matched_surface is None and len(candidates) == 1 and parsed.is_dm:
            # Single unambiguous DM surface: route to it so the onboarding flow
            # runs — an unknown sender gets the signup link, a signed-up
            # non-member gets the pod-access link (see _prepare_surface_context).
            matched_surface = identity_surface

        if matched_surface is None:
            return await self._prepare_unrouted_platform_context(
                platform=platform,
                surface=identity_surface,
                parsed=parsed,
                adapter=adapter,
                resolved_user=resolved_user,
            )

        return await self._prepare_surface_context(
            surface=matched_surface,
            parsed=parsed,
            adapter=adapter,
            resolved_user=resolved_user,
        )

    async def _prepare_surface_webhook_ingress(
        self,
        request: SurfaceDirectWebhookIngress,
    ) -> AgentSurfaceContext | None:
        surface = await self.surface_repository.get(request.surface_id)
        if surface is None:
            return None

        if not surface.is_active or not surface.status.accepts_inbound_events():
            return None

        adapter = self.adapter_registry.get(surface.surface_type)
        if adapter is None:
            return None

        async with connection_released(self.uow.session):
            parsed = await adapter.parse_inbound_event(request.payload, request.headers)
        if parsed is None:
            return None

        return await self._prepare_surface_context(
            surface=surface,
            parsed=parsed,
            adapter=adapter,
        )

    async def _prepare_unrouted_platform_context(
        self,
        *,
        platform: str,
        surface: AgentSurfaceEntity | None,
        parsed: ParsedInboundSurfaceEvent,
        adapter: SurfacePlatformAdapterPort,
        resolved_user: ResolvedSurfaceUser | None = None,
    ) -> SurfaceReplyContext | None:
        if not parsed.is_dm:
            return None
        if surface is not None:
            if self._is_self_email_event(surface=surface, parsed=parsed):
                return None
            if surface.should_ignore_sender(parsed.sender_external_user_id):
                return None
        if resolved_user is None:
            credentials = (
                await self._resolve_credentials(surface)
                if surface is not None
                # No surface on this path by definition: the event matched none.
                else await self.credential_resolver.for_platform(
                    platform, None, surface=None
                )
            )
            resolved_user = await self._resolve_sender_identity(
                adapter=adapter,
                parsed=parsed,
                credentials=credentials,
            )
        display_name = agent_display_name(
            (await self.agent_name_for_surface(surface)) if surface else None
        )
        return await prepare_unrouted_context(
            platform=platform,
            surface=surface,
            parsed=parsed,
            adapter=adapter,
            resolved_user=resolved_user,
            agent_display_name=display_name,
            event_dedup_store=self.event_dedup_store,
        )

    async def _prepare_surface_context(
        self,
        *,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
        adapter: SurfacePlatformAdapterPort,
        resolved_user: ResolvedSurfaceUser | None = None,
    ) -> AgentSurfaceContext | None:
        if self._is_self_email_event(surface=surface, parsed=parsed):
            return None

        if surface.should_ignore_sender(parsed.sender_external_user_id):
            return None

        credentials = await self._resolve_credentials(surface)
        fallback_agent_name = await self.agent_name_for_surface(surface)
        fallback_agent_display_name = agent_display_name(fallback_agent_name)

        # `enrich_or_drop` is module-level: no session of its own to release.
        async with connection_released(self.uow.session):
            enriched = await enrich_or_drop(
                adapter=adapter, surface=surface, parsed=parsed, credentials=credentials
            )
        if enriched is None:
            return None
        parsed = enriched

        # Re-check after enrichment: email triggers (e.g. Outlook) deliver a
        # minimal payload with no sender, so the pre-enrich self-check above
        # cannot see it. Without this the surface would process its own
        # outgoing replies and loop, re-sending the signup/agent reply forever.
        if self._is_self_email_event(surface=surface, parsed=parsed):
            return None

        # Claimed only with the message in hand: claiming earlier burns it on an
        # attempt that had no body, so the retry is discarded as a duplicate.
        # Enrichment also changes the ids this keys on.
        claimed = await self.event_dedup_store.claim_message(
            surface_installation_id=surface.id,
            platform=surface.surface_type,
            external_channel_id=parsed.external_channel_id,
            external_thread_id=parsed.external_thread_id,
            external_message_id=parsed.external_message_id,
        )
        if not claimed:
            logger.debug(
                "agent_surfaces.ingress_service.agent_surface_ignored_duplicate_external.observed",
                surface_type=surface.surface_type,
                external_channel_id=parsed.external_channel_id,
            )
            return None

        attachment_count = len(parsed.metadata.get("attachments") or [])
        logger.debug(
            "agent_surfaces.ingress_service.agent_surface_prepared_inbound_event.observed",
            surface_type=surface.surface_type,
            attachment_count=attachment_count,
        )

        if resolved_user is None:
            resolved_user = await self._resolve_sender_identity(
                adapter=adapter,
                parsed=parsed,
                credentials=credentials,
            )
        if resolved_user.internal_user_id is None:
            return unresolved_sender_context(
                surface=surface,
                parsed=parsed,
                adapter=adapter,
                agent_display_name=fallback_agent_display_name,
            )
        confirmation = adapter.linked_sender_confirmation(parsed)
        if confirmation is not None:
            return identity_confirmation_context(
                surface=surface,
                parsed=parsed,
                agent_display_name=fallback_agent_display_name,
                confirmation=confirmation,
            )
        if (
            await self._match_surface_for_user(
                surfaces=[surface],
                resolved_user=resolved_user,
            )
            is None
        ):
            logger.debug(
                "agent_surfaces.ingress_service.agent_surface_resolved_user_not.observed",
                surface_type=surface.surface_type,
                internal_user_id=resolved_user.internal_user_id,
                pod_id=surface.pod_id,
            )
            return nonmember_context(
                surface=surface,
                parsed=parsed,
                agent_display_name=fallback_agent_display_name,
            )
        if not surface.config.identity.allows_email(resolved_user.email):
            return surface_setup_context(
                surface=surface,
                parsed=parsed,
                agent_display_name=fallback_agent_display_name,
            )

        route = await self._resolve_route(surface=surface, parsed=parsed)
        if route is None:
            return surface_setup_context(
                surface=surface,
                parsed=parsed,
                agent_display_name=fallback_agent_display_name,
            )

        link, created_conversation_title = await self._get_or_create_conversation_link(
            surface=surface,
            parsed=parsed,
            resolved_user=resolved_user,
            route=route,
        )

        return SurfaceChatContext(
            created_conversation_title=created_conversation_title,
            platform=surface.surface_type,
            pod_id=surface.pod_id,
            agent_name=route.agent_name,
            conversation_id=link.conversation_id,
            user_id=resolved_user.internal_user_id,
            surface_id=surface.id,
            surface_name=surface.name,
            surface_account_id=surface.account_id,
            surface_config=surface.config,
            agent_display_name=route.agent_display_name,
            message_text=parsed.message_text,
            message_metadata=SurfaceMessageMetadata(
                surface_platform=surface.surface_type,
                sender_display_name=resolved_user.display_name,
                sender_email=resolved_user.email,
                sender_phone=resolved_user.phone,
                event_metadata=parsed.metadata,
            ),
            message_user_id=resolved_user.internal_user_id,
            message_external_user_id=resolved_user.external_user_id,
            message_external_message_id=parsed.external_message_id,
            event=parsed,
        )
