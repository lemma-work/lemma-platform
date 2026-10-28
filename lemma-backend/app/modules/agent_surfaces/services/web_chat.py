"""What a visitor holding a widget's public key can do.

Send a message and read the answers; add a row to a table the pod opened to
visitors. Nothing else -- and what a row may contain is the table's to decide,
never the page's. Who the visitor is, and how they become a contact, is
``web_visitors``; by the time a request reaches here ``PublicVisitorDep`` has
checked their access token and the session behind it.

Every step that reaches Redis, the mail service or a function runs with no
unit of work open, so a slow dependency never holds a pooled connection.
"""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.anonymous import build_anonymous_context
from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.factory import create_authorization_data_service
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.contracts import channels as agent_conversations
from app.modules.agent.contracts import channels as agent_channels
from app.modules.agent.contracts.agents import agent_name_for_id
from app.modules.agent.contracts.channel_replies import (
    CLIENT_NONCE_KEY,
    VisibleMessage,
    visible_messages,
)
from app.modules.agent_surfaces.infrastructure.repositories.conversation_surface_repository import (
    ConversationSurfaces,
)
from app.modules.agent_surfaces.domain.web_widgets import (
    WebWidget,
    WidgetAnswer,
    refused,
)
from app.modules.agent_surfaces.services.channel_lines import (
    note_heard_on_web,
    web_place,
)
from app.modules.agent_surfaces.services.outside_cap import (
    first_word_today,
    hand_to_a_person,
    held_for_a_person,
    organization_capped,
)
from app.modules.agent_surfaces.services.contact_keepers import answers
from app.modules.agent_surfaces.services.outsider_limits import WebWidgetLimiter
from app.modules.agent_surfaces.services.visitor_access import Visitor
from app.modules.agent_surfaces.services.widget_directory import widget_by_key
from app.modules.channels.contracts import ContactsHome, Source, source_for
from app.modules.contacts.contracts import contact_by_id, open_anonymous_contact
from app.modules.contacts.contracts.visitor_sessions import (
    VisitorSession,
    attach_visitor_conversation,
    touch_visitor_session,
    visitor_session,
)
from app.modules.datastore.contracts.public_tables import (
    PublicOrderRefused,
    PublicRows,
    PublicTableClosed,
    read_public_table,
)
from app.modules.datastore.contracts.public_rows import (
    OpenTable,
    PublicAudience,
    PublicRowRefused,
    PublicRowsClosed,
    add_visitor_row,
    visitor_table,
)
from app.modules.pod.contracts.members import (
    pod_member_id,
    pod_name,
    pod_organization_id,
)

#: The longest message a visitor may send. Longer is refused, never cut: a
#: message cut short says something its writer did not.
MAX_MESSAGE_CHARS = 4000
MAX_HISTORY = 100


