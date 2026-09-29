"""Deciding which surface, agent and sender an inbound event belongs to.

Resolution: given an event and the surfaces configured for it, pick one, name
the agent that answers, and identify who sent it.

**Who decides where a private message goes.** A surface is ingress, an agent
module and egress; routing only answers *which* pod, surface, agent and
conversation a message belongs to, and it answers by one precedence, highest
first:

1. **Membership.** Only pods the sender belongs to are eligible.
2. **The saved default** (``/surfaces/me``) -- the person's explicit choice, so
   it outranks everything below it, including a verified personal route and an
   existing conversation elsewhere.
3. **A verified personal route** -- the pod the person chose for private chat
   through an installation; it answers before ordinary selection runs, and
   steps aside for a default via `deliverable_default`.
4. **Continuity** -- the surface the person's conversation already lives on.
5. **The oldest candidate**, so the answer is at least deterministic.

Two functions enforce it -- `select_surface` for 2, 4 and 5, and
`deliverable_default` for the personal route's check of 2 -- and they share one
predicate, so a message cannot be routed to a default by one path and to a
personal pod by the other.

One write, and it is deliberate: a saved default that no longer stands is
cleared where it is found, because the alternative is routing reading it and
declining to honour it on every message from then on. Everything else here is a
read, which is what lets onboarding ask the same question before it decides
whether to interrupt somebody.
"""

from __future__ import annotations

from app.modules.agent_surfaces.platforms.common import (
    PLATFORM_TRANSPORT_ERRORS,
)
from app.core.authorization.delegation import DEFAULT_RESPONDER_NAME
from app.modules.agent.contracts import (
    conversations_for_surfaces as agent_conversations,
)
from app.modules.agent_surfaces.services.surface_route_types import (
    ResolvedSurfaceRoute,
)

from contextlib import suppress
from typing import Any
from uuid import UUID


from app.core.infrastructure.db.transaction_locks import connection_released

from app.modules.agent_surfaces.services.agent_naming import agent_name_for_agent_id
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
    ResolvedSurfaceUser,
    SurfaceChannelRoute,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.domain.adapter_port import (
    SurfacePlatformAdapterPort,
)
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.ports import (
    SurfaceInstallationRepositoryPort,
    SurfacePodMembershipPort,
)
from app.modules.agent_surfaces.infrastructure.repositories.conversation_link_repository import (
    SurfaceConversationLinkRepository,
)
from app.modules.agent_surfaces.services.saved_surface_default import (
    deliverable_default,
    default_still_stands,
)
from app.modules.agent_surfaces.services.credential_uniqueness import (
    warn_if_tied_on_one_bot,
)
from app.modules.agent_surfaces.services.credential_resolver import (
    SurfaceCredentialResolver,
)
from app.modules.agent_surfaces.services.identity_resolution_service import (
    SurfaceIdentityResolutionService,
)

logger = get_logger(__name__)

# Recent thread/channel messages fetched per run for group-mention continuity.


def _addressed(parsed: ParsedInboundSurfaceEvent) -> bool:
    """Whether a group message is for the bot: an @mention, or a reply in its thread."""
    return bool(parsed.mentioned_agent or parsed.metadata.get("is_thread_reply"))


def _channel_route_key(
    surface: AgentSurfaceEntity, parsed: ParsedInboundSurfaceEvent
) -> str | None:
    """The conversation key for a Slack or Teams channel message, or None.

    None means the bot does not answer here: the channel is not one it is wired
    to, or nobody addressed it. The route is an allow-list entry -- it says this
    channel is a place the bot answers -- and channels always require an @mention
    or a reply inside a bot thread, with no per-route opt-out.
    """
    route = surface.channel_route_for(
        channel_id=parsed.external_channel_id,
        channel_name=parsed.metadata.get("channel_name"),
    )
    if (
        route is None
        and surface.external_channel_id
        and surface.external_channel_id == parsed.external_channel_id
    ):
        # Surface bound directly to one channel without explicit routes.
        route = SurfaceChannelRoute(channel_id=surface.external_channel_id)
    if route is None or not _addressed(parsed):
        return None
    return (
        f"channel:{parsed.external_channel_id}"
        if parsed.external_channel_id
        else f"channel-name:{route.channel_name}"
    )


