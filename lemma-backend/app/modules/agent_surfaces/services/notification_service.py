"""One way to tell a person something.

Everything that wants to reach a human — a schedule that finished, a workflow
that needs an answer, an agent messaging a colleague — goes through
:meth:`NotificationService.notify`. Before this existed each of those either had
no path at all or reached for ``surface.send``, which could only re-reply on a
thread the person had already started.

Two rules shape the whole thing:

**The app always gets it.** A chat delivery can fail, expire, or be muted; the
in-app inbox cannot. So the notification row is written unconditionally and the
chat send is the enhancement, not the delivery. "Nobody was told" is never a
possible outcome for a pod member.

**A message you can't reply to is an alert, not a conversation.** Every
notification lands in a conversation the recipient owns, with the outbound text
persisted in it. That is what lets someone answer "yes, do it" six hours later
and have the agent know what "it" was.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.factory import create_authorization_data_service
from app.core.log.log import get_logger
from app.modules.agent.domain.value_objects import (
    MessageDraft,
    MessageKind,
    MessageRole,
)
from app.modules.agent_surfaces.domain.entities import (
    MemberReach,
    Notification,
    NotificationOrigin,
    ReachKind,
)

logger = get_logger(__name__)

# How recently a conversation must have been touched to count as "the one we are
# already in" rather than a new thing to start. Deliberately minutes, not the
# surface's 24h DM reset: a digest arriving the morning after a support chat is
# a new subject, and appending it would read as a non-sequitur.
_LIVE_CONVERSATION_WINDOW = timedelta(minutes=30)


@dataclass
class NotifyOutcome:
    """What actually happened, so callers can tell the agent the truth."""

    notification_id: UUID
    conversation_id: UUID | None = None
    # The channel that took the message, or None when only the inbox has it.
    delivered_via: ReachKind | None = None
    attempted: list[ReachKind] = field(default_factory=list)

    @property
    def reached_a_chat_surface(self) -> bool:
        return self.delivered_via is not None and not self.delivered_via.is_app


class NotificationService:
    def __init__(
        self,
        *,
        uow,
        reach_repository,
        notification_repository,
        conversation_repository,
        surface_repository,
        conversation_link_repository,
        conversation_service,
        ingress_service,
        pod_membership_port=None,
    ):
        self.uow = uow
        self.reach_repository = reach_repository
        self.notification_repository = notification_repository
        self.conversation_repository = conversation_repository
        self.surface_repository = surface_repository
        self.conversation_link_repository = conversation_link_repository
        self.conversation_service = conversation_service
        self.ingress_service = ingress_service
        self.pod_membership_port = pod_membership_port

    async def notify(
        self,
        *,
        pod_id: UUID,
        recipient_user_id: UUID,
        body: str,
        title: str | None = None,
        agent_id: UUID | None = None,
        agent_name: str | None = None,
        origin_type: NotificationOrigin | None = None,
        origin_id: UUID | None = None,
        conversation_id: UUID | None = None,
        attribution: str | None = None,
    ) -> NotifyOutcome | None:
        """Tell one person one thing.

        ``conversation_id`` is a *hint*, not an instruction: it is used only when
        the recipient actually owns that conversation. An agent running as one
        person must not be able to drop a colleague into its own thread, where
        they would read context they were never granted.

        Returns ``None`` when the recipient is not a member of the pod.
        """
        if not await self._is_pod_member(pod_id=pod_id, user_id=recipient_user_id):
            logger.debug(
                "agent_surfaces.notification_service.notify_refused_not_a_member.diagnostic",
                pod_id=str(pod_id),
            )
            return None

        reaches = await self.reach_repository.list_for_user(
            pod_id=pod_id, user_id=recipient_user_id
        )
        chat_reach = self._pick_chat_reach(reaches)

        conversation_id = await self._resolve_conversation(
            pod_id=pod_id,
            recipient_user_id=recipient_user_id,
            hinted_conversation_id=conversation_id,
            chat_reach=chat_reach,
            agent_name=agent_name,
            title=title,
        )

        rendered = f"{attribution}\n\n{body}" if attribution else body

        # Persist BEFORE sending. If the platform call fails the person still has
        # the message in Lemma; if we sent first and then failed to persist, they
        # would have a message on their phone that the agent has no memory of.
        if conversation_id is not None:
            await self._persist_message(
                conversation_id=conversation_id, text=rendered, title=title
            )

        attempted: list[ReachKind] = []
        delivered_via: ReachKind | None = None
        if chat_reach is not None:
            attempted.append(chat_reach.kind)
            if await self._deliver_to_chat(reach=chat_reach, message=rendered):
                delivered_via = chat_reach.kind

        notification = await self.notification_repository.create(
            Notification(
                pod_id=pod_id,
                user_id=recipient_user_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                title=title,
                body=body,
                origin_type=origin_type,
                origin_id=origin_id,
            )
        )
        return NotifyOutcome(
            notification_id=notification.id,
            conversation_id=conversation_id,
            delivered_via=delivered_via or ReachKind.APP,
            attempted=attempted,
        )

    # -- internals ---------------------------------------------------------------

    async def _is_pod_member(self, *, pod_id: UUID, user_id: UUID) -> bool:
        """Fail closed. A missing membership port is a wiring bug, and the safe
        reading of a wiring bug is "reach nobody", not "reach anybody"."""
        if self.pod_membership_port is None:
            logger.debug(
                "agent_surfaces.notification_service.notify_refused_no_membership_port.diagnostic",
            )
            return False
        return pod_id in set(
            await self.pod_membership_port.get_user_pod_ids(user_id)
        )

    def _pick_chat_reach(self, reaches: list[MemberReach]) -> MemberReach | None:
        """The freshest channel this person can actually be reached on.

        Reaches arrive ordered by inbound recency, so this is "wherever they were
        last talking to us". First success wins rather than fanning out: three
        copies of the same message across three apps is how a genuinely useful
        feature comes to read as spam.
        """
        for reach in reaches:
            if reach.kind.is_app:
                continue
            if reach.is_deliverable():
                return reach
        return None

    async def _resolve_conversation(
        self,
        *,
        pod_id: UUID,
        recipient_user_id: UUID,
        hinted_conversation_id: UUID | None,
        chat_reach: MemberReach | None,
        agent_name: str | None,
        title: str | None,
    ) -> UUID | None:
        if hinted_conversation_id is not None:
            if await self._is_owned_by(hinted_conversation_id, recipient_user_id):
                return hinted_conversation_id
            logger.debug(
                "agent_surfaces.notification_service.conversation_hint_rejected.diagnostic",
                conversation_id=str(hinted_conversation_id),
            )

        if chat_reach is not None:
            existing = await self._live_conversation_for_reach(chat_reach)
            if existing is not None:
                return existing

        return await self._open_conversation(
            pod_id=pod_id,
            recipient_user_id=recipient_user_id,
            agent_name=agent_name,
            title=title,
            chat_reach=chat_reach,
        )

    async def _is_owned_by(self, conversation_id: UUID, user_id: UUID) -> bool:
        conversation = await self.conversation_repository.get_conversation(
            conversation_id
        )
        return conversation is not None and conversation.user_id == user_id

    async def _live_conversation_for_reach(
        self, reach: MemberReach
    ) -> UUID | None:
        """Continue a thread only if it is genuinely still going.

        The surface's own DM reset is 24h, which is far too loose to double as
        this test — it exists to stop threads living forever, not to decide
        whether a new subject belongs in an old one.
        """
        if reach.surface_id is None or reach.target is None:
            return None
        link = await self.conversation_link_repository.get_by_external_thread(
            surface_id=reach.surface_id,
            platform=reach.kind.value,
            external_channel_id=reach.target.external_channel_id,
            external_thread_id=reach.target.external_thread_id,
            external_user_id=reach.external_user_id,
        )
        if link is None:
            return None
        touched = link.updated_at
        if touched.tzinfo is None:
            touched = touched.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - touched > _LIVE_CONVERSATION_WINDOW:
            return None
        return link.conversation_id

    async def _open_conversation(
        self,
        *,
        pod_id: UUID,
        recipient_user_id: UUID,
        agent_name: str | None,
        title: str | None,
        chat_reach: MemberReach | None,
    ) -> UUID | None:
        """Start a conversation owned by the recipient.

        Created under the *recipient's* authority, not the caller's. This is the
        boundary that makes cross-member messaging safe: whatever the agent does
        next in this thread, it does with the permissions of the person reading
        it.
        """
        metadata: dict[str, object] = {"source": "notification"}
        if chat_reach is not None and chat_reach.surface_id is not None:
            metadata.update(
                {
                    "surface_id": str(chat_reach.surface_id),
                    "surface_platform": chat_reach.kind.value,
                    "external_user_id": chat_reach.external_user_id,
                }
            )
        try:
            auth_ctx = await create_authorization_data_service(
                self.uow
            ).build_user_context(user_id=recipient_user_id, pod_id=pod_id)
            token = set_current_context(auth_ctx)
            try:
                conversation = await self.conversation_service.create_conversation(
                    pod_id=pod_id,
                    agent_name=agent_name,
                    user_id=recipient_user_id,
                    title=title or "Notification",
                    metadata=metadata,
                )
            finally:
                reset_current_context(token)
        except Exception:
            # A notification with no thread is still worth delivering — the
            # person sees it, they just cannot reply in place.
            logger.debug(
                "agent_surfaces.notification_service.open_conversation_failed.diagnostic",
                pod_id=str(pod_id),
                exc_info=True,
            )
            return None

        if chat_reach is not None:
            await self._repoint_reach_thread(
                reach=chat_reach, conversation_id=conversation.id
            )
        return conversation.id

    async def _repoint_reach_thread(
        self, *, reach: MemberReach, conversation_id: UUID
    ) -> None:
        """Point the person's existing thread at the conversation we just opened,
        so their reply lands where the question is."""
        if reach.surface_id is None or reach.target is None:
            return
        try:
            link = await self.conversation_link_repository.get_by_external_thread(
                surface_id=reach.surface_id,
                platform=reach.kind.value,
                external_channel_id=reach.target.external_channel_id,
                external_thread_id=reach.target.external_thread_id,
                external_user_id=reach.external_user_id,
            )
            if link is None:
                return
            await self.conversation_link_repository.repoint_conversation(
                link_id=link.id,
                conversation_id=conversation_id,
                expected_conversation_id=link.conversation_id,
            )
        except Exception:
            logger.debug(
                "agent_surfaces.notification_service.repoint_failed.diagnostic",
                exc_info=True,
            )

    async def _persist_message(
        self, *, conversation_id: UUID, text: str, title: str | None
    ) -> None:
        """Write the outbound into the conversation.

        ``NOTIFICATION`` rather than ``TEXT`` because it was not produced by a
        turn of the model; it is already replayed into model history, so the next
        run sees what was said and a bare "yes" back has something to attach to.
        """
        try:
            await self.conversation_repository.append_message(
                conversation_id=conversation_id,
                agent_run_id=None,
                draft=MessageDraft(
                    role=MessageRole.ASSISTANT,
                    kind=MessageKind.NOTIFICATION,
                    text=text,
                    metadata={"source": "notification", "title": title},
                ),
            )
        except Exception:
            logger.debug(
                "agent_surfaces.notification_service.persist_message_failed.diagnostic",
                conversation_id=str(conversation_id),
                exc_info=True,
            )

    async def _deliver_to_chat(self, *, reach: MemberReach, message: str) -> bool:
        if reach.surface_id is None or reach.target is None:
            return False
        try:
            surface = await self.surface_repository.get(reach.surface_id)
            if surface is None:
                return False
            return await self.ingress_service.send_agent_message_to_target(
                surface=surface, target=reach.target, message=message
            )
        except Exception:
            # Best-effort by construction: the inbox already has it.
            logger.debug(
                "agent_surfaces.notification_service.chat_delivery_failed.diagnostic",
                reach_kind=reach.kind.value,
                exc_info=True,
            )
            return False
