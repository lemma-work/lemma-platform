"""How a message physically leaves Lemma for a chat surface.

One object with one job, and it is the mechanism rather than the vocabulary:
given a conversation, find the surface, adapter, thread and credentials a reply
goes to, and hand one :class:`SurfaceEnvelope` to that adapter. Everything that
*decides what to say* -- an answer, a question, an approval card, a file -- is
:class:`SurfaceEgress`, which holds one of these.

Why it is its own object: these three methods are the only part of egress that
:class:`SurfaceProgress` also needs, and progress speaks a different adapter API
(edit a live message) from the one here (deliver an envelope). Sharing them by
inheritance is what produced an eight-base ingress service where the type
checker could not see which collaborator any method was reaching for.

Nothing here reads an inbound event.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import ValidationError

from app.core.authorization.delegation import DEFAULT_RESPONDER_NAME
from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent.contracts import (
    conversations_for_surfaces as agent_conversations,
)
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
)
from app.modules.agent_surfaces.domain.envelope import SurfaceEnvelope
from app.modules.agent_surfaces.domain.errors import AgentSurfaceError
from app.modules.agent_surfaces.domain.ports import (
    SurfaceInstallationRepositoryPort,
)
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.infrastructure.repositories.conversation_link_repository import (
    SurfaceConversationLinkRepository,
)
from app.modules.agent_surfaces.services.agent_naming import agent_name_for_surface
from app.modules.agent_surfaces.services.credential_resolver import (
    SurfaceCredentialResolver,
)
from app.modules.agent_surfaces.services.free_text_answer import (
    remember_a_prompt_that_arrived_as_words,
)
from app.modules.agent_surfaces.services.surface_route_types import SurfaceEgressTarget

logger = get_logger(__name__)


class SurfaceDelivery:
    """Resolve where a reply goes, and put one envelope there."""

    def __init__(
        self,
        *,
        uow: SqlAlchemyUnitOfWork,
        surface_repository: SurfaceInstallationRepositoryPort,
        conversation_link_repository: SurfaceConversationLinkRepository,
        adapter_registry: SurfacePlatformAdapterRegistry,
        credential_resolver: SurfaceCredentialResolver,
    ) -> None:
        # Every one of these is required, and `uow` most of all. The service
        # this was carved out of took either a unit of work or a factory and
        # left seven collaborators `None` in the second mode, which is why
        # every use of `self.uow` in the outbound path was written
        # `getattr(self.uow, "session", None)`. There is one mode here.
        self.uow = uow
        self.surface_repository = surface_repository
        self.conversation_link_repository = conversation_link_repository
        self.adapter_registry = adapter_registry
        self.credential_resolver = credential_resolver

    async def egress_credentials(self, surface: AgentSurfaceEntity) -> dict[str, Any]:
        return await self.credential_resolver.for_surface(surface)

    async def agent_name_for_surface(self, surface: AgentSurfaceEntity) -> str | None:
        """Whose name a message on this surface goes out under.

        Part of :class:`SurfaceNotificationEgressPort`: notification delivery
        names the agent when it opens a conversation for a recipient. The answer
        itself is `agent_naming`, shared with routing.
        """
        return await agent_name_for_surface(self.uow, surface)

    async def resolve_egress_target(
        self, conversation_id: UUID
    ) -> SurfaceEgressTarget | None:
        """Resolve the surface/adapter/event for an outbound message.

        Returns None (never raises) when the conversation has no active surface
        link or its stored ``last_event`` is missing/unparseable, so callers in
        the agent-run path treat egress as best-effort.
        """
        link = await self.conversation_link_repository.get_by_conversation_id(
            conversation_id
        )
        if link is None:
            logger.debug(
                "agent_surfaces.egress.skipped_no_conversation.diagnostic",
                conversation_id=conversation_id,
            )
            return None

        surface = await self.surface_repository.get(link.surface_id)
        if surface is None or not surface.is_active:
            logger.debug(
                "agent_surfaces.egress.skipped_surface_missing.diagnostic",
                conversation_id=conversation_id,
                surface_id=link.surface_id,
            )
            return None

        adapter = self.adapter_registry.get(surface.surface_type)
        if adapter is None:
            logger.debug(
                "agent_surfaces.egress.skipped_no_adapter.diagnostic",
                surface_type=surface.surface_type,
                conversation_id=conversation_id,
            )
            return None

        if not link.last_event:
            logger.debug(
                "agent_surfaces.egress.skipped_missing_last.diagnostic",
                conversation_id=conversation_id,
            )
            return None
        try:
            parsed_event = ParsedInboundSurfaceEvent.model_validate(link.last_event)
        except ValidationError:
            logger.debug(
                "agent_surfaces.egress.skipped_invalid_last.diagnostic",
                conversation_id=conversation_id,
            )
            return None

        # The pod comes from the conversation, not from the surface. They are
        # the same thing for a channel or a shared bot, and deliberately not for
        # a personal DM, where the installation belongs to the company and the
        # conversation to the person. Reading pod files or table rows off the
        # installation resolved them in the wrong pod.
        conversation = await agent_conversations.surface_conversation(
            self.uow, conversation_id
        )
        if conversation is None:
            logger.debug(
                "agent_surfaces.egress.skipped_no_conversation.diagnostic",
                conversation_id=conversation_id,
            )
            return None

        return SurfaceEgressTarget(
            link=link,
            surface=surface,
            pod_id=conversation.pod_id,
            adapter=adapter,
            event=parsed_event,
            credentials=await self.egress_credentials(surface),
        )

    async def egress_metadata(
        self,
        target: SurfaceEgressTarget,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """The message metadata, with whoever is speaking filled in."""
        resolved = dict(metadata or {})
        # The link first: it records which agent actually answered this thread,
        # which is not always the one the surface points at now -- a surface can
        # be moved to another agent, and older threads keep the name they wore.
        agent_id = target.link.routed_agent_id or target.surface.agent_id
        agent = (
            await agent_conversations.surface_agent_identity(self.uow, agent_id)
            if agent_id
            else None
        )
        # Not `agent.name` unconditionally: the pod's own agent is stored as
        # `pod_default`, an internal identifier that used to be absent entirely,
        # so every caller wrote `or "Lemma"` and the null did the work. It has a
        # name of its own now, and it is not the product's -- see
        # `DEFAULT_RESPONDER_NAME`. Written as one expression rather than
        # through an `is_default` flag so the None case narrows: behind the flag
        # this read as `agent.name` on a possibly-absent agent.
        resolved.setdefault(
            "agent_display_name",
            agent.name
            if agent is not None and not agent.is_pod_default
            else DEFAULT_RESPONDER_NAME,
        )
        if agent is not None and agent.icon_url:
            resolved.setdefault("agent_icon_url", str(agent.icon_url))
        return resolved

    async def deliver_envelope(
        self,
        target: SurfaceEgressTarget,
        *,
        envelope: SurfaceEnvelope,
        metadata: dict[str, Any],
        conversation_id: UUID,
    ) -> bool:
        """Hand one envelope to the platform and say whether it arrived.

        The ladder -- native, then the part's own text, then nothing -- lives in
        ``BaseSurfaceAdapter.deliver``, and this is what is left once the two
        hand-written copies of it are gone: release the connection, send, report.

        Returning ``False`` matters as much as delivering. A prompt that reached
        nobody leaves the run WAITING on an answer that cannot come, so the
        caller un-dedupes and a later WAITING event tries again.
        """
        # No connection held for the platform call; see `connection_released`.
        async with connection_released(self.uow.session):
            try:
                receipt = await target.adapter.deliver(
                    credentials=target.credentials,
                    event=target.event,
                    envelope=envelope,
                    metadata=metadata,
                )
            except AgentSurfaceError:
                # An error, not a warning, and with the traceback. This is the
                # end of every ladder: native, then text, then nobody. A run
                # left WAITING on a prompt that reached nobody cannot be seen or
                # acted on by the person it was for.
                logger.error(
                    "agent_surfaces.egress.envelope_reached_nobody.failed",
                    conversation_id=str(conversation_id),
                    platform=target.surface.surface_type.value,
                    exc_info=True,
                )
                return False
            if receipt.degraded:
                logger.debug(
                    "agent_surfaces.egress.envelope_degraded.diagnostic",
                    conversation_id=str(conversation_id),
                    platform=target.surface.surface_type.value,
                    parts=receipt.degraded,
                )
        await remember_a_prompt_that_arrived_as_words(
            self.uow,
            conversation_id=conversation_id,
            envelope=envelope,
            receipt=receipt,
        )
        return True
