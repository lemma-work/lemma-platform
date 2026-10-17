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

from app.modules.agent_surfaces.domain.credentials import SurfaceCredentials

from datetime import timezone
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
from app.modules.agent_surfaces.domain.envelope import (
    DeliveryReceipt,
    SurfaceEnvelope,
)
from app.modules.agent_surfaces.domain.errors import AgentSurfaceError
from app.modules.agent_surfaces.domain.ports import (
    SurfaceInstallationRepositoryPort,
)
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.services.agent_naming import agent_name_for_surface
from app.modules.agent_surfaces.services.credential_resolver import (
    SurfaceCredentialResolver,
    arrival_number,
)
from app.modules.agent_surfaces.services.free_text_answer import (
    remember_a_prompt_that_arrived_as_words,
)
from app.modules.agent_surfaces.services.channel_lines import (
    WRITTEN_BY_MEMBER,
    ChannelLines,
    ChannelLinesInDatabase,
)
from app.modules.agent_surfaces.services.group_names import sender_name_for
from app.modules.agent_surfaces.services.surface_route_types import SurfaceEgressTarget
from app.modules.channels.contracts import Source, SourceKind
from app.modules.agent_surfaces.services.channel_places import (
    ChannelSeats,
    ChannelSeatsInDatabase,
)
from app.modules.channels.contracts.places import ReplyAddress

logger = get_logger(__name__)


#: How many of a person's private chats on one surface are looked through for
#: the one in a given pod. One per pod they reach through this installation is
#: the ordinary count, and it is rarely more than two.
_PRIVATE_THREADS_CHECKED = 5


