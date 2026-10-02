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
from app.modules.agent_surfaces.domain.ports import (
    SurfaceEventDedupStorePort,
    SurfaceInstallationRepositoryPort,
)
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.infrastructure.repositories.conversation_link_repository import (  # noqa: E501
    SurfaceConversationLinkRepository,
)
from app.modules.agent_surfaces.services.surface_candidates import (
    admitted_surfaces,
    fan_in_candidates,
)
from app.modules.agent_surfaces.services.delivery_claims import (
    hand_back_delivery_claim,
    take_delivery_claim,
)
from app.modules.agent_surfaces.services.created_groups import created_group_surfaces
from app.modules.agent_surfaces.services.group_ingress import (
    logged_and_addressed,
    own_bot_group_message,
)
from app.modules.agent_surfaces.services.group_names import spoken_to_by_name
from app.modules.agent_surfaces.services.outsiders import OutsiderDoor
from app.modules.agent_surfaces.services.credential_resolver import (
    SurfaceCredentialResolver,
    arrival_number,
)
from app.modules.agent_surfaces.domain.entities import (
    platform_value_for_source,
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
    ResolvedSurfaceUser,
)
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfaceDirectWebhookIngress,
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.domain.ingress_context import (
    AgentSurfaceContext,
    SurfaceReplyContext,
)
from app.modules.agent_surfaces.domain.adapter_port import (
    SurfacePlatformAdapterPort,
)
from app.modules.agent_surfaces.services.fallback_reply_service import (
    identity_confirmation_context,
    nonmember_context,
    prepare_unrouted_context,
    surface_setup_context,
    unresolved_sender_context,
)
from app.modules.agent_surfaces.services.agent_naming import agent_name_for_surface
from app.modules.agent_surfaces.platforms.platform_capabilities import (
    has_shared_system_bot,
)
from app.core.log.log import get_logger
from app.modules.agent_surfaces.services.conversation_binder import ConversationBinder
from app.modules.agent_surfaces.services.chat_context_builder import build_chat_context
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
    #: The rest of what `AgentSurfaceIngressService.__init__` sets, declared for
    #: the same reason and with the same effect. Reading an attribute the class
    #: never names is what `undeclared_self_attributes` counts and what left
    #: twelve errors baselined against this file: not one of them was a real
    #: fault, they were all the checker being unable to follow a name into the
    #: object that supplies it. Naming them here costs nothing at runtime -- a
    #: bare annotation binds no value -- and turns the baseline into something
    #: that would notice an actual mistake.
    surface_repository: SurfaceInstallationRepositoryPort
    conversation_link_repository: SurfaceConversationLinkRepository
    credential_resolver: SurfaceCredentialResolver
    adapter_registry: SurfacePlatformAdapterRegistry
    event_dedup_store: SurfaceEventDedupStorePort

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
        fan_in = receiver_surface_ids is None and has_shared_system_bot(platform)
        surfaces: list[AgentSurfaceEntity] = []
        resolved_user: ResolvedSurfaceUser | None = None
        user_pod_ids: set[UUID] | None = None
        if fan_in and parsed.is_dm:
            surfaces, resolved_user, user_pod_ids = await fan_in_candidates(
                platform=platform,
                parsed=parsed,
                adapter=adapter,
                router=self.router,
                surfaces=self.surface_repository,
            )
        created = await created_group_surfaces(
            self.uow,
            self.surface_repository,
            platform=platform,
            parsed=parsed,
            receiver_surface_ids=receiver_surface_ids,
        )
        if created == []:
            return None  # a group the bot created, but not for any pod here
        surfaces = created or surfaces
        if not surfaces:
            surfaces = await self.surface_repository.list_active_for_routing(
                platform,
                surface_ids=receiver_surface_ids,
                system_credentials_only=fan_in,
            )
        if receiver_surface_ids is not None and not surfaces:
            return None
        parsed = await logged_and_addressed(
            self.uow,
            router=self.router,
            surfaces=surfaces,
            parsed=parsed,
            platform=platform,
        )

        candidates = await admitted_surfaces(
            surfaces, parsed, links=self.conversation_link_repository
        )
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
                    identity_surface, arrived_on=arrival_number(parsed)
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
        if matched_surface is None and not parsed.is_dm:
            # In none of these pods: answered only where a group welcomes them.
            matched_surface = await self._outsiders().surface_for(
                candidates, parsed, sender
            )

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
        if parsed is not None and not parsed.is_dm:
            parsed = await own_bot_group_message(
                self.uow,
                router=self.router,
                surface_repository=self.surface_repository,
                links=self.conversation_link_repository,
                surface=surface,
                parsed=parsed,
            )
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
                await self.credential_resolver.for_surface(
                    surface, arrived_on=arrival_number(parsed)
                )
                if surface is not None
                # No surface on this path by definition: the event matched none.
                else await self.credential_resolver.for_platform(
                    platform, None, surface=None, arrived_on=arrival_number(parsed)
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

        credentials = await self.credential_resolver.for_surface(
            surface, arrived_on=arrival_number(parsed)
        )
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
        # Copied on a thread is not asked (see `email_recipients`), unless a
        # line speaks to the agent by a name people call it -- which needs the
        # body, so it is asked here.
        if (
            parsed.platform.is_email
            and not parsed.should_start_conversation
            and not await spoken_to_by_name(self.uow, surface, parsed.message_text)
        ):
            return None

        if claim_delivery and not await take_delivery_claim(
            self.uow, self.event_dedup_store, surface, parsed
        ):
            return None

        # The claim is spent, and everything from here can still fail (sender
        # resolution, routing, the binder, the commit after). Left spent, the
        # inbox's retry reads a "duplicate" and drops the message, and a
        # `DomainError` is terminal and never retried. So it is kept only once
        # preparation has returned -- a `finally`, so cancellation counts too.
        # A replay took no claim, so it has none to give back.
        prepared = False
        try:
            context = await self._prepare_claimed_surface_context(
                surface=surface,
                parsed=parsed,
                adapter=adapter,
                credentials=credentials,
                resolved_user=resolved_user,
                fallback_agent_display_name=fallback_agent_display_name,
            )
            prepared = True
            return context
        finally:
            if claim_delivery and not prepared:
                await hand_back_delivery_claim(
                    self.uow, self.event_dedup_store, surface, parsed
                )

    async def _prepare_claimed_surface_context(
        self,
        *,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
        adapter: SurfacePlatformAdapterPort,
        credentials: dict[str, object],
        resolved_user: ResolvedSurfaceUser | None,
        fallback_agent_display_name: str,
    ) -> AgentSurfaceContext | None:
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
        outsiders = self._outsiders()
        group = await outsiders.group_welcoming(
            surface=surface, parsed=parsed, sender=resolved_user
        )
        if group is not None:
            route = await self.router.resolve_route(surface=surface, parsed=parsed)
            if route is None:
                return None
            return await outsiders.prepare(
                surface=surface,
                parsed=parsed,
                sender=resolved_user,
                group=group,
                route=route,
                binder=self.binder,
            )
        user_id = resolved_user.internal_user_id
        refusal = await self._sender_refusal(
            surface=surface,
            parsed=parsed,
            adapter=adapter,
            resolved_user=resolved_user,
            agent_display_name=fallback_agent_display_name,
        )
        # A sender with no internal user always gets a refusal, so the second
        # arm is only there to narrow the type for what follows.
        if refusal is not None or user_id is None:
            return refusal

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
        return build_chat_context(
            surface=surface,
            parsed=parsed,
            resolved_user=resolved_user,
            user_id=user_id,
            route=route,
            conversation_id=link.conversation_id,
            created_conversation_title=created_conversation_title,
        )

    def _outsiders(self) -> OutsiderDoor:
        """Built per use: it holds nothing but this session and the router's port."""
        return OutsiderDoor(uow=self.uow, membership=self.router.pod_membership_port)

    async def _sender_refusal(
        self,
        *,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
        adapter: SurfacePlatformAdapterPort,
        resolved_user: ResolvedSurfaceUser,
        agent_display_name: str,
    ) -> AgentSurfaceContext | None:
        """The reply owed to a sender who cannot start a run here, else ``None``.

        A stranger, a sender who only needed to confirm a link, someone outside
        the pod, and someone the surface's email policy excludes each get a
        direct reply instead of a conversation.
        """
        if resolved_user.internal_user_id is None:
            return unresolved_sender_context(
                surface=surface,
                parsed=parsed,
                adapter=adapter,
                agent_display_name=agent_display_name,
            )
        confirmation = adapter.linked_sender_confirmation(parsed)
        if confirmation is not None:
            return identity_confirmation_context(
                surface=surface,
                parsed=parsed,
                agent_display_name=agent_display_name,
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
                agent_display_name=agent_display_name,
            )
        if not surface.config.identity.allows_email(resolved_user.email):
            return surface_setup_context(
                surface=surface,
                parsed=parsed,
                agent_display_name=agent_display_name,
            )
        return None
