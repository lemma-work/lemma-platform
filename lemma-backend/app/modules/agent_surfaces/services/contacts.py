"""Answering somebody outside the pod who writes to its bot privately.

Routing has refused every private message from somebody who is not in the pod,
pointing them at sign-up or at a request for access. That stays the answer
unless the bot is the pod's own and its contact policy says otherwise (see
``SurfaceContactPolicy``). Then the sender is a **contact**:

* **Who they are** is a handle something trustworthy vouched for: the WhatsApp
  number in a payload Meta signed, the Telegram user id in one Telegram did, an
  email address the receiving mail service authenticated, and -- on a bot in a
  Slack workspace the pod's organization holds -- a full member of that
  workspace in an event Slack signed (``slack_workspace_people``). Nothing
  else -- not a display name, not an address typed into a message -- makes
  somebody a contact.
* **Where it runs.** In a private conversation of their own, belonging to the
  member who looks after the bot's contacts and marked with the contact's id,
  so every run in it authorizes as nobody and reads only what is Public (see
  ``agent.domain.outsiders``).
* **How often.** ``ContactTurnLimiter``, per contact and per bot, and a ceiling
  on how many strangers a bot may meet in a day.

**Email that was not authenticated gets no reply at all.** A reply goes to the
``From:`` line, which is text the sender chose, so answering a forged message
mails whoever the forger named. Such mail is parked instead: the member who
looks after contacts is told, and may answer by hand if it is genuine. **Mail a
machine sent** -- an out-of-office, a bounce, a list, one of our own bots -- is
not answered either, nor parked: answering it is how two robots loop.

**What a person says about hearing from the pod** is read first, before
anything decides whether they are answered (``preferences``): whoever looks
after contacts, whether the bot answers them at all, whether they are a
contact yet. "STOP" -- or an email whose subject is "unsubscribe", which is
what a mail client's unsubscribe button sends -- opts the handle out
(``contacts.contracts.outreach``) and starts nothing, even from a machine:
opting out is never the wrong way to fail. A message the person typed opts
them back in, "START" explicitly; one a machine sent (an out-of-office, a
bounce) or one nothing vouched for (unauthenticated mail) never does.
"""

from __future__ import annotations

from uuid import UUID

from app.core.config import settings
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
    ResolvedSurfaceUser,
    SurfaceCredentialMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.ingress_context import (
    SurfaceChatContext,
    SurfaceReplyContext,
)
from app.modules.agent_surfaces.domain.ports import SurfacePodMembershipPort
from app.modules.agent_surfaces.domain.surface_config import ContactAnswer
from app.modules.agent_surfaces.services.direct_messages import tell_member
from app.modules.agent.contracts.audience import Audience
from app.modules.agent_surfaces.services.chat_context_builder import build_chat_context
from app.modules.agent_surfaces.services.contact_windows import ContactWindows
from app.modules.agent_surfaces.services.conversation_binder import ConversationBinder
from app.modules.agent_surfaces.services.fallback_reply_service import (
    contact_refusal_context,
    to_sender_alone,
)
from app.modules.agent_surfaces.services.opt_out_notes import note_opt_out
from app.modules.agent_surfaces.services.outsider_limits import ContactTurnLimiter
from app.modules.agent_surfaces.services.sender_handles import (
    SenderHandle,
    private_handle,
)
from app.modules.agent_surfaces.services.slack_workspace_people import (
    WorkspacePeople,
)
from app.modules.agent_surfaces.services.surface_route_types import (
    ResolvedSurfaceRoute,
)
from app.modules.contacts.contracts import (
    ContactRef,
    IdentityStrength,
    find_contact,
    note_inbound,
    open_contact,
)
from app.modules.contacts.contracts.outreach import (
    SuppressionReason,
    heard_from,
    is_start_request,
    is_stop_request,
    opt_out,
)
from app.modules.pod.contracts.members import pod_member_id

logger = get_logger(__name__)

_PARKED_BODY_CHARS = 500

#: What the contact path does with a message, when it starts no run.
ContactOutcome = SurfaceChatContext | SurfaceReplyContext | None


def is_pods_own_bot(surface: AgentSurfaceEntity) -> bool:
    """Whether this surface is the pod's own, rather than Lemma's shared bot.

    An email surface always is: it receives on an address of its own. A chat
    bot is when it runs on the pod's own credentials or account -- a shared
    system bot or a pooled number is every pod's at once, and a stranger
    writing to it is writing to Lemma, not to this pod.
    """
    if surface.surface_type.is_email:
        return True
    return (
        surface.account_id is not None
        or surface.credential_mode is not SurfaceCredentialMode.SYSTEM
    )