class SurfaceRouter:
    """Which surface an inbound event belongs to, and who sent it.

    An object rather than a mixin because it is the bottom of the module's
    dependency graph: inbound and interactions both reach into it and it reaches
    into neither. Flattening it onto one service is what made those two callers
    unable to see their own collaborators -- `self._resolve_sender_identity`
    resolves to nothing for a type checker, so its non-optional return type was
    invisible and every use of the result read as possibly-None.
    """

    def __init__(
        self,
        *,
        uow: SqlAlchemyUnitOfWork,
        surface_repository: SurfaceInstallationRepositoryPort,
        conversation_link_repository: SurfaceConversationLinkRepository,
        pod_membership_port: SurfacePodMembershipPort,
        identity_service: SurfaceIdentityResolutionService,
        credential_resolver: SurfaceCredentialResolver,
    ) -> None:
        self.uow = uow
        self.surface_repository = surface_repository
        self.conversation_link_repository = conversation_link_repository
        self.pod_membership_port = pod_membership_port
        self.identity_service = identity_service
        self.credential_resolver = credential_resolver

    async def matches_user(
        self,
        surfaces: list[AgentSurfaceEntity],
        resolved_user: ResolvedSurfaceUser | None,
    ) -> AgentSurfaceEntity | None:
        """Return the first surface whose pod the resolved user is a member of."""
        if resolved_user is None or resolved_user.internal_user_id is None:
            return None

        user_pod_ids = set(
            await self.pod_membership_port.get_user_pod_ids(
                resolved_user.internal_user_id
            )
        )
        for surface in surfaces:
            if surface.pod_id in user_pod_ids:
                return surface
        return None

    async def reachable_surface(
        self,
        *,
        candidates: list[AgentSurfaceEntity],
        user_id: UUID,
        platform: SurfacePlatform,
        parsed: ParsedInboundSurfaceEvent,
    ) -> AgentSurfaceEntity | None:
        """Where routing would send this person on this platform, if anywhere.

        Selection, asked by somebody who is not in the middle of an inbound
        delivery: the onboarding replay, when the surface it saved has gone.

        It is **not** a reachability test, and onboarding's "is there anywhere
        to talk" deliberately does not use it. Selection can answer with a
        surface the sender is not a member of -- that is its continuity fallback,
        and it exists so ordinary ingestion has somewhere to send the
        access-denied reply. A replay wants that; a question about whether to
        interrupt somebody does not.
        """
        if not candidates:
            return None
        return await self.select_surface(
            candidates=candidates,
            resolved_user=ResolvedSurfaceUser(internal_user_id=user_id),
            parsed=parsed,
            platform=platform.value,
        )

    async def select_surface(
        self,
        *,
        candidates: list[AgentSurfaceEntity],
        resolved_user: ResolvedSurfaceUser | None,
        parsed: ParsedInboundSurfaceEvent,
        platform: str,
        user_pod_ids: set[UUID] | None = None,
    ) -> AgentSurfaceEntity | None:
        """Pick which candidate surface an inbound event belongs to.

        The module docstring's precedence, applied to ordinary delivery --
        deterministic, which is what makes a sender reachable via a shared bot
        across pods in several orgs route consistently: membership, the saved
        default (a *stale* one, pointing at a pod the user left, is cleared and
        ignored), continuity, then the first member candidate (``candidates`` is
        ordered by ``created_at, id``). A verified personal route is decided
        before this runs.

        For an unresolved sender (or one who belongs to no candidate pod), fall
        back to continuity alone. Membership is still re-validated downstream in
        ``_prepare_surface_context`` (which sends the pod-access/signup reply when
        appropriate), so this only decides *which* candidate — never bypasses the
        access check.
        """
        # Resolved once -- it is both a fallback for unresolved senders and the
        # tie-decider when no valid default is set.
        candidates_by_id = {surface.id: surface for surface in candidates}
        continuity_surface = await self._continuity_surface(
            candidates_by_id, parsed=parsed, platform=platform
        )

        # An unresolved sender: continuity is all we have.
        if resolved_user is None or resolved_user.internal_user_id is None:
            return continuity_surface

        user_id = resolved_user.internal_user_id
        # Already in hand when the shared bot's fan-in was narrowed by it; the
        # narrowing and this filter are the same question, so asking twice is
        # one indexed round trip on the busiest path for no new answer.
        if user_pod_ids is None:
            user_pod_ids = set(await self.pod_membership_port.get_user_pod_ids(user_id))
        member_candidates = [s for s in candidates if s.pod_id in user_pod_ids]
        if not member_candidates:
            # No pod the user belongs to; keep continuity if any (membership is
            # re-validated downstream), else nothing to route to.
            return continuity_surface

        member_by_id = {s.id: s for s in member_candidates}

        # 2. A valid saved default is authoritative — it wins over continuity.
        chosen = await self._default_surface(
            user_id=user_id,
            platform=platform,
            member_by_id=member_by_id,
            user_pod_ids=user_pod_ids,
        )
        if chosen is not None:
            return chosen

        # 3. Continuity — reuse the surface this chat already lives on (only when
        # it is a pod the user still belongs to).
        if continuity_surface is not None and continuity_surface.id in member_by_id:
            return continuity_surface

        # 4. Deterministic tiebreak (candidates are ordered by created_at, id).
        # The user can pick a default via GET/PUT /surfaces/me when this happens.
        warn_if_tied_on_one_bot(member_candidates, platform=platform)
        return member_candidates[0]

    async def _continuity_surface(
        self,
        candidates_by_id: dict[UUID, AgentSurfaceEntity],
        *,
        parsed: ParsedInboundSurfaceEvent,
        platform: str,
    ) -> AgentSurfaceEntity | None:
        """The candidate this chat already lives on, if any.

        Narrowed to the candidates, which changes an answer and not just a cost.
        Unnarrowed the lookup returns the freshest link for this chat anywhere on
        the platform, and the caller then keeps it only if it is a candidate -- so
        a fresher link on a non-candidate surface returned an id that was
        immediately discarded, and the person's real ongoing conversation on a
        candidate surface was never looked for. The chat fell through to the
        tiebreak and was answered by a different pod. See
        `find_surface_id_for_external_thread`.

        The exact key names a delivery address, which for a private chat is not
        what makes it the same conversation, so a miss falls back to the same
        person's latest private chat on a candidate -- see
        `find_latest_dm_link_for_person`.
        """
        links = self.conversation_link_repository
        surface_ids = list(candidates_by_id)
        found = await links.find_surface_id_for_external_thread(
            platform=platform,
            external_channel_id=parsed.external_channel_id,
            external_thread_id=parsed.external_thread_id,
            external_user_id=parsed.sender_external_user_id,
            surface_ids=surface_ids,
        )
        person = parsed.sender_external_user_id
        if found is None and surface_ids and parsed.is_dm and person:
            if not parsed.platform.is_email:
                earlier = await links.find_latest_dm_link_for_person(
                    platform=platform, external_user_id=person, surface_ids=surface_ids
                )
                found = earlier.surface_id if earlier is not None else None
        return candidates_by_id.get(found) if found is not None else None

    async def deliverable_default(
        self,
        *,
        user_id: UUID,
        parsed: ParsedInboundSurfaceEvent,
        receiver_surface_ids: list[UUID] | None,
        system_credentials_only: bool = False,
    ) -> AgentSurfaceEntity | None:
        """The saved default, if ordinary selection could route this delivery to it.

        A verified personal route answers a private message before selection
        runs, so without this the saved default -- which the docs call
        authoritative -- was consulted only for people with no personal route,
        and a person who had set one was answered by the other. The route asks
        this and steps aside when it returns a surface; where it returns None
        selection would not pick the default either, so the personal route -- the
        next thing the person chose -- stands. It asks and never writes.
        """
        return await deliverable_default(
            membership=self.pod_membership_port,
            surfaces=self.surface_repository,
            user_id=user_id,
            parsed=parsed,
            receiver_surface_ids=receiver_surface_ids,
            system_credentials_only=system_credentials_only,
        )

    async def _default_surface(
        self,
        *,
        user_id: UUID,
        platform: str,
        member_by_id: dict[UUID, AgentSurfaceEntity],
        user_pod_ids: set[UUID],
    ) -> AgentSurfaceEntity | None:
        """The surface this user chose as their default, if it is still valid.

        A stale default -- one pointing at a surface that is gone, or at a pod
        the user has since left -- is cleared rather than honoured, so routing
        stops silently sending them somewhere they can no longer reach.

        Staleness is a question about the surface and the person, **not** about
        this delivery. ``member_by_id`` holds only the candidates for the message
        in hand, and that set is narrowed twice over -- by which receiver took
        delivery, and by whether system credentials are required. Reading a
        default missing from it as stale deleted somebody's saved choice every
        time a different bot on the same platform received a message, which is
        the ordinary case in any deployment running more than one.
        """
        default_id = await self.pod_membership_port.get_user_default_surface_id(
            user_id, platform
        )
        if default_id is None:
            return None
        if default_id in member_by_id:
            return member_by_id[default_id]
        if await default_still_stands(
            self.surface_repository,
            surface_id=default_id,
            platform=platform,
            user_pod_ids=user_pod_ids,
        ):
            # Valid, just not on this delivery's list. Routing falls through to
            # continuity and the tiebreak; the saved choice is left alone.
            return None

        logger.debug(
            "agent_surfaces.ingress_service.agent_surface_default_user_s.diagnostic",
            user_id=user_id,
            default_id=default_id,
        )
        await self._clear_stale_default(user_id, platform)
        return None

    async def _clear_stale_default(self, user_id: UUID, platform: str) -> None:
        """Forget a default that no longer resolves, best-effort."""
        try:
            await self.pod_membership_port.clear_user_default_surface_id(
                user_id, platform
            )
        except Exception:
            logger.debug(
                "agent_surfaces.ingress_service.clear_stale_surface_default_user.diagnostic",
                user_id=user_id,
            )

    async def enrich_telegram_mention(
        self,
        parsed: ParsedInboundSurfaceEvent,
        surface: AgentSurfaceEntity,
    ) -> ParsedInboundSurfaceEvent:
        """Upgrade mentioned_agent when a Telegram group message actually
        targets this bot.

        The parser records @username / text_mention entities without claiming
        they mention the bot (a `mention` entity is just a plain @username and
        doesn't identify the user). Here we resolve the bot's @username and
        numeric user id via getMe and check:

        - whether ``@{bot_username}`` appears in the mention entities (precise),
        - whether the bot's user id is in the text_mention entities (precise),
        - whether ``@{bot_username}`` appears in the message text (fallback for
          a manually typed name that produced no entity).

        Best-effort; returns the event unchanged on any failure."""
        with suppress(*PLATFORM_TRANSPORT_ERRORS, ImportError):
            from app.modules.agent_surfaces.platforms.telegram.service import (
                TelegramPlatformService,
            )

            credentials = await self.credential_resolver.for_surface(surface)
            service = TelegramPlatformService(credentials)
            bot_username = (await service.get_bot_username() or "").lower()
            bot_user_id = await service.get_bot_user_id()
            metadata = parsed.metadata or {}
            mentioned_usernames = {
                str(name).lower() for name in metadata.get("mentioned_usernames") or []
            }
            text_mention_user_ids = {
                str(uid) for uid in metadata.get("text_mention_user_ids") or []
            }
            text = (parsed.message_text or "").lower()

            matched = False
            if bot_username and bot_username in mentioned_usernames:
                matched = True
            elif bot_user_id and bot_user_id in text_mention_user_ids:
                matched = True
            elif bot_username and f"@{bot_username}" in text:
                matched = True

            if matched:
                return parsed.model_copy(update={"mentioned_agent": True})
        return parsed

    async def resolve_route(
        self,
        *,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
    ) -> ResolvedSurfaceRoute | None:
        """Which agent answers this event, and under what conversation key.

        Every shape is answered by the surface's own agent -- a channel route or
        a Telegram group says *where* the bot answers, never *who*. What differs
        is only the conversation kind and key, and whether the message is for us
        at all, so that is all this decides.
        """
        if parsed.is_dm or surface.surface_type.is_email:
            is_email = surface.surface_type.is_email
            return await self._route(
                surface,
                conversation_kind="EMAIL" if is_email else "DM",
                route_key="email" if is_email else "dm",
            )
        if surface.surface_type is SurfacePlatform.TELEGRAM:
            # Being added to the group by an admin is the authorization, so there
            # is no per-group route config. The sender is still resolved and
            # pod-membership checked upstream, so only pod members can invoke it.
            if not _addressed(parsed):
                return None
            return await self._route(
                surface,
                conversation_kind="CHANNEL",
                route_key=f"channel:{parsed.external_channel_id}",
            )
        if surface.surface_type in {SurfacePlatform.SLACK, SurfacePlatform.TEAMS}:
            route_key = _channel_route_key(surface, parsed)
            if route_key is None:
                return None
            return await self._route(
                surface, conversation_kind="CHANNEL", route_key=route_key
            )
        return None

    async def _route(
        self,
        surface: AgentSurfaceEntity,
        *,
        conversation_kind: str,
        route_key: str,
    ) -> ResolvedSurfaceRoute:
        agent_id = surface.agent_id
        return ResolvedSurfaceRoute(
            pod_id=surface.pod_id,
            agent_id=agent_id,
            agent_name=await self._agent_name_for_agent_id(agent_id),
            agent_display_name=await self._agent_display_name(agent_id),
            conversation_kind=conversation_kind,
            route_key=route_key,
        )

    async def _agent_display_name(self, agent_id: UUID | None) -> str:
        """What this agent calls itself in front of a person.

        Not `agent.name`. The pod's own agent is stored as `pod_default`,
        which is an internal identifier -- it used to have no row at all, so
        every caller wrote `agent_name or "Lemma"` and the null did the work.
        Now that it has one, that expression puts `pod_default` on the message.

        The name it falls back to is `Lem`, not `Lemma`: the pod's agent is an
        actor with a name of its own, and the product is what the bot and the
        sending domain already say. See `agent_display_name`.
        """
        agent = (
            await agent_conversations.surface_agent_identity(self.uow, agent_id)
            if agent_id
            else None
        )
        if agent is None or agent.is_pod_default:
            return DEFAULT_RESPONDER_NAME
        return agent.name

    async def _agent_name_for_agent_id(
        self,
        agent_id: UUID | None,
    ) -> str | None:
        return await agent_name_for_agent_id(self.uow, agent_id)

    @staticmethod
    def scoped_fallback_surface(
        request: SurfacePlatformWebhookIngress,
        surfaces: list[AgentSurfaceEntity],
    ) -> AgentSurfaceEntity | None:
        if request.receiver_surface_ids is not None and surfaces:
            return surfaces[0]
        return None

    async def resolve_sender(
        self,
        *,
        adapter: SurfacePlatformAdapterPort,
        parsed: ParsedInboundSurfaceEvent,
        credentials: dict[str, Any],
        installation_id: UUID | None = None,
    ) -> ResolvedSurfaceUser:
        try:
            async with connection_released(self.uow.session):
                sender_profile = await adapter.fetch_sender_profile(
                    credentials=credentials,
                    event=parsed,
                )
        except Exception:
            sender_profile = None
        resolved = await self.identity_service.resolve(
            event=parsed,
            sender_profile=sender_profile,
            installation_id=installation_id,
        )
        return await self._hydrate_resolved_user(resolved)

    async def _hydrate_resolved_user(
        self,
        resolved_user: ResolvedSurfaceUser,
    ) -> ResolvedSurfaceUser:
        if resolved_user.internal_user_id is not None and not resolved_user.email:
            resolved_user.email = await self.pod_membership_port.get_user_email(
                resolved_user.internal_user_id
            )
        return resolved_user

    def is_self_addressed(
        self,
        *,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
    ) -> bool:
        if not surface.surface_type.is_email:
            return False
        surface_email = str(surface.surface_identity_email or "").strip().lower()
        sender_email = (
            str(parsed.sender_email or parsed.sender_external_user_id or "")
            .strip()
            .lower()
        )
        return bool(surface_email and sender_email and surface_email == sender_email)
