"""Deciding which surface, agent and sender an inbound event belongs to.

Pure resolution: given an event and the surfaces configured for it, pick one,
name the agent that answers, and identify who sent it. Nothing here writes.
"""

from __future__ import annotations

from app.modules.agent_surfaces.services.surface_route_types import (
    ResolvedSurfaceRoute,
)

from contextlib import suppress
from typing import Any
from uuid import UUID


from app.core.infrastructure.db.transaction_locks import connection_released

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
    ResolvedSurfaceUser,
    SurfaceChannelRoute,
    SurfaceMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.domain.ports import (
    SurfacePlatformAdapterPort,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)

# Recent thread/channel messages fetched per run for group-mention continuity.


def _addressed(parsed: ParsedInboundSurfaceEvent) -> bool:
    """Whether a group message is for the bot: an @mention, or a reply in its thread."""
    return bool(parsed.mentioned_agent or parsed.metadata.get("is_thread_reply"))


class SurfaceRoutingMixin:
    async def _match_surface_for_user(
        self,
        surfaces: list[AgentSurfaceEntity],
        resolved_user: ResolvedSurfaceUser | None,
    ) -> AgentSurfaceEntity | None:
        """Return the first surface whose pod the resolved user is a member of."""
        if resolved_user is None or resolved_user.internal_user_id is None:
            return None

        if not self.pod_membership_port:
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

    async def _select_surface(
        self,
        *,
        candidates: list[AgentSurfaceEntity],
        resolved_user: ResolvedSurfaceUser | None,
        parsed: ParsedInboundSurfaceEvent,
        platform: str,
    ) -> AgentSurfaceEntity | None:
        """Pick which candidate surface an inbound event belongs to.

        Deterministic precedence — this is what makes a sender reachable via a
        shared system bot/number across pods in multiple orgs route consistently:

        1. **Pod membership** — only surfaces whose pod the sender belongs to are
           eligible.
        2. **User default (authoritative)** — a valid saved
           ``users.preferences.default_surfaces[platform]`` wins over everything
           else, including an existing conversation on another pod, so changing
           the default re-routes new messages to the chosen pod (starting a fresh
           conversation there). A *stale* default (pointing at a pod the user left)
           is cleared and ignored.
        3. **Continuity** — otherwise reuse the surface an existing conversation
           for this exact chat already lives on, so a returning chat doesn't bounce
           between pods.
        4. **Deterministic tiebreak** — the first member candidate (``candidates``
           is ordered by ``created_at, id``).

        For an unresolved sender (or one who belongs to no candidate pod), fall
        back to continuity alone. Membership is still re-validated downstream in
        ``_prepare_surface_context`` (which sends the pod-access/signup reply when
        appropriate), so this only decides *which* candidate — never bypasses the
        access check.
        """
        # Resolve continuity once — it is both a fallback for unresolved senders
        # and the tie-decider when no valid default is set.
        continuity_id = (
            await self.conversation_link_repository.find_surface_id_for_external_thread(
                platform=platform,
                external_channel_id=parsed.external_channel_id,
                external_thread_id=parsed.external_thread_id,
                external_user_id=parsed.sender_external_user_id,
            )
        )
        continuity_surface = (
            next((s for s in candidates if s.id == continuity_id), None)
            if continuity_id is not None
            else None
        )

        # Unresolved / no-membership-port senders: continuity is all we have.
        if (
            resolved_user is None
            or resolved_user.internal_user_id is None
            or not self.pod_membership_port
        ):
            return continuity_surface

        user_id = resolved_user.internal_user_id
        user_pod_ids = set(await self.pod_membership_port.get_user_pod_ids(user_id))
        member_candidates = [s for s in candidates if s.pod_id in user_pod_ids]
        if not member_candidates:
            # No pod the user belongs to; keep continuity if any (membership is
            # re-validated downstream), else nothing to route to.
            return continuity_surface

        member_by_id = {s.id: s for s in member_candidates}

        # 2. A valid saved default is authoritative — it wins over continuity.
        chosen = await self._default_surface(
            user_id=user_id, platform=platform, member_by_id=member_by_id
        )
        if chosen is not None:
            return chosen

        # 3. Continuity — reuse the surface this chat already lives on (only when
        # it is a pod the user still belongs to).
        if continuity_surface is not None and continuity_surface.id in member_by_id:
            return continuity_surface

        # 4. Deterministic tiebreak (candidates are ordered by created_at, id).
        # The user can pick a default via GET/PUT /surfaces/me when this happens.
        return member_candidates[0]

    async def _default_surface(
        self,
        *,
        user_id: UUID,
        platform: str,
        member_by_id: dict[UUID, AgentSurfaceEntity],
    ) -> AgentSurfaceEntity | None:
        """The surface this user chose as their default, if it is still valid.

        A stale default -- one pointing at a pod the user has since left -- is
        cleared rather than honoured, so routing stops silently sending them
        somewhere they can no longer reach.
        """
        get_default = getattr(
            self.pod_membership_port, "get_user_default_surface_id", None
        )
        if get_default is None:
            return None
        default_id = await get_default(user_id, platform)
        if default_id is None:
            return None
        if default_id in member_by_id:
            return member_by_id[default_id]

        logger.debug(
            "agent_surfaces.ingress_service.agent_surface_default_user_s.diagnostic",
            user_id=user_id,
            default_id=default_id,
        )
        await self._clear_stale_default(user_id, platform)
        return None

    async def _clear_stale_default(self, user_id: UUID, platform: str) -> None:
        """Forget a default that no longer resolves, best-effort."""
        clear_default = getattr(
            self.pod_membership_port, "clear_user_default_surface_id", None
        )
        if clear_default is None:
            return
        try:
            await clear_default(user_id, platform)
        except Exception:
            logger.debug(
                "agent_surfaces.ingress_service.clear_stale_surface_default_user.diagnostic",
                user_id=user_id,
            )

    async def _telegram_text_mention_enrich(
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
        with suppress(Exception):
            from app.modules.agent_surfaces.platforms.telegram.service import (
                TelegramPlatformService,
            )

            credentials = await self._resolve_credentials(surface)
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

    async def _resolve_route(
        self,
        *,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
    ) -> ResolvedSurfaceRoute | None:
        """Which agent answers this event, and under what conversation key."""
        if parsed.is_dm or surface.mode is SurfaceMode.EMAIL:
            return await self._direct_route(surface=surface, parsed=parsed)
        if surface.surface_type is SurfacePlatform.TELEGRAM:
            return await self._telegram_group_route(surface=surface, parsed=parsed)
        if surface.surface_type in {SurfacePlatform.SLACK, SurfacePlatform.TEAMS}:
            return await self._channel_route(surface=surface, parsed=parsed)
        return None

    async def _direct_route(
        self,
        *,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
    ) -> ResolvedSurfaceRoute:
        """A DM or an email: one agent, no per-channel configuration."""
        agent_id = surface.agent_id
        agent_name = await self._agent_name_for_agent_id(agent_id)
        # On Slack a person can choose which agent answers their own DMs.
        # Their choice wins over the workspace default; everyone who has
        # not chosen keeps it, so this is purely additive.
        if surface.surface_type is SurfacePlatform.SLACK:
            # An explicit pod-assistant pick means *no* agent, which is not
            # the same as falling back to the surface default.
            if surface.config.slack.chose_pod_assistant(parsed.sender_external_user_id):
                return ResolvedSurfaceRoute(
                    agent_id=None,
                    agent_name=None,
                    agent_display_name="Lemma",
                    conversation_kind="DM",
                    route_key="dm",
                )
            chosen = await self._slack_chosen_agent(surface, parsed)
            if chosen is not None:
                agent_id, agent_name = chosen

        is_email = surface.mode is SurfaceMode.EMAIL
        return ResolvedSurfaceRoute(
            agent_id=agent_id,
            agent_name=agent_name,
            agent_display_name=agent_name or "Lemma",
            conversation_kind="EMAIL" if is_email else "DM",
            route_key="email" if is_email else "dm",
        )

    async def _slack_chosen_agent(
        self, surface: AgentSurfaceEntity, parsed: ParsedInboundSurfaceEvent
    ) -> tuple[UUID, str] | None:
        """The agent this person picked for their Slack DMs, if it still exists."""
        chosen = surface.config.slack.agent_for_user(parsed.sender_external_user_id)
        if not chosen:
            return None
        agent = await self.conversation_service.agent_repository.get_by_pod_and_name(
            pod_id=surface.pod_id,
            name=chosen,
        )
        if agent is not None:
            return agent.id, agent.name
        # A renamed or deleted agent must not strand the person with a dead DM —
        # fall back to the surface default.
        logger.debug(
            "agent_surfaces.ingress_service.surface_dm_agent_choice_missing.diagnostic",
            pod_id=surface.pod_id,
        )
        return None

    async def _telegram_group_route(
        self,
        *,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
    ) -> ResolvedSurfaceRoute | None:
        """A Telegram group: the bot answers when addressed, on the surface default.

        Being added to the group by an admin is the authorization, so there is no
        per-group route config. The sender is still resolved and pod-membership
        checked upstream, so only pod members can invoke it.
        """
        if not _addressed(parsed):
            return None
        agent_id = surface.agent_id
        agent_name = await self._agent_name_for_agent_id(agent_id)
        return ResolvedSurfaceRoute(
            agent_id=agent_id,
            agent_name=agent_name,
            agent_display_name=agent_name or "Lemma",
            conversation_kind="CHANNEL",
            route_key=f"channel:{parsed.external_channel_id}",
        )

    async def _channel_route(
        self,
        *,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
    ) -> ResolvedSurfaceRoute | None:
        """A Slack or Teams channel, routed to whichever agent it is wired to."""
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
        if route is None:
            return None

        # Channels always require an @mention (or a reply within a bot thread);
        # there is no per-route opt-out.
        if not _addressed(parsed):
            return None

        agent_id, agent_name = await self._resolve_route_agent(
            surface=surface, route=route
        )
        return ResolvedSurfaceRoute(
            agent_id=agent_id,
            agent_name=agent_name,
            agent_display_name=agent_name or "Lemma",
            conversation_kind="CHANNEL",
            route_key=(
                f"channel:{parsed.external_channel_id}"
                if parsed.external_channel_id
                else f"channel-name:{route.channel_name}"
            ),
        )

    async def _resolve_route_agent(
        self,
        *,
        surface: AgentSurfaceEntity,
        route: SurfaceChannelRoute,
    ) -> tuple[UUID | None, str | None]:
        """Resolve a route's agent name to (id, name); a renamed or deleted
        route agent falls back to the surface default agent."""
        # The pod assistant is the *absence* of an agent, so it must short
        # circuit before the surface-default fallback below — otherwise picking
        # it silently routes to whichever agent the surface defaults to.
        if route.use_pod_assistant:
            return None, None
        if route.agent_name:
            agent = (
                await self.conversation_service.agent_repository.get_by_pod_and_name(
                    pod_id=surface.pod_id,
                    name=route.agent_name,
                )
            )
            if agent is not None:
                return agent.id, agent.name
            logger.debug(
                "agent_surfaces.ingress_service.surface_channel_route_agent_s.diagnostic",
                pod_id=surface.pod_id,
            )
        agent_id = surface.agent_id
        return agent_id, await self._agent_name_for_agent_id(agent_id)

    async def agent_name_for_surface(
        self,
        surface: AgentSurfaceEntity,
    ) -> str | None:
        """Whose name a message on this surface goes out under.

        Public because notification delivery names the agent when it opens a
        conversation for a recipient — see ``SurfaceNotificationEgressPort``.
        It was private for exactly as long as it took that cross-service call to
        raise ``AttributeError`` in production; keeping the port and this method
        in step is what stops the next rename doing the same.
        """
        return await self._agent_name_for_agent_id(surface.agent_id)

    async def _agent_name_for_agent_id(
        self,
        agent_id: UUID | None,
    ) -> str | None:
        if agent_id is None:
            return None
        agent = await self.conversation_service.agent_repository.get(agent_id)
        return agent.name if agent else None

    def _resolve_platform(self, source: str) -> str | None:
        platform = SurfacePlatform.from_source(source)
        return platform.value if platform else None

    @staticmethod
    def _scoped_fallback_surface(
        request: SurfacePlatformWebhookIngress,
        surfaces: list[AgentSurfaceEntity],
    ) -> AgentSurfaceEntity | None:
        if request.receiver_surface_ids is not None and surfaces:
            return surfaces[0]
        return None

    async def _resolve_sender_identity(
        self,
        *,
        adapter: SurfacePlatformAdapterPort,
        parsed: ParsedInboundSurfaceEvent,
        credentials: dict[str, Any],
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
        )
        return await self._hydrate_resolved_user(resolved)

    async def _hydrate_resolved_user(
        self,
        resolved_user: ResolvedSurfaceUser,
    ) -> ResolvedSurfaceUser:
        if (
            resolved_user.internal_user_id is not None
            and self.pod_membership_port is not None
            and not resolved_user.email
        ):
            resolved_user.email = await self.pod_membership_port.get_user_email(
                resolved_user.internal_user_id
            )
        return resolved_user

    def _is_self_email_event(
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
