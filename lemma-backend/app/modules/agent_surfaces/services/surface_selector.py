"""Deciding which surface an inbound event belongs to.

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

from uuid import UUID

from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
    ResolvedSurfaceUser,
    SurfacePlatform,
)
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.domain.ports import (
    SurfaceInstallationRepositoryPort,
    SurfacePodMembershipPort,
)
from app.modules.agent_surfaces.services.credential_uniqueness import (
    warn_if_tied_on_one_bot,
)
from app.modules.agent_surfaces.services.channel_places import (
    ChannelSeats,
    ChannelSeatsInDatabase,
)
from app.modules.agent_surfaces.services.saved_surface_default import (
    default_still_stands,
    deliverable_default,
)

logger = get_logger(__name__)


class SurfaceSelector:
    """Which candidate surface an event belongs to, by the precedence above."""

    def __init__(
        self,
        *,
        uow: SqlAlchemyUnitOfWork,
        surface_repository: SurfaceInstallationRepositoryPort,
        pod_membership_port: SurfacePodMembershipPort,
        seats: ChannelSeats | None = None,
    ) -> None:
        self.uow = uow
        self.seats = seats or ChannelSeatsInDatabase(uow)
        self.surface_repository = surface_repository
        self.pod_membership_port = pod_membership_port

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

        Narrowed to the candidates, which changes an answer and not just a
        cost: unnarrowed, a place on a non-candidate surface would be found and
        then discarded, and the person's real place on a candidate never
        looked for -- the chat would fall to the tiebreak and be answered by a
        different pod.

        A group is the place hearing its chat. A private chat is the person's:
        the place they last wrote to privately on a candidate, which is the
        same conversation whatever number or thread they write from now.
        Email is the exception -- an address is not a person's chat, so a new
        email thread is placed afresh.
        """
        surface_ids = list(candidates_by_id)
        if not surface_ids:
            return None
        person = parsed.sender_external_user_id
        if not parsed.is_dm:
            if not parsed.external_channel_id:
                return None
            places = await self.seats.places_hearing(
                platform=platform,
                external_channel_id=parsed.external_channel_id,
                surface_ids=surface_ids,
            )
        elif person and not parsed.platform.is_email:
            places = await self.seats.person_places(
                platform=platform,
                external_user_id=person,
                surface_ids=surface_ids,
                limit=1,
            )
        else:
            return None
        for place in places:
            found = candidates_by_id.get(place.surface_id) if place.surface_id else None
            if found is not None:
                return found
        return None

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
            # Left in place, a stale default is read and declined again on
            # every message this person sends.
            logger.warning(
                "agent_surfaces.surface_selector.stale_default_not_cleared.degraded",
                user_id=str(user_id),
                exc_info=True,
            )

    @staticmethod
    def scoped_fallback_surface(
        request: SurfacePlatformWebhookIngress,
        surfaces: list[AgentSurfaceEntity],
    ) -> AgentSurfaceEntity | None:
        if request.receiver_surface_ids is not None and surfaces:
            return surfaces[0]
        return None
