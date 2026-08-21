"""Turning a received event into a message the agent can be run on.

Preparation, not dispatch: unpack the webhook, build the context the run needs,
persist the inbound message, and fold in anything that arrived alongside it --
a voice note to transcribe, recent channel history for a group mention.
"""

from __future__ import annotations


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
    SurfaceScheduleIngress,
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

        # Scope to the bot that actually delivered this event when a native
        # receiver told us which surfaces it serves (Telegram polling / Slack
        # socket). This prevents a custom bot's update from being attributed to a
        # different bot's surface. A shared system-bot platform webhook leaves
        # this unset → platform-wide fan-in (disambiguated per-sender below).
        if request.receiver_surface_ids is not None:
            allowed_ids = set(request.receiver_surface_ids)
            surfaces = [surface for surface in surfaces if surface.id in allowed_ids]
            if not surfaces:
                return None
        elif platform in {
            SurfacePlatform.TELEGRAM.value,
            SurfacePlatform.WHATSAPP.value,
        }:
            # Platform-wide webhooks are shared system credentials. Custom/bound
            # bots must arrive with receiver_surface_ids (native receiver) or via
            # a direct surface webhook; otherwise continuity for the same external
            # user/thread can accidentally pull a system-bot message into a
            # custom-bot conversation.
            surfaces = [
                surface
                for surface in surfaces
                if surface.account_id is None
                and surface.credential_mode is SurfaceCredentialMode.SYSTEM
            ]

        # Mention verification for Telegram groups: the parser records any
        # @username / text_mention entities but does NOT set mentioned_agent for
        # generic mentions (a `mention` entity is just a plain @username and
        # doesn't indicate *which* user was mentioned). Here we verify whether
        # the mention actually targets this bot by resolving the bot's
        # @username / user id via getMe and comparing. Must run before
        # allows_inbound_event so the event isn't filtered out before we get a
        # chance to check.
        if (
            platform == SurfacePlatform.TELEGRAM.value
            and not parsed.is_dm
            and not parsed.mentioned_agent
            and surfaces
            and (
                (parsed.metadata or {}).get("mentioned_usernames")
                or (parsed.metadata or {}).get("text_mention_user_ids")
                or "@" in (parsed.message_text or "")
            )
        ):
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

        # Resolve the sender once (using the first candidate's credentials) and
        # pick the surface this event belongs to (continuity → pod membership →
        # user default → deterministic tiebreak). An unknown sender only proceeds
        # when the target surface is unambiguous — it gets the signup/link flow.
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

    async def _prepare_schedule_ingress(
        self,
        request: SurfaceScheduleIngress,
    ) -> AgentSurfaceContext | None:
        surface = await self.surface_repository.get_by_email_schedule_id(
            request.schedule_id
        )
        if surface is None:
            return None
        if not surface.is_active or not surface.status.accepts_inbound_events():
            return None

        adapter = self.adapter_registry.get(surface.surface_type)
        if adapter is None:
            return None

        async with connection_released(self.uow.session):
            parsed = await adapter.parse_inbound_event(request.payload, {})
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
        agent_display_name = (
            (await self.agent_name_for_surface(surface)) if surface else None
        ) or "Lemma"
        return await prepare_unrouted_context(
            platform=platform,
            surface=surface,
            parsed=parsed,
            adapter=adapter,
            resolved_user=resolved_user,
            agent_display_name=agent_display_name,
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
        fallback_agent_display_name = fallback_agent_name or "Lemma"

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
