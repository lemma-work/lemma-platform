"""Turning a received event into a message the agent can be run on.

Preparation, not dispatch: unpack the webhook, build the context the run needs,
persist the inbound message, and fold in anything that arrived alongside it --
a voice note to transcribe, recent channel history for a group mention.
"""

from __future__ import annotations

from app.modules.agent_surfaces.domain.credentials import SurfaceCredentials

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
from app.modules.agent_surfaces.services.surface_candidates import (
    admitted_surfaces,
    fan_in_candidates,
)
from app.modules.agent_surfaces.services.created_groups import created_group_surfaces
from app.modules.agent_surfaces.services.customer_held_secrets import (
    signed_with_a_customer_secret,
)
from app.modules.agent_surfaces.services.group_ingress import (
    logged_and_addressed,
    own_bot_group_message,
)
from app.modules.agent_surfaces.services.group_names import spoken_to_by_name
from app.modules.agent_surfaces.services.outside_the_pod import (
    NOT_OUTSIDE,
    answer_outside_the_pod,
)
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
        if fan_in:
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
        if not surfaces and not fan_in:
            surfaces = await self.surface_repository.list_active_for_routing(
                platform, surface_ids=receiver_surface_ids
            )
        if receiver_surface_ids is not None and not surfaces:
            return None
        parsed = await logged_and_addressed(
            self.uow,
            adapter=adapter,
            credential_resolver=self.credential_resolver,
            seats=self.router.seats,
            surfaces=surfaces,
            parsed=parsed,
        )

        candidates = await admitted_surfaces(surfaces, parsed, seats=self.router.seats)
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
                installation_id=await self.credential_resolver.installation_identity(
                    identity_surface
                ),
                # Every candidate verified against one app's secret, so the
                # first speaks for all of them.
                customer_held_secret=await signed_with_a_customer_secret(
                    self.uow, identity_surface
                ),
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
                adapter=adapter,
                credential_resolver=self.credential_resolver,
                surface_repository=self.surface_repository,
                seats=self.router.seats,
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
        if resolved_user is None:
            credentials = (
                await self.credential_resolver.for_surface(
                    surface, arrived_on=arrival_number(parsed)
                )
                if surface is not None
                # No surface on this path by definition: the event matched none.
                else await self.credential_resolver.for_platform(
                    platform, surface=None, arrived_on=arrival_number(parsed)
                )
            )
            resolved_user = await self.router.resolve_sender(
                adapter=adapter,
                parsed=parsed,
                credentials=credentials,
                installation_id=(
                    await self.credential_resolver.installation_identity(surface)
                    if surface
                    else None
                ),
                customer_held_secret=await signed_with_a_customer_secret(
                    self.uow, surface
                ),
            )
        display_name = agent_display_name(
            (await agent_name_for_surface(self.uow, surface)) if surface else None
        )
        # `prepare_unrouted_context` opens with a message claim, and
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

        # The message-level claim (Slack sends `message` and `app_mention` for
        # one message): on this session, so it commits with what preparation
        # writes and goes away with it if preparation fails -- nothing to hand
        # back. Owned by the inbound row being ingested, whose own retry finds
        # it its own. A replay took its claim already, and asks for none.
        if claim_delivery and not await self.event_dedup_store.claim_message(
            surface_installation_id=surface.id,
            platform=surface.surface_type,
            external_channel_id=parsed.external_channel_id,
            external_thread_id=parsed.external_thread_id,
            external_message_id=parsed.external_message_id,
        ):
            logger.info(
                "agent_surfaces.ingress_service.duplicate_message_dropped.observed",
                surface_type=surface.surface_type,
                external_channel_id=parsed.external_channel_id,
            )
            return None

        return await self._prepare_claimed_surface_context(
            surface=surface,
            parsed=parsed,
            adapter=adapter,
            credentials=credentials,
            resolved_user=resolved_user,
            fallback_agent_display_name=fallback_agent_display_name,
        )

    async def _prepare_claimed_surface_context(
        self,
        *,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
        adapter: SurfacePlatformAdapterPort,
        credentials: SurfaceCredentials,
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
                installation_id=await self.credential_resolver.installation_identity(
                    surface
                ),
                customer_held_secret=await signed_with_a_customer_secret(
                    self.uow, surface
                ),
            )
        outside = await answer_outside_the_pod(
            uow=self.uow,
            router=self.router,
            binder=self.binder,
            surface=surface,
            parsed=parsed,
            sender=resolved_user,
        )
        if outside is not NOT_OUTSIDE:
            return outside
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

        bound = await self.binder.bind_conversation(
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
            conversation_id=bound.conversation_id,
            source_id=bound.source_id,
            created_conversation_title=bound.created_title,
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