def machine_mail(
    surface: AgentSurfaceEntity, parsed: ParsedInboundSurfaceEvent
) -> str | None:
    """Why this email is a machine's, not a person's, or ``None``.

    The parser reads the headers that say so; this adds our own addresses,
    which no header marks: one of our bots writing to another is a loop the
    sender cannot see.
    """
    if not parsed.platform.is_email:
        return None
    reason = parsed.metadata.get("automated")
    if isinstance(reason, str) and reason:
        return reason
    sender = (parsed.sender_email or "").strip().lower()
    if sender and sender in _our_addresses(surface):
        return "our_own_address"
    domain = (surface_settings.resend_inbound_domain or "").strip().lower()
    if domain and sender.endswith(f"@{domain}"):
        return "our_own_address"
    return None


def _asks_to_stop(parsed: ParsedInboundSurfaceEvent) -> bool:
    """A whole message asking to stop, or an email whose whole subject does:
    what a mail client sends to a ``List-Unsubscribe`` mailto, with a body of
    its own words."""
    if is_stop_request(parsed.message_text):
        return True
    subject = parsed.metadata.get("subject") if parsed.platform.is_email else None
    return isinstance(subject, str) and is_stop_request(subject)


def _our_addresses(surface: AgentSurfaceEntity) -> set[str]:
    candidates = (
        surface.surface_identity_email,
        settings.resend_from_email,
        settings.smtp_from_email,
    )
    return {address.strip().lower() for address in candidates if address}


def parked_mail_notice(
    *,
    surface: AgentSurfaceEntity,
    parsed: ParsedInboundSurfaceEvent,
    owner: UUID,
    member_id: UUID,
) -> str:
    """The note for one unanswered, unauthenticated email."""
    del owner, member_id
    sender = (parsed.sender_email or parsed.sender_external_user_id or "")[:320]
    subject = str(parsed.metadata.get("subject") or "").strip()
    excerpt = parsed.message_text.strip()[:_PARKED_BODY_CHARS]
    return _inbox_note(
        title=f"Unverified email from {sender}",
        body=(
            f"An email to {surface.surface_identity_email or surface.name} could "
            "not be verified as coming from the address it names, so the bot did "
            "not reply: a reply would go to that address, whoever really sent it. "
            "If it is genuine, answer it from your own email.\n\n"
            + (f"Subject: {subject}\n\n" if subject else "")
            + excerpt
        ),
    )


def parked_mail_summary(
    *, surface: AgentSurfaceEntity, owner: UUID, member_id: UUID
) -> str:
    """The one note that stands for the rest of an hour's unverified email."""
    del owner, member_id
    return _inbox_note(
        title="More unverified email",
        body=(
            f"More email to {surface.surface_identity_email or surface.name} "
            "could not be verified this hour than the bot leaves notes for. None "
            "of it was answered. Mail sent from forged addresses often arrives "
            "like this; any that is genuine will arrive again from a verified "
            "address."
        ),
    )


def _inbox_note(*, title: str, body: str) -> str:
    """A notice to the member, as the line it is in their channel."""
    return f"{title}\n\n{body}"