class SurfaceDelivery:
    """Resolve where a reply goes, and put one envelope there."""

    def __init__(
        self,
        *,
        uow: SqlAlchemyUnitOfWork,
        surface_repository: SurfaceInstallationRepositoryPort,
        adapter_registry: SurfacePlatformAdapterRegistry,
        credential_resolver: SurfaceCredentialResolver,
        channel_lines: ChannelLines | None = None,
        seats: ChannelSeats | None = None,
    ) -> None:
        # Every one of these is required, and `uow` most of all. The service
        # this was carved out of took either a unit of work or a factory and
        # left seven collaborators `None` in the second mode, which is why
        # every use of `self.uow` in the outbound path was written
        # `getattr(self.uow, "session", None)`. There is one mode here.
        self.uow = uow
        self.surface_repository = surface_repository
        self.adapter_registry = adapter_registry
        self.credential_resolver = credential_resolver
        self.channel_lines = channel_lines or ChannelLinesInDatabase(uow)
        self.seats = seats or ChannelSeatsInDatabase(uow)

    async def egress_credentials(
        self,
        surface: AgentSurfaceEntity,
        *,
        event: ParsedInboundSurfaceEvent | None = None,
    ) -> SurfaceCredentials:
        """What this surface answers with, for this particular reply.

        ``event`` is the inbound message being answered, and it is passed for
        one reason: on a shared WhatsApp line the surface holds no number, so
        the number to answer from is the one the message arrived on. Without it
        every pooled number was answered from the one in settings.

        Absent on a message the agent starts, which has nothing to have arrived
        on -- there the surface's own number, or settings, is the whole answer.
        """
        return await self.credential_resolver.for_surface(
            surface, arrived_on=arrival_number(event)
        )

    async def agent_name_for_surface(self, surface: AgentSurfaceEntity) -> str | None:
        """Whose name a message on this surface goes out under.

        Part of :class:`SurfaceNotificationEgressPort`: notification delivery
        names the agent when it opens a conversation for a recipient. The answer
        itself is `agent_naming`, shared with routing.
        """
        return await agent_name_for_surface(self.uow, surface)

    async def resolve_egress_target(
        self, conversation_id: UUID, *, at: Source | None = None
    ) -> SurfaceEgressTarget | None:
        """Where an outbound message for this conversation goes, and through what.

        The place the conversation's answer goes (``reply_address``): the
        line being answered, or the place's last word -- or ``at``, a place
        chosen for it, written to with what was last heard there. Returns None
        (never raises) when there is none, or it cannot be written to, so
        callers in the agent-run path treat egress as best-effort.
        """
        address = (
            ReplyAddress(source=at, ref=at.delivery_ref)
            if at is not None
            else await self.seats.reply_address(conversation_id)
        )
        if address is None:
            # The one ordinary answer, and the reason the rest are not. Every
            # agent run asks for an egress target, and most conversations are
            # said nowhere outside Lemma -- somebody typed in the web app, and
            # there is nobody on a platform waiting. That stays `debug`.
            #
            # Except a conversation that *was* opened from a platform and whose
            # place has since gone -- a group the platform deleted. Then
            # somebody was waiting there, and the answer goes nowhere.
            opened_on = await agent_conversations.conversation_metadata_value(
                self.uow, conversation_id, "surface_id"
            )
            if opened_on is not None:
                logger.warning(
                    "agent_surfaces.egress.surface_conversation_lost_its_thread.degraded",
                    conversation_id=str(conversation_id),
                    surface_id=opened_on,
                )
                return None
            logger.debug(
                "agent_surfaces.egress.skipped_no_conversation.diagnostic",
                conversation_id=conversation_id,
            )
            return None
        surface_id = address.source.surface_id
        if surface_id is None:
            # A web chat, which shows its answers on its own page. Every branch
            # below is different in kind: the conversation reached us from a
            # platform and somebody there is waiting for an answer that is now
            # not coming.
            return None
        source = address.source
        surface = await self.surface_repository.get(surface_id)
        if surface is None or not surface.is_active:
            logger.warning(
                "agent_surfaces.egress.surface_cannot_answer.degraded",
                conversation_id=str(conversation_id),
                surface_id=str(source.surface_id),
                platform=source.platform,
                reason="deleted" if surface is None else "not active",
                surface_status=None if surface is None else str(surface.status),
            )
            return None

        adapter = self.adapter_registry.get(surface.surface_type)
        if adapter is None:
            logger.warning(
                "agent_surfaces.egress.no_adapter_for_platform.degraded",
                surface_type=str(surface.surface_type),
                conversation_id=str(conversation_id),
                surface_id=str(surface.id),
            )
            return None

        if not address.ref:
            logger.warning(
                "agent_surfaces.egress.place_has_no_inbound_event.degraded",
                conversation_id=str(conversation_id),
                surface_id=str(surface.id),
                platform=str(surface.surface_type),
            )
            return None
        try:
            parsed_event = ParsedInboundSurfaceEvent.model_validate(address.ref)
        except ValidationError:
            # With the traceback: a stored event that no longer parses is a
            # shape change between writing it and reading it, and which field
            # moved is the whole of what a reader needs.
            logger.warning(
                "agent_surfaces.egress.stored_inbound_event_unreadable.degraded",
                conversation_id=str(conversation_id),
                surface_id=str(surface.id),
                platform=str(surface.surface_type),
                exc_info=True,
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
            logger.warning(
                "agent_surfaces.egress.conversation_missing_for_place.degraded",
                conversation_id=str(conversation_id),
                surface_id=str(surface.id),
                platform=str(surface.surface_type),
            )
            return None

        if await self._answers_lemma_message(source, conversation_id):
            parsed_event = parsed_event.model_copy(update={"answers_inbound": False})

        return SurfaceEgressTarget(
            source=source,
            conversation_id=conversation_id,
            agent_id=conversation.agent_id,
            surface=surface,
            pod_id=conversation.pod_id,
            adapter=adapter,
            event=parsed_event,
            credentials=await self.egress_credentials(surface, event=parsed_event),
            conversation_user_id=conversation.user_id,
            audience=conversation.audience,
        )

    async def private_thread_for(self, target: SurfaceEgressTarget) -> UUID | None:
        """This person's own conversation with the agent on this surface, if any.

        A group is where they last spoke, not somewhere private to reach them:
        this answers with the conversation of one of their private places on
        this surface, in this pod -- a personal DM arrives through a company's
        installation and is answered in the person's own pod, and a word meant
        for one pod's member must not land in another pod's -- or with nothing,
        so somebody whose only place is a group is unreachable this way rather
        than reachable in front of everybody.
        """
        external_user_id = target.person_external_id
        if not external_user_id:
            return None
        places = await self.seats.person_places(
            platform=target.surface.surface_type.value,
            external_user_id=external_user_id,
            surface_ids=[target.surface.id],
            limit=_PRIVATE_THREADS_CHECKED,
        )
        for place in places:
            if place.pod_id != target.pod_id or target.conversation_user_id is None:
                continue
            found = await self.seats.conversation_in_channel(
                channel_id=place.channel_id,
                pod_id=target.pod_id,
                agent_id=target.agent_id,
                user_id=target.conversation_user_id,
                contact_id=target.audience.contact_id,
            )
            if found is not None:
                return found
        return None

    async def _answers_lemma_message(
        self, source: Source, conversation_id: UUID
    ) -> bool:
        """Whether what goes out now answers a message typed in Lemma.

        The latest run was started from Lemma, and nobody in the chat has
        written since it started -- someone who has is part of what it answers.
        Only a group's conversation can hold such a run, so nothing else pays
        for the lookup.
        """
        if source.kind is not SourceKind.SHARED:
            return False
        started = await agent_conversations.lemma_message_run_started_at(
            self.uow, conversation_id
        )
        if started is None:
            return False
        last_inbound = source.last_inbound_at
        if last_inbound is None:
            return True
        if last_inbound.tzinfo is None:
            last_inbound = last_inbound.replace(tzinfo=timezone.utc)
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        return started >= last_inbound

    async def egress_metadata(
        self,
        target: SurfaceEgressTarget,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """The message metadata, with whoever is speaking filled in."""
        resolved = dict(metadata or {})
        # The conversation's agent first: it is who actually answers, which is
        # not always the one the surface points at now -- a surface can be
        # moved to another agent, and older conversations keep the name they
        # wore.
        agent_id = target.agent_id or target.surface.agent_id
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
        # The email ``From`` display name, which is not the chat bot's name and
        # not the bot's row name either. An inbox shows it beside a domain that
        # already says the product, so it is one name: the pod's own, when the
        # pod's assistant is writing, because that is what a member calls it.
        # Mail only, so no chat send pays for the pod lookup.
        # Checked before the read, not through ``setdefault``, which would
        # await the lookup only to throw its answer away.
        if target.surface.surface_type.is_email and "email_sender_name" not in resolved:
            resolved["email_sender_name"] = await sender_name_for(
                self.uow,
                is_pod_default=agent is None or agent.is_pod_default,
                agent_name=agent.name if agent is not None else None,
                pod_id=target.surface.pod_id,
            )
        return resolved

    async def deliver_envelope(
        self,
        target: SurfaceEgressTarget,
        *,
        envelope: SurfaceEnvelope,
        metadata: dict[str, object],
        conversation_id: UUID,
    ) -> bool:
        """Hand one envelope to the platform and say whether it arrived."""
        receipt = await self.deliver_envelope_receipt(
            target,
            envelope=envelope,
            metadata=metadata,
            conversation_id=conversation_id,
        )
        return receipt is not None

    async def deliver_envelope_receipt(
        self,
        target: SurfaceEgressTarget,
        *,
        envelope: SurfaceEnvelope,
        metadata: dict[str, Any],
        conversation_id: UUID,
    ) -> DeliveryReceipt | None:
        """Hand one envelope to the platform; its receipt, or None if it did not
        arrive. The receipt says part by part what landed, for a caller that
        must know (held files are released only when theirs did).

        The ladder -- native, then the part's own text, then nothing -- lives in
        ``BaseSurfaceAdapter.deliver``, and this is what is left once the two
        hand-written copies of it are gone: release the connection, send, report.

        Returning ``None`` matters as much as delivering. A prompt that reached
        nobody leaves the run WAITING on an answer that cannot come, so the
        caller un-dedupes and falls back to asking in plain words.

        "Reached nobody" is judged on the prompt, not on the envelope. The
        narration text can land while the choices or the approval card do not,
        and that is the worst outcome there is: the person reads "let me check
        with you first" and is given nothing to answer. That used to report
        True because *something* arrived.
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
                return None
            if receipt.degraded:
                logger.debug(
                    "agent_surfaces.egress.envelope_degraded.diagnostic",
                    conversation_id=str(conversation_id),
                    platform=target.surface.surface_type.value,
                    parts=receipt.degraded,
                )
        lost_prompt = [
            part for part in receipt.undelivered if part in {"choices", "decision"}
        ]
        if lost_prompt:
            logger.error(
                "agent_surfaces.egress.prompt_reached_nobody.failed",
                conversation_id=str(conversation_id),
                platform=target.surface.surface_type.value,
                parts=receipt.undelivered,
            )
            return None
        await remember_a_prompt_that_arrived_as_words(
            self.uow,
            conversation_id=conversation_id,
            envelope=envelope,
            receipt=receipt,
        )
        if envelope.text:
            name = metadata.get("agent_display_name")
            await self.channel_lines.said(
                target=target,
                conversation_id=conversation_id,
                text=envelope.text,
                agent_display_name=name if isinstance(name, str) else None,
                written_by=_member_id(metadata.get(WRITTEN_BY_MEMBER)),
            )
        return receipt


def _member_id(value: object) -> UUID | None:
    """The member a delivery names as its writer, if it names one."""
    try:
        return UUID(str(value)) if value else None
    except ValueError:
        return None
