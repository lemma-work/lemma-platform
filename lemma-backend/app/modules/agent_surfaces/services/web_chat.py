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

from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.factory import create_authorization_data_service
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.contracts import (
    conversations_for_surfaces as agent_conversations,
)
from app.modules.agent.contracts.agents import agent_name_for_id
from app.modules.agent.contracts.contact_conversations import (
    ExportedMessage,
    visible_messages,
)
from app.modules.agent_surfaces.domain.web_widgets import (
    WebWidget,
    WidgetAnswer,
    refused,
)
from app.modules.agent_surfaces.services.outsider_limits import WebWidgetLimiter
from app.modules.agent_surfaces.services.visitor_access import Visitor
from app.modules.agent_surfaces.services.widget_directory import widget_by_key
from app.modules.contacts.contracts import contact_by_id
from app.modules.contacts.contracts.visitor_sessions import (
    VisitorSession,
    attach_visitor_conversation,
    touch_visitor_session,
    visitor_session,
)
from app.modules.datastore.contracts.public_rows import (
    OpenTable,
    PublicAudience,
    PublicRowRefused,
    PublicRowsClosed,
    add_visitor_row,
    visitor_table,
)
from app.modules.pod.contracts.members import pod_member_id, pod_name

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
        self, widget: WebWidget, visitor: Visitor, *, text: str
    ) -> None:
        message = text.strip()
        if not message:
            raise refused("Say something first", 400, "empty_message")
        if len(message) > MAX_MESSAGE_CHARS:
            raise refused(
                f"Keep it under {MAX_MESSAGE_CHARS} characters", 422, "too_long"
            )
        if widget.answer is WidgetAnswer.KNOWN and visitor.session.contact_id is None:
            raise refused("Verify your email to chat", 403, "not_a_contact")
        if not await self.limiter.allow_turn(
            widget_id=widget.id, session_id=visitor.session.session_id
        ):
            raise refused("Too many messages. Try again later.", 429, "rate_limited")
        owner = widget.looked_after_by
        async with self.uow_factory() as uow:
            if owner is None or await pod_member_id(uow, widget.pod_id, owner) is None:
                raise refused("Nobody is answering this chat", 503, "unattended")
            session = await self._visitor_session(uow, visitor)
            await self._start_visitor_turn(
                uow, widget, session, owner=owner, text=message
            )
            await touch_visitor_session(uow, session.id)
            await uow.commit()

    async def _start_visitor_turn(
        self,
        uow: SqlAlchemyUnitOfWork,
        widget: WebWidget,
        session: VisitorSession,
        *,
        owner: UUID,
        text: str,
    ) -> None:
        agent_name = await agent_name_for_id(uow.session, widget.agent_id)
        name = await self._display_name(uow, session)
        auth_ctx = await create_authorization_data_service(uow).build_user_context(
            user_id=owner, pod_id=widget.pod_id
        )
        context_token = set_current_context(auth_ctx)
        try:
            conversation_id = (
                session.conversation_id
                or await self._open_visitor_conversation(
                    uow, widget, session, owner=owner, agent_name=agent_name, name=name
                )
            )
            await agent_conversations.start_surface_turn(
                uow,
                conversation_id=conversation_id,
                user_id=owner,
                pod_id=widget.pod_id,
                content=text,
                agent_name=agent_name,
                message_metadata={
                    "source": "web_widget",
                    "sender_display_name": name or "Visitor",
                },
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
        *,
        owner: UUID,
        agent_name: str | None,
        name: str | None,
    ) -> UUID:
        conversation = await agent_conversations.open_surface_conversation(
            uow,
            pod_id=widget.pod_id,
            agent_name=agent_name,
            user_id=owner,
            title=f"{widget.name}: {name or 'visitor'}"[:255],
            metadata={
                "source": "web_widget",
                "web_widget_id": str(widget.id),
                "web_session_id": str(session.id),
            },
            for_outsiders=session.contact_id is None,
            for_contact=session.contact_id,
        )
        await attach_visitor_conversation(
            uow, session.id, conversation_id=conversation.id
        )
        return conversation.id

    async def visitor_history(
        self, visitor: Visitor, *, after: int
    ) -> tuple[ExportedMessage, ...]:
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
        contact_id = visitor.session.contact_id if visitor else None
        if widget.answer is WidgetAnswer.KNOWN and contact_id is None:
            raise refused("This is for existing customers only", 403, "not_a_contact")
        _opened, contacts_only = await self.visitor_table(widget, table=table)
        if contacts_only and contact_id is None:
            raise refused("Confirm your email first", 403, "needs_contact")
        if not await self.limiter.allow_submission(
            widget_id=widget.id, address=address
        ):
            raise refused("Too many answers. Try again later.", 429, "rate_limited")
        try:
            await add_visitor_row(
                self.uow_factory,
                pod_id=widget.pod_id,
                table_name=table,
                answers=answers,
                contact_id=contact_id,
                actor=(
                    f"visitor:{visitor.session.id}"
                    if visitor is not None and contact_id is None
                    else None
                ),
            )
        except PublicRowRefused as exc:
            raise refused(exc.message, 422, "bad_answer") from exc
        except PublicRowsClosed as exc:
            raise refused(
                "This isn't taking answers right now", 403, "table_closed"
            ) from exc