class WebChat:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        limiter: WebWidgetLimiter | None = None,
    ) -> None:
        self.uow_factory = uow_factory
        self.limiter = limiter or WebWidgetLimiter()

    async def widget_for_key(self, public_key: str) -> WebWidget:
        widget = await widget_by_key(public_key, uow_factory=self.uow_factory)
        if widget is None or widget.answer is WidgetAnswer.OFF:
            raise refused("This widget is not available", 404, "widget_not_found")
        return widget

    async def widget_title(self, widget: WebWidget) -> str:
        """What the chat calls itself at the top: the space's name."""
        async with self.uow_factory() as uow:
            return await pod_name(uow.session, widget.pod_id) or widget.name

    async def _visitor_session(
        self, uow: SqlAlchemyUnitOfWork, visitor: Visitor
    ) -> VisitorSession:
        session = await visitor_session(uow, visitor.session.session_id)
        if session is None:
            raise refused("This chat has ended. Start a new one.", 401, "no_session")
        return session

    # -- conversation -------------------------------------------------------

    async def send_visitor_message(
        self,
        widget: WebWidget,
        visitor: Visitor,
        *,
        text: str,
        client_nonce: str | None = None,
    ) -> None:
        message = text.strip()
        if not message:
            raise refused("Say something first", 400, "empty_message")
        if len(message) > MAX_MESSAGE_CHARS:
            raise refused(
                f"Keep it under {MAX_MESSAGE_CHARS} characters", 422, "too_long"
            )
        if widget.answer is WidgetAnswer.KNOWN and not visitor.session.is_vouched:
            raise refused("Verify your email to chat", 403, "not_a_contact")
        if not await self.limiter.allow_turn(
            widget_id=widget.id, session_id=visitor.session.session_id
        ):
            raise refused("Too many messages. Try again later.", 429, "rate_limited")
        owner = widget.looked_after_by
        capped = await organization_capped(self.uow_factory, pod_id=widget.pod_id)
        async with self.uow_factory() as uow:
            if owner is None or await pod_member_id(uow, widget.pod_id, owner) is None:
                raise refused("Nobody is answering this chat", 503, "unattended")
            session = await self._visitor_session(uow, visitor)
            conversation_id, contact_id = await self._visitor_conversation(
                uow, widget, session, owner=owner
            )
            await note_heard_on_web(
                uow,
                pod_id=widget.pod_id,
                widget_id=widget.id,
                session_id=session.id,
                contact_id=contact_id,
                conversation_id=conversation_id,
                text=message,
                display_name=await self._display_name(uow, session),
            )
            held = await held_for_a_person(
                uow,
                pod_id=widget.pod_id,
                conversation_id=conversation_id,
                capped=capped,
            )
            if not held:
                await self._start_visitor_turn(
                    uow,
                    widget,
                    session,
                    conversation_id,
                    owner=owner,
                    text=message,
                    client_nonce=client_nonce,
                )
            await touch_visitor_session(uow, session.id)
            await uow.commit()
        if held:
            await self._hold_for_a_person(
                widget,
                session,
                conversation_id,
                owner=owner,
                text=message,
                client_nonce=client_nonce,
            )

    async def _hold_for_a_person(
        self,
        widget: WebWidget,
        session: VisitorSession,
        conversation_id: UUID,
        *,
        owner: UUID,
        text: str,
        client_nonce: str | None,
    ) -> None:
        """Keep the message for the member, and tell the visitor once a day.

        A member has the conversation, or the organization's contacts cap is
        reached: no run starts. The visitor reads the "a person will reply"
        line in the chat itself, so nothing else has to send it.
        """
        tell = await first_word_today(conversation_id)
        async with self.uow_factory() as uow:
            await hand_to_a_person(
                uow,
                pod_id=widget.pod_id,
                conversation_id=conversation_id,
                owner_id=owner,
                text=text,
                metadata=_visitor_metadata(
                    await self._display_name(uow, session), client_nonce
                ),
                tell=tell,
            )
            await uow.commit()

    async def _visitor_conversation(
        self,
        uow: SqlAlchemyUnitOfWork,
        widget: WebWidget,
        session: VisitorSession,
        *,
        owner: UUID,
    ) -> tuple[UUID, UUID]:
        """The visitor's conversation, found or opened on their first word, and
        their contact.

        The contact's one conversation with the widget's agent, in their
        channel -- the one they may already have from another session, or from
        a chat on another platform -- kept by the member looking after the
        widget. Opened under the same lock, and in the same order, as an
        inbound message opens one (``ConversationBinder``): authorized first,
        since that commits, then locked, looked for again, and opened.

        The session's own conversation is continued while it is in the
        channel. One that lost its seat when this contact was merged into
        another is not: the survivor's conversation there is the one to
        continue, and the session's is placed again only when there is none.
        """
        contact_id = await _visitor_contact_id(uow, session)
        kept = session.conversation_id
        if kept is not None and not await _session_answers(uow, kept, contact_id):
            kept = None
        if (
            kept is not None
            and await agent_channels.channel_of_conversation(uow, kept) is not None
        ):
            return kept, contact_id
        place = await source_for(
            uow,
            pod_id=widget.pod_id,
            place=web_place(widget_id=widget.id, session_id=session.id),
            home=ContactsHome(contact_id=contact_id, keeper_user_id=owner),
        )
        found = await self._contacts_conversation(
            uow, widget, place, owner=owner, contact_id=contact_id
        )
        if found is None:
            found = await self._open_visitor_conversation(
                uow,
                widget,
                session,
                place,
                owner=owner,
                contact_id=contact_id,
                kept=kept,
            )
        await attach_visitor_conversation(uow, session.id, conversation_id=found)
        return found, contact_id

    @staticmethod
    async def _contacts_conversation(
        uow: SqlAlchemyUnitOfWork,
        widget: WebWidget,
        place: Source,
        *,
        owner: UUID,
        contact_id: UUID,
    ) -> UUID | None:
        return await agent_channels.conversation_in_channel(
            uow,
            channel_id=place.channel_id,
            pod_id=widget.pod_id,
            agent_id=widget.agent_id,
            user_id=owner,
            contact_id=contact_id,
        )

    async def _start_visitor_turn(
        self,
        uow: SqlAlchemyUnitOfWork,
        widget: WebWidget,
        session: VisitorSession,
        conversation_id: UUID,
        *,
        owner: UUID,
        text: str,
        client_nonce: str | None,
    ) -> None:
        agent_name = await agent_name_for_id(uow.session, widget.agent_id)
        metadata = _visitor_metadata(
            await self._display_name(uow, session), client_nonce
        )
        auth_ctx = await create_authorization_data_service(uow).build_user_context(
            user_id=owner, pod_id=widget.pod_id
        )
        context_token = set_current_context(auth_ctx)
        try:
            await agent_conversations.start_surface_turn(
                uow,
                conversation_id=conversation_id,
                user_id=owner,
                pod_id=widget.pod_id,
                content=text,
                agent_name=agent_name,
                message_metadata=metadata,
            )
        finally:
            reset_current_context(context_token)

    @staticmethod
    async def _display_name(
        uow: SqlAlchemyUnitOfWork, session: VisitorSession
    ) -> str | None:
        if session.contact_id is None:
            return None
        contact = await contact_by_id(
            uow, pod_id=session.pod_id, contact_id=session.contact_id
        )
        return contact.display_name if contact else None

    async def _open_visitor_conversation(
        self,
        uow: SqlAlchemyUnitOfWork,
        widget: WebWidget,
        session: VisitorSession,
        place: Source,
        *,
        owner: UUID,
        contact_id: UUID,
        kept: UUID | None,
    ) -> UUID:
        agent_name = await agent_name_for_id(uow.session, widget.agent_id)
        name = await self._display_name(uow, session)
        context_token = set_current_context(
            await create_authorization_data_service(uow).build_user_context(
                user_id=owner, pod_id=widget.pod_id
            )
        )
        try:
            await agent_conversations.authorize_surface_conversation(
                uow, pod_id=widget.pod_id, agent_name=agent_name, user_id=owner
            )
            await agent_channels.lock_person_in_channel(
                uow,
                channel_id=place.channel_id,
                pod_id=widget.pod_id,
                agent_id=widget.agent_id,
                person_id=contact_id,
            )
            found = await self._contacts_conversation(
                uow, widget, place, owner=owner, contact_id=contact_id
            )
            if found is not None:
                return found
            if kept is not None:
                await agent_channels.place_in_channel(
                    uow, conversation_id=kept, channel_id=place.channel_id
                )
                return kept
            conversation = await agent_conversations.open_surface_conversation(
                uow,
                pod_id=widget.pod_id,
                agent_name=agent_name,
                user_id=owner,
                title=f"{widget.name}: {name or 'visitor'}"[:255],
                contact_id=contact_id,
                channel_id=place.channel_id,
                require_execute_grant=False,
                started_on="web_widget",
            )
            await ConversationSurfaces(uow).record_web_visitor(
                conversation.id, widget_id=widget.id, session_id=session.id
            )
            return conversation.id
        finally:
            reset_current_context(context_token)

    async def visitor_history(
        self, visitor: Visitor, *, after: int
    ) -> tuple[VisibleMessage, ...]:
        async with self.uow_factory() as uow:
            session = await self._visitor_session(uow, visitor)
            if session.conversation_id is None:
                return ()
            return await visible_messages(
                uow, session.conversation_id, after=after, limit=MAX_HISTORY
            )

    async def visitor_conversation(self, visitor: Visitor) -> UUID | None:
        """The conversation this visitor's session writes to, once there is one."""
        async with self.uow_factory() as uow:
            return (await self._visitor_session(uow, visitor)).conversation_id

    # -- rows -------------------------------------------------------------

    async def visitor_table(
        self, widget: WebWidget, *, table: str
    ) -> tuple[OpenTable, bool]:
        """What a page may ask for on ``table``, and whether only contacts may send.

        A table open to contacts only is still described to a stranger, so the
        page can ask them to confirm their email before they answer.
        """
        async with self.uow_factory() as uow:
            opened = await visitor_table(uow, pod_id=widget.pod_id, table_name=table)
        if opened is None or not opened.columns:
            raise refused("There is nothing to fill in here", 404, "table_closed")
        return opened, opened.audience is PublicAudience.CONTACTS

    async def add_visitor_row(
        self,
        widget: WebWidget,
        visitor: Visitor | None,
        *,
        table: str,
        answers: dict[str, object],
        address: str,
    ) -> None:
        vouched = visitor is not None and visitor.session.is_vouched
        if widget.answer is WidgetAnswer.KNOWN and not vouched:
            raise refused("This is for existing customers only", 403, "not_a_contact")
        _opened, contacts_only = await self.visitor_table(widget, table=table)
        if contacts_only and not vouched:
            raise refused("Confirm your email first", 403, "needs_contact")
        if not await self.limiter.allow_submission(
            widget_id=widget.id, address=address
        ):
            raise refused("Too many answers. Try again later.", 429, "rate_limited")
        contact_id = await self._writer(visitor) if visitor is not None else None
        try:
            await add_visitor_row(
                self.uow_factory,
                pod_id=widget.pod_id,
                table_name=table,
                answers=answers,
                contact_id=contact_id,
                vouched=vouched,
                actor=f"contact:{contact_id}" if contact_id is not None else None,
            )
        except PublicRowRefused as exc:
            raise refused(exc.message, 422, "bad_answer") from exc
        except PublicRowsClosed as exc:
            raise refused(
                "This isn't taking answers right now", 403, "table_closed"
            ) from exc

    async def _writer(self, visitor: Visitor) -> UUID:
        """The contact a visitor writing a row is, opened if this is their first write."""
        async with self.uow_factory() as uow:
            contact_id = await _visitor_contact_id(
                uow, await self._visitor_session(uow, visitor)
            )
            await uow.commit()
        return contact_id

    async def public_rows(
        self,
        widget: WebWidget,
        visitor: Visitor | None,
        *,
        table: str,
        address: str,
        order_by: str | None,
        descending: bool,
    ) -> PublicRows:
        """Every row of a table the pod marked Public, as this visitor reads it.

        Read with the visitor's own outsider context -- the one the pod's chat
        answers them with -- or, for a page that started no chat, an anonymous
        one pinned to the widget's pod. Either reads Public and nothing else, and
        anything else is said the same way: nothing to read here.
        """
        if not await self.limiter.allow_read(widget_id=widget.id, address=address):
            raise refused("Too many requests. Try again later.", 429, "rate_limited")
        async with self.uow_factory() as uow:
            ctx = (
                visitor.context
                if visitor is not None
                else build_anonymous_context(
                    session=uow.session,
                    pod_id=widget.pod_id,
                    organization_id=await pod_organization_id(uow, widget.pod_id),
                    actor_id=f"web:{widget.id}",
                )
            )
            token = set_current_context(ctx)
            try:
                return await read_public_table(
                    uow,
                    ctx=ctx,
                    table_name=table,
                    order_by=order_by,
                    descending=descending,
                )
            except PublicTableClosed as exc:
                raise refused(
                    "There is nothing to read here", 404, "table_closed"
                ) from exc
            except PublicOrderRefused as exc:
                raise refused("The table has no such column", 422, "bad_order") from exc
            finally:
                reset_current_context(token)