class ContactDoor:
    """Decides whether a private message from outside the pod is a contact's."""

    def __init__(
        self,
        *,
        uow: SqlAlchemyUnitOfWork,
        membership: SurfacePodMembershipPort,
        limiter: ContactTurnLimiter | None = None,
        windows: ContactWindows | None = None,
        people: WorkspacePeople | None = None,
    ) -> None:
        self.uow = uow
        self.membership = membership
        self.limiter = limiter or ContactTurnLimiter()
        self.windows = windows or ContactWindows()
        self.people = people or WorkspacePeople(uow)

    async def preferences(
        self,
        *,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
        sender: ResolvedSurfaceUser,
    ) -> bool:
        """Record what an outsider's private message says about hearing from
        the pod; whether that is all it said (STOP, START), and nothing runs.

        Asked of every private message to the pod's own bot from somebody not
        in the pod, before anything else about them: an opt-out holds with
        nobody looking after contacts, and for a handle no contact has yet.

        Not on Slack: nothing the pod sends reaches a Slack handle but an
        answer, so there is nothing to opt out of, and a colleague typing
        "stop" to a bot means the conversation, not the pod's mail. It is
        answered as anything else they write.
        """
        handle = private_handle(parsed)
        if not parsed.is_dm or handle is None or not is_pods_own_bot(surface):
            return False
        if sender.internal_user_id is not None and await self._in_pod(
            sender.internal_user_id, surface.pod_id
        ):
            return False
        if _asks_to_stop(parsed):
            if not handle.vouched_for:
                # Anybody can write "STOP" under somebody else's address. An
                # opt-out keyed by a forged sender would cut the real person off
                # from the pod's mail, so one nothing vouched for is parked for
                # the member who looks after contacts, like any unverified mail
                # (``prepare``). The person is not stuck: every email the pod
                # sends carries a signed one-click unsubscribe that does not
                # depend on whose address a reply comes from.
                logger.info(
                    "agent_surfaces.contacts.unverified_stop_parked.observed",
                    surface_id=str(surface.id),
                )
                return False
            await self._stop(surface, handle, platform=parsed.platform.value)
            return True
        typed = handle.vouched_for and machine_mail(surface, parsed) is None
        lifted = await heard_from(
            self.uow,
            pod_id=surface.pod_id,
            kind=handle.kind,
            value=handle.value,
            typed=typed,
        )
        if lifted:
            logger.info(
                "agent_surfaces.contacts.opted_back_in.observed",
                surface_id=str(surface.id),
                explicitly=is_start_request(parsed.message_text),
            )
        return is_start_request(parsed.message_text)

    async def applies(
        self,
        *,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
        sender: ResolvedSurfaceUser,
    ) -> bool:
        """Whether this message is for the contact path rather than refusal.

        A member is never a contact, wherever they write from: they route as
        themselves. Asked the way routing asks it, so a sender cannot be a
        member to one and a contact to the other.
        """
        policy = surface.config.contacts
        if (
            not parsed.is_dm
            or policy.answer is ContactAnswer.OFF
            or not is_pods_own_bot(surface)
            or (
                parsed.platform is not SurfacePlatform.SLACK
                and private_handle(parsed) is None
            )
        ):
            return False
        if sender.internal_user_id is not None and await self._in_pod(
            sender.internal_user_id, surface.pod_id
        ):
            return False
        return await self._contact_handle_of(surface, parsed, sender) is not None

    async def _contact_handle_of(
        self,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
        sender: ResolvedSurfaceUser,
    ) -> SenderHandle | None:
        """The handle a private message from outside the pod names its sender by.

        On Slack, only a full member of a workspace the pod's organization
        holds has one (``slack_workspace_people``): anybody else writing to a
        workspace bot privately is refused, as before.
        """
        if parsed.platform is SurfacePlatform.SLACK:
            return await self.people.colleague_handle(
                surface, parsed, sender, private=True
            )
        return private_handle(parsed)

    async def prepare(
        self,
        *,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
        sender: ResolvedSurfaceUser,
        route: ResolvedSurfaceRoute,
        binder: ConversationBinder,
    ) -> ContactOutcome:
        """The contact's run, a refusal to send, or None when nothing should happen.

        None covers every quiet outcome: unauthenticated mail (parked), mail a
        machine sent, "STOP", nobody left to look after contacts, and a limit
        reached. Each is either somebody we cannot be sure is there, something
        that must not be answered, or somebody we already decided not to.
        """
        handle = await self._contact_handle_of(surface, parsed, sender)
        owner = surface.config.contacts.looked_after_by
        if (
            handle is None
            or owner is None
            or not await self._looked_after(surface, owner)
        ):
            return None
        machine = machine_mail(surface, parsed)
        if machine is not None:
            logger.info(
                "agent_surfaces.contacts.machine_mail_ignored.observed",
                surface_id=str(surface.id),
                reason=machine,
            )
            return None
        if not handle.vouched_for:
            logger.info(
                "agent_surfaces.contacts.unverified_email_parked.observed",
                surface_id=str(surface.id),
            )
            await self._park(surface=surface, parsed=parsed, owner=owner)
            return None
        contact = await self._contact(surface=surface, handle=handle, parsed=parsed)
        if contact is None:
            return await self._refusal(surface, parsed, handle, route)
        if not await self.limiter.allow(surface_id=surface.id, contact_id=contact.id):
            return None
        # Stamped again now that they are a contact: a stranger's first message
        # had no handle row for ``preferences`` to stamp.
        await note_inbound(
            self.uow, pod_id=surface.pod_id, kind=handle.kind, value=handle.value
        )
        return await self._contacts_turn(
            surface=surface,
            parsed=parsed,
            sender=sender,
            route=route,
            binder=binder,
            contact=contact,
            owner=owner,
        )

    async def _contacts_turn(
        self,
        *,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
        sender: ResolvedSurfaceUser,
        route: ResolvedSurfaceRoute,
        binder: ConversationBinder,
        contact: ContactRef,
        owner: UUID,
    ) -> SurfaceChatContext:
        # Only the contact. On an email thread a reply would otherwise copy
        # everybody else on it, and a contact's conversation is theirs.
        parsed = to_sender_alone(parsed)
        bound = await binder.bind_conversation(
            surface=surface,
            parsed=parsed,
            resolved_user=ResolvedSurfaceUser(
                internal_user_id=owner,
                external_user_id=sender.external_user_id,
                display_name=contact.display_name or sender.display_name,
            ),
            route=route,
            contact_id=contact.id,
        )
        return build_chat_context(
            surface=surface,
            parsed=parsed,
            resolved_user=sender,
            user_id=owner,
            route=route,
            conversation_id=bound.conversation_id,
            source_id=bound.source_id,
            created_conversation_title=bound.created_title,
            audience=Audience.contact(contact.id),
        )

    async def _looked_after(self, surface: AgentSurfaceEntity, owner: UUID) -> bool:
        """Whether the member who looks after this bot's contacts is still here.

        A member who has left the pod looks after nothing: every question
        passed on would reach somebody who can no longer act on it.
        """
        if await self._in_pod(owner, surface.pod_id):
            return True
        logger.info(
            "agent_surfaces.contacts.nobody_looks_after.observed",
            surface_id=str(surface.id),
        )
        return False

    async def _in_pod(self, user_id: UUID, pod_id: UUID) -> bool:
        return pod_id in set(await self.membership.get_user_pod_ids(user_id))

    async def _stop(
        self, surface: AgentSurfaceEntity, handle: SenderHandle, *, platform: str
    ) -> None:
        """Opt the handle out; a contact's channel is told, the first time."""
        newly = await opt_out(
            self.uow,
            pod_id=surface.pod_id,
            kind=handle.kind,
            value=handle.value,
            reason=SuppressionReason.STOP,
        )
        contact = (
            await find_contact(
                self.uow, pod_id=surface.pod_id, kind=handle.kind, value=handle.value
            )
            if newly
            else None
        )
        if contact is not None:
            await note_opt_out(
                self.uow,
                pod_id=surface.pod_id,
                contact_id=contact.id,
                platform=platform,
            )
        logger.info(
            "agent_surfaces.contacts.opted_out_by_message.observed",
            surface_id=str(surface.id),
            newly=newly,
        )

    async def _contact(
        self,
        *,
        surface: AgentSurfaceEntity,
        handle: SenderHandle,
        parsed: ParsedInboundSurfaceEvent,
    ) -> ContactRef | None:
        known = await find_contact(
            self.uow, pod_id=surface.pod_id, kind=handle.kind, value=handle.value
        )
        if known is not None:
            return known
        if surface.config.contacts.answer is not ContactAnswer.ANYONE:
            logger.info(
                "agent_surfaces.contacts.stranger_refused.observed",
                surface_id=str(surface.id),
            )
            return None
        if not await self.limiter.allow_new_contact(surface_id=surface.id):
            return None
        contact = await open_contact(
            self.uow,
            pod_id=surface.pod_id,
            kind=handle.kind,
            value=handle.value,
            strength=IdentityStrength.CHANNEL,
            display_name=parsed.sender_display_name,
        )
        logger.info(
            "agent_surfaces.contacts.contact_opened.observed",
            surface_id=str(surface.id),
            contact_id=str(contact.id),
        )
        return contact

    async def _refusal(
        self,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
        handle: SenderHandle,
        route: ResolvedSurfaceRoute,
    ) -> SurfaceReplyContext | None:
        """One short refusal a day to a stranger at a bot for known contacts only.

        Only there: a bot that answers anyone and stopped meeting strangers for
        the day stays quiet, rather than telling a flood it is being refused.
        """
        if surface.config.contacts.answer is not ContactAnswer.KNOWN:
            return None
        if not await self.windows.stranger_refusal(surface.id, handle.value):
            return None
        return contact_refusal_context(
            surface=surface,
            parsed=parsed,
            agent_display_name=route.agent_display_name or "This bot",
        )

    async def _park(
        self,
        *,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
        owner: UUID,
    ) -> None:
        """Tell the member about unverified mail: once an hour per sender, and
        at most ``surface_parked_mail_notes_per_surface_per_hour`` notes a bot,
        then one that says more came.

        A forged sender can be made to write as often, and from as many
        addresses, as the forger likes; the member should hear of it, not be
        buried by it.
        """
        sender = (parsed.sender_email or parsed.sender_external_user_id or "")[:320]
        if not await self.windows.parked_sender(surface.id, sender):
            return
        count = await self.windows.parked_note(surface.id)
        limit = surface_settings.surface_parked_mail_notes_per_surface_per_hour
        if count is None or count > limit + 1:
            return
        member_id = await pod_member_id(self.uow, surface.pod_id, owner)
        if member_id is None:
            return
        note = (
            parked_mail_notice(
                surface=surface, parsed=parsed, owner=owner, member_id=member_id
            )
            if count <= limit
            else parked_mail_summary(surface=surface, owner=owner, member_id=member_id)
        )
        await tell_member(self.uow, pod_id=surface.pod_id, user_id=owner, text=note)
