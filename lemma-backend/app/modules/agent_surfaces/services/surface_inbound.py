"""Turning a received event into a message the agent can be run on.

Preparation, not dispatch: unpack the webhook, build the context the run needs,
persist the inbound message, and fold in anything that arrived alongside it --
a voice note to transcribe, recent channel history for a group mention.
"""

from __future__ import annotations

from uuid import UUID


from app.core.authorization.delegation import agent_display_name
from app.core.infrastructure.db.session_uow import commit_now
from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork

from app.modules.agent_surfaces.services.inbound_enrichment import enrich_or_drop
from app.modules.agent_surfaces.domain.channel_names import configured_channel_name
from app.modules.agent_surfaces.domain.entities import (
    platform_value_for_source,
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
    ResolvedSurfaceUser,
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
from app.modules.agent_surfaces.domain.adapter_port import (
    SurfacePlatformAdapterPort,
)
from app.modules.agent_surfaces.domain.ports import (
    SurfaceEventDedupStorePort,
)
from app.modules.agent_surfaces.services.credential_resolver import (
    native_credentials,
)
from app.modules.agent_surfaces.services.fallback_reply_service import (
    identity_confirmation_context,
    nonmember_context,
    prepare_unrouted_context,
    surface_setup_context,
    unresolved_sender_context,
)
from app.modules.agent_surfaces.services.agent_naming import agent_name_for_surface
from app.core.log.log import get_logger
from app.modules.agent_surfaces.services.conversation_binder import ConversationBinder
from app.modules.agent_surfaces.services.surface_router import SurfaceRouter


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


def _has_shared_system_bot(platform: str) -> bool:
    """Whether a platform-wide webhook for this platform arrives on shared credentials.

    Only where that is true may a shared webhook be narrowed to system-credential
    surfaces. Custom or bound bots on those platforms have to come with
    `receiver_surface_ids` (a native receiver) or over a direct surface webhook;
    without the narrowing, continuity for the same external user or thread can
    pull a system-bot message into a custom-bot conversation.

    Applying it to every platform instead would delete the Slack own-app path,
    where an org signs with its own secret and its surface is legitimately not on
    system credentials. The list is the whole rule, which is why it stays here
    rather than moving into the statement that consumes it.
    """
    return platform in {
        SurfacePlatform.TELEGRAM.value,
        SurfacePlatform.WHATSAPP.value,
    }


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


class SurfaceInboundMixin:
    #: Supplied by `AgentSurfaceIngressService`, which composes these mixins.
    #: Not optional any more: the worker's factory mode went to
    #: `SurfaceTurnStarter`, so there is one kind of ingress service and it
    #: always has a session.
    uow: SqlAlchemyUnitOfWork
    #: The two objects this used to reach *into*, as nine cross-mixin calls on a
    #: flattened namespace. Declared, so the type checker can follow them --
    #: `self._resolve_sender_identity` resolved to nothing, which is why its
    #: non-optional return type was invisible here and every use of the result
    #: read as possibly-None. Most of this file's baselined errors are that.
    router: SurfaceRouter
    binder: ConversationBinder

    async def _prepare_platform_webhook_ingress(
        self, request: SurfacePlatformWebhookIngress
    ) -> AgentSurfaceContext | None:
        platform = platform_value_for_source(request.source)
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

        # Two narrowings, and which one applies is decided by whether a native
        # receiver named the surfaces it serves. Both were applied in Python to
        # a list of every surface of this platform in the deployment -- read and
        # hydrated to throw most of it away, on the path every inbound message
        # takes. They are the same two predicates, asked of the database.
        #
        # `receiver_surface_ids` scopes to the bot that actually delivered this
        # event (Telegram polling / Slack socket); without it a custom bot's
        # update can be attributed to a different bot's surface. Absent, this is
        # a shared system-bot webhook: platform-wide fan-in, disambiguated
        # per-sender below, and narrowed to shared credentials where the
        # platform has a shared bot at all.
        receiver_surface_ids = request.receiver_surface_ids
        fan_in = receiver_surface_ids is None and _has_shared_system_bot(platform)
        surfaces: list[AgentSurfaceEntity] = []
        resolved_user: ResolvedSurfaceUser | None = None
        user_pod_ids: set[UUID] | None = None
        if fan_in and parsed.is_dm:
            surfaces, resolved_user, user_pod_ids = await self._fan_in_candidates(
                platform=platform, parsed=parsed, adapter=adapter
            )
        if not surfaces:
            surfaces = await self.surface_repository.list_active_for_routing(
                platform,
                surface_ids=receiver_surface_ids,
                system_credentials_only=fan_in,
            )
        if receiver_surface_ids is not None and not surfaces:
            return None

        if _needs_mention_verification(platform, parsed, surfaces):
            async with connection_released(self.uow.session):  # Telegram API
                parsed = await self.router.enrich_telegram_mention(parsed, surfaces[0])

        candidates = [
            surface for surface in surfaces if surface.allows_inbound_event(parsed)
        ]
        if not candidates:
            return await self._prepare_unrouted_platform_context(
                platform=platform,
                surface=self.router.scoped_fallback_surface(request, surfaces),
                parsed=parsed,
                adapter=adapter,
            )
        return await self._route_to_surface(
            platform=platform,
            adapter=adapter,
            parsed=parsed,
            candidates=candidates,
            resolved_user=resolved_user,
            user_pod_ids=user_pod_ids,
        )

    async def _fan_in_candidates(
        self,
        *,
        platform: str,
        parsed: ParsedInboundSurfaceEvent,
        adapter: SurfacePlatformAdapterPort,
    ) -> tuple[list[AgentSurfaceEntity], ResolvedSurfaceUser, set[UUID] | None]:
        """The shared bot's fan-in, narrowed to the pods the sender is in.

        The fan-in is every system-credential surface of the platform in the
        deployment -- one per provisioned person -- read and hydrated on the way
        to picking the handful this sender can use. Narrowing it looks circular,
        because selection needs the sender and the sender was resolved from
        `candidates[0]`'s credentials. It is not. Every candidate here is by
        definition a system-credential surface, and `for_surface` answers those
        with `native_credentials`, which comes from settings: the same values
        whichever row asks, and no database round trip to get them. The
        installation id is not needed either -- `resolve` consults it only for
        Slack and Teams, and neither has a shared bot.

        An unknown sender, or one who belongs to none of these pods, gets no
        candidates from here and the caller reads the fan-in unnarrowed. That is
        what keeps the answer identical rather than merely cheaper: selection
        falls back to the thread's existing surface for a non-member, and that
        surface is how ordinary ingestion tells them they have no access to the
        pod their conversation is in.
        """
        resolved_user = await self.router.resolve_sender(
            adapter=adapter,
            parsed=parsed,
            credentials=native_credentials(platform),
            installation_id=None,
        )
        if resolved_user.internal_user_id is None:
            return [], resolved_user, None
        # Through the router. Membership is the router's question -- it is what
        # `select_surface` and `matches_user` ask -- and holding a second copy
        # here meant the same question could be answered two ways in one
        # message, which is the class of bug this whole pass is removing.
        pod_ids = set(
            await self.router.pod_membership_port.get_user_pod_ids(
                resolved_user.internal_user_id
            )
        )
        if not pod_ids:
            return [], resolved_user, None
        return (
            await self.surface_repository.list_active_for_routing(
                platform, pod_ids=pod_ids, system_credentials_only=True
            ),
            resolved_user,
            pod_ids,
        )

    async def _route_to_surface(
        self,
        *,
        platform: str,
        adapter: SurfacePlatformAdapterPort,
        parsed: ParsedInboundSurfaceEvent,
        candidates: list[AgentSurfaceEntity],
        resolved_user: ResolvedSurfaceUser | None = None,
        user_pod_ids: set[UUID] | None = None,
    ) -> AgentSurfaceContext | None:
        """Pick the surface this event belongs to, and build its context.

        The sender is resolved once, on the first candidate's credentials, and
        then continuity -> pod membership -> user default -> deterministic
        tiebreak picks the surface. An unknown sender only proceeds when the
        target is unambiguous, which is what gets it the signup/link flow.

        `resolved_user` arrives already answered when the shared bot's fan-in
        was narrowed by it -- resolving a second time would repeat a profile
        fetch and an upsert to reach the same answer.
        """
        identity_surface = candidates[0]
        sender = (
            resolved_user
            if resolved_user is not None
            else await self.router.resolve_sender(
                adapter=adapter,
                parsed=parsed,
                credentials=await self.credential_resolver.for_surface(
                    identity_surface
                ),
                installation_id=identity_surface.account_id or identity_surface.id,
            )
        )
        matched_surface = await self.router.select_surface(
            candidates=candidates,
            resolved_user=sender,
            parsed=parsed,
            platform=platform,
            user_pod_ids=user_pod_ids,
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
                resolved_user=sender,
            )

        return await self._prepare_surface_context(
            surface=matched_surface,
            parsed=parsed,
            adapter=adapter,
            resolved_user=sender,
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
            if self.router.is_self_addressed(surface=surface, parsed=parsed):
                return None
            if surface.should_ignore_sender(parsed.sender_external_user_id):
                return None
        if resolved_user is None:
            credentials = (
                await self.credential_resolver.for_surface(surface)
                if surface is not None
                # No surface on this path by definition: the event matched none.
                else await self.credential_resolver.for_platform(
                    platform, None, surface=None
                )
            )
            resolved_user = await self.router.resolve_sender(
                adapter=adapter,
                parsed=parsed,
                credentials=credentials,
                installation_id=(surface.account_id or surface.id) if surface else None,
            )
        display_name = agent_display_name(
            (await agent_name_for_surface(self.uow, surface)) if surface else None
        )
        # `prepare_unrouted_context` opens with a Redis dedup claim, and
        # `resolve_sender` above has flushed an external-user upsert --
        # so `connection_released` would decline and hand nothing back. Commit
        # instead: what has been written by here is a durable fact about the
        # sender, not something the decision to reply should be able to undo,
        # and a batched delivery would otherwise carry the first part's writes
        # through every later part's Redis round trip.
        await commit_now(self.uow)
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
        claim_delivery: bool = True,
    ) -> AgentSurfaceContext | None:
        if self.router.is_self_addressed(surface=surface, parsed=parsed):
            return None

        if surface.should_ignore_sender(parsed.sender_external_user_id):
            return None

        credentials = await self.credential_resolver.for_surface(surface)
        fallback_agent_name = await agent_name_for_surface(self.uow, surface)
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
        if self.router.is_self_addressed(surface=surface, parsed=parsed):
            return None

        # Claimed only with the message in hand: claiming earlier burns it on an
        # attempt that had no body, so the retry is discarded as a duplicate.
        # Enrichment also changes the ids this keys on.
        # Replay re-runs a message the claim already burned, so it asks for the
        # claim to be skipped; every live delivery still takes it.
        if claim_delivery:
            # The connection goes back for the claim itself: it is a Redis round
            # trip, and only reads have happened by here -- the identity upsert
            # and the conversation link are below, so this release is real
            # rather than a `safe_to_release` no-op.
            async with connection_released(self.uow.session):
                claimed = await self.event_dedup_store.claim_message(
                    surface_installation_id=surface.id,
                    platform=surface.surface_type,
                    external_channel_id=parsed.external_channel_id,
                    external_thread_id=parsed.external_thread_id,
                    external_message_id=parsed.external_message_id,
                )
        else:
            claimed = True
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
            resolved_user = await self.router.resolve_sender(
                adapter=adapter,
                parsed=parsed,
                credentials=credentials,
                installation_id=surface.account_id or surface.id,
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
            await self.router.matches_user(
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

        route = await self.router.resolve_route(surface=surface, parsed=parsed)
        if route is None:
            return surface_setup_context(
                surface=surface,
                parsed=parsed,
                agent_display_name=fallback_agent_display_name,
            )

        link, created_conversation_title = await self.binder.bind_conversation(
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
                conversation_kind=route.conversation_kind,
                external_channel_id=parsed.external_channel_id,
                channel_name=configured_channel_name(surface, parsed),
                event_metadata=parsed.metadata,
            ),
            message_user_id=resolved_user.internal_user_id,
            message_external_user_id=resolved_user.external_user_id,
            message_external_message_id=parsed.external_message_id,
            event=parsed,
        )