async def _visitor_contact_id(
    uow: SqlAlchemyUnitOfWork, session: VisitorSession
) -> UUID:
    """The contact this session's browser is, opened on the first thing it writes.

    Never on a read: a page that only shows what the pod made Public opens
    nobody.
    """
    if session.contact_id is not None:
        return session.contact_id
    contact = await open_anonymous_contact(
        uow, pod_id=session.pod_id, session_id=session.id
    )
    return contact.id


def _visitor_metadata(name: str | None, client_nonce: str | None) -> dict[str, object]:
    """What a visitor's message carries: who sent it, and the page's own name
    for its copy, handed back in history so the page draws the message once."""
    metadata: dict[str, object] = {
        "source": "web_widget",
        "sender_display_name": name or "Visitor",
    }
    if client_nonce:
        metadata[CLIENT_NONCE_KEY] = client_nonce
    return metadata


async def _session_answers(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID, contact_id: UUID
) -> bool:
    """Whether the session's conversation answers its visitor's contact.

    One that names nobody -- or another person -- is not continued: its runs
    would act with whatever authority it answers, not as this visitor. One
    naming the contact the visitor was merged from is theirs: the merge's
    work moves it over shortly.
    """
    conversation = await agent_conversations.surface_conversation(uow, conversation_id)
    return conversation is not None and await answers(uow, conversation, contact_id)
