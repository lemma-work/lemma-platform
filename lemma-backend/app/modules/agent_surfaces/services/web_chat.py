"""What a visitor holding a widget's public key can do.

Start a session; send a message and read the answers; prove an email address
with a one-time code; add a row to a table the pod opened to visitors. Nothing
else -- and what a row may contain is the table's to decide, never the page's.

A visitor is anonymous -- an outsider, answered from what the pod made Public --
until something vouches for them: a token the customer's server signed with
the widget's secret (``HOST``), or a code they entered (``CODE``). Then they are
a contact, and a conversation they already started becomes theirs in place.

Every step that reaches Redis, the mail service or a function runs with no
unit of work open, so a slow dependency never holds a pooled connection.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt

from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.factory import create_authorization_data_service
from app.core.domain.errors import DomainError
from app.core.email.email_sender import EmailNotConfiguredError, EmailSender
from app.core.email.transactional import render_transactional_email
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.contracts import (
    conversations_for_surfaces as agent_conversations,
)
from app.modules.agent.contracts.agents import agent_name_for_id
from app.modules.agent.contracts.contact_conversations import (
    ExportedMessage,
    mark_conversation_contact,
    visible_messages,
)
from app.modules.agent_surfaces.domain.web_widgets import (
    WebSession,
    WebWidget,
    WidgetAnswer,
    digest,
    mint_code,
    mint_session_token,
)
from app.modules.agent_surfaces.infrastructure.repositories.web_widget_repository import (  # noqa: E501
    WebWidgetRepository,
)
from app.modules.agent_surfaces.services.outsider_limits import WebWidgetLimiter
from app.modules.contacts.contracts import (
    ContactRef,
    IdentityKind,
    IdentityStrength,
    find_contact,
    open_contact,
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

logger = get_logger(__name__)

#: The longest a host token may live: a token copied out of a page should be
#: worthless minutes later.
HOST_TOKEN_MAX_SECONDS = 600
_LEEWAY_SECONDS = 30

MAX_MESSAGE_CHARS = 4000
MAX_HISTORY = 100
CODE_TTL = timedelta(minutes=10)
MAX_CODE_ATTEMPTS = 5

_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,63}$")


class WebChatRefused(DomainError):
    def __init__(self, message: str, *, status_code: int, code: str) -> None:
        super().__init__(message, code=code, status_code=status_code)


def _refused(message: str, status_code: int, code: str) -> WebChatRefused:
    return WebChatRefused(message, status_code=status_code, code=code)


@dataclass(frozen=True, slots=True)
class HostClaims:
    subject: str
    name: str | None


@dataclass(frozen=True, slots=True)
class StartedSession:
    token: str
    is_contact: bool
    display_name: str | None


def verify_host_token(token: str, *, secret: str, public_key: str) -> HostClaims | None:
    """Who the customer's server says this visitor is, or ``None``.

    HS256 under the widget's secret, addressed to its public key, with a subject
    and an expiry no more than ten minutes away. Anything else is refused.
    """
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience=public_key,
            leeway=_LEEWAY_SECONDS,
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError:
        return None
    if float(claims["exp"]) - time.time() > HOST_TOKEN_MAX_SECONDS + _LEEWAY_SECONDS:
        return None
    subject = str(claims["sub"]).strip()[:200]
    name = claims.get("name")
    return (
        HostClaims(
            subject=subject,
            name=str(name).strip()[:255]
            if isinstance(name, str) and name.strip()
            else None,
        )
        if subject
        else None
    )


class WebChat:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        limiter: WebWidgetLimiter | None = None,
        email_sender: Callable[[], EmailSender] = EmailSender.from_settings,
    ) -> None:
        self.uow_factory = uow_factory
        self.limiter = limiter or WebWidgetLimiter()
        self.email_sender = email_sender

    async def widget_for_key(self, public_key: str) -> WebWidget:
        async with self.uow_factory() as uow:
            widget = await WebWidgetRepository(uow.session).by_public_key(public_key)
        if widget is None or widget.answer is WidgetAnswer.OFF:
            raise _refused("This widget is not available", 404, "widget_not_found")
        return widget

    # -- sessions -----------------------------------------------------------

    async def start_visitor_session(
        self, widget: WebWidget, *, host_token: str | None, address: str
    ) -> StartedSession:
        if not await self.limiter.allow_session(widget_id=widget.id, address=address):
            raise _refused("Too many chats. Try again later.", 429, "rate_limited")
        claims: HostClaims | None = None
        if host_token:
            async with self.uow_factory() as uow:
                secret = await WebWidgetRepository(uow.session).signing_secret(
                    widget.id
                )
            claims = verify_host_token(
                host_token, secret=secret or "", public_key=widget.public_key
            )
            if claims is None:
                raise _refused(
                    "The sign-in token was not accepted", 401, "bad_host_token"
                )
        token = mint_session_token()
        async with self.uow_factory() as uow:
            contact = (
                await self._visitor_contact(
                    uow,
                    widget,
                    IdentityKind.HOST,
                    f"{widget.id}:{claims.subject}",
                    IdentityStrength.HOST,
                    claims.name,
                )
                if claims is not None
                else None
            )
            await WebWidgetRepository(uow.session).open_session(
                widget_id=widget.id,
                token=token,
                contact_id=contact.id if contact else None,
                contact_strength=IdentityStrength.HOST.value if contact else None,
                display_name=(contact.display_name if contact else None),
            )
            await uow.commit()
        return StartedSession(
            token=token,
            is_contact=contact is not None,
            display_name=contact.display_name if contact else None,
        )

    async def _visitor_session(self, widget: WebWidget, token: str) -> WebSession:
        async with self.uow_factory() as uow:
            session = await WebWidgetRepository(uow.session).session_by_token(
                widget_id=widget.id, token=token
            )
        if session is None:
            raise _refused("This chat has ended. Start a new one.", 401, "no_session")
        return session

    async def _visitor_contact(
        self,
        uow: SqlAlchemyUnitOfWork,
        widget: WebWidget,
        kind: IdentityKind,
        value: str,
        strength: IdentityStrength,
        display_name: str | None,
    ) -> ContactRef:
        """The contact this handle names, created only where the widget answers anyone."""
        known = await find_contact(uow, pod_id=widget.pod_id, kind=kind, value=value)
        if known is not None:
            return known
        if widget.answer is not WidgetAnswer.ANYONE:
            raise _refused(
                "This chat is for existing customers only", 403, "not_a_contact"
            )
        return await open_contact(
            uow,
            pod_id=widget.pod_id,
            kind=kind,
            value=value,
            strength=strength,
            display_name=display_name,
        )

    # -- conversation -------------------------------------------------------

    async def send_visitor_message(
        self, widget: WebWidget, *, token: str, text: str
    ) -> None:
        message = text.strip()[:MAX_MESSAGE_CHARS]
        if not message:
            raise _refused("Say something first", 400, "empty_message")
        session = await self._visitor_session(widget, token)
        if widget.answer is WidgetAnswer.KNOWN and session.contact_id is None:
            raise _refused("Verify your email to chat", 403, "not_a_contact")
        if not await self.limiter.allow_turn(
            widget_id=widget.id, session_id=session.id
        ):
            raise _refused("Too many messages. Try again later.", 429, "rate_limited")
        owner = widget.looked_after_by
        async with self.uow_factory() as uow:
            if owner is None or await pod_member_id(uow, widget.pod_id, owner) is None:
                raise _refused("Nobody is answering this chat", 503, "unattended")
            agent_name = await agent_name_for_id(uow.session, widget.agent_id)
            auth_ctx = await create_authorization_data_service(uow).build_user_context(
                user_id=owner, pod_id=widget.pod_id
            )
            context_token = set_current_context(auth_ctx)
            try:
                conversation_id = (
                    session.conversation_id
                    or await self._open_visitor_conversation(
                        uow, widget, session, owner=owner, agent_name=agent_name
                    )
                )
                await agent_conversations.start_surface_turn(
                    uow,
                    conversation_id=conversation_id,
                    user_id=owner,
                    pod_id=widget.pod_id,
                    content=message,
                    agent_name=agent_name,
                    message_metadata={
                        "source": "web_widget",
                        "sender_display_name": session.display_name or "Visitor",
                    },
                )
            finally:
                reset_current_context(context_token)
            await WebWidgetRepository(uow.session).touch(session.id)
            await uow.commit()

    async def _open_visitor_conversation(
        self,
        uow: SqlAlchemyUnitOfWork,
        widget: WebWidget,
        session: WebSession,
        *,
        owner: UUID,
        agent_name: str | None,
    ) -> UUID:
        conversation = await agent_conversations.open_surface_conversation(
            uow,
            pod_id=widget.pod_id,
            agent_name=agent_name,
            user_id=owner,
            title=f"{widget.name}: {session.display_name or 'visitor'}"[:255],
            metadata={
                "source": "web_widget",
                "web_widget_id": str(widget.id),
                "web_session_id": str(session.id),
            },
            for_outsiders=session.contact_id is None,
            for_contact=session.contact_id,
        )
        await WebWidgetRepository(uow.session).attach_conversation(
            session_id=session.id, conversation_id=conversation.id
        )
        return conversation.id

    async def visitor_history(
        self, widget: WebWidget, *, token: str, after: int
    ) -> tuple[ExportedMessage, ...]:
        session = await self._visitor_session(widget, token)
        if session.conversation_id is None:
            return ()
        async with self.uow_factory() as uow:
            return await visible_messages(
                uow, session.conversation_id, after=after, limit=MAX_HISTORY
            )

    async def visitor_conversation(
        self, widget: WebWidget, *, token: str
    ) -> UUID | None:
        """The conversation this visitor's session writes to, once there is one."""
        session = await self._visitor_session(widget, token)
        return session.conversation_id

    async def widget_title(self, widget: WebWidget) -> str:
        """What the chat calls itself at the top: the space's name."""
        async with self.uow_factory() as uow:
            return await pod_name(uow.session, widget.pod_id) or widget.name

    # -- codes --------------------------------------------------------------

    async def send_visitor_code(
        self, widget: WebWidget, *, token: str, email: str, address: str
    ) -> None:
        email = email.strip().lower()
        if not _EMAIL.match(email):
            raise _refused("That doesn't look like an email address", 400, "bad_email")
        session = await self._visitor_session(widget, token)
        if not await self.limiter.allow_code(
            widget_id=widget.id, email=email, address=address
        ):
            raise _refused("Too many codes. Try again later.", 429, "rate_limited")
        try:
            sender = self.email_sender()
        except EmailNotConfiguredError as exc:
            raise _refused(
                "Email is not available here", 503, "email_unavailable"
            ) from exc
        code = mint_code()
        async with self.uow_factory() as uow:
            await WebWidgetRepository(uow.session).add_code(
                session_id=session.id,
                email=email,
                code_hash=digest(f"{session.id}:{code}"),
                expires_at=datetime.now(timezone.utc) + CODE_TTL,
            )
            await uow.commit()
        rendered = render_transactional_email(
            preheader=f"Your code is {code}",
            eyebrow=widget.name,
            heading=f"Your code is {code}",
            body=[
                (
                    "Enter it in the chat to confirm this is your email address. "
                    "It works for ten minutes."
                ),
                "If you did not ask for it, you can ignore this email.",
            ],
        )
        await sender.send_email(
            email, f"Your code is {code}", rendered.html, rendered.text
        )

    async def verify_visitor_code(
        self, widget: WebWidget, *, token: str, email: str, code: str
    ) -> StartedSession:
        email = email.strip().lower()
        session = await self._visitor_session(widget, token)
        async with self.uow_factory() as uow:
            repository = WebWidgetRepository(uow.session)
            live = await repository.live_code(session_id=session.id, email=email)
            if live is None or live.attempts >= MAX_CODE_ATTEMPTS:
                raise _refused(
                    "That code has expired. Ask for a new one.", 400, "bad_code"
                )
            if digest(f"{session.id}:{code.strip()}") != live.code_hash:
                live.attempts += 1
                await uow.commit()
                raise _refused("That code is not right", 400, "bad_code")
            live.consumed_at = datetime.now(timezone.utc)
            contact = await self._visitor_contact(
                uow,
                widget,
                IdentityKind.EMAIL,
                email,
                IdentityStrength.CODE,
                session.display_name,
            )
            await repository.identify(
                session_id=session.id,
                contact_id=contact.id,
                strength=IdentityStrength.CODE.value,
            )
            if session.conversation_id is not None:
                await mark_conversation_contact(
                    uow, conversation_id=session.conversation_id, contact_id=contact.id
                )
            await uow.commit()
        return StartedSession(
            token=token, is_contact=True, display_name=contact.display_name
        )

    # -- rows -------------------------------------------------------------

    async def visitor_table(
        self, widget: WebWidget, *, token: str | None, table: str
    ) -> tuple[OpenTable, bool]:
        """What a page may ask for on ``table``, and whether this visitor may send.

        A table open to contacts only is still described to a stranger, so the
        page can ask them to confirm their email before they answer.
        """
        if token:
            await self._visitor_session(widget, token)
        async with self.uow_factory() as uow:
            opened = await visitor_table(uow, pod_id=widget.pod_id, table_name=table)
        if opened is None or not opened.columns:
            raise _refused("There is nothing to fill in here", 404, "table_closed")
        return opened, opened.audience is PublicAudience.CONTACTS

    async def add_visitor_row(
        self,
        widget: WebWidget,
        *,
        token: str | None,
        table: str,
        answers: dict[str, object],
        address: str,
    ) -> None:
        session = await self._visitor_session(widget, token) if token else None
        if widget.answer is WidgetAnswer.KNOWN and (
            session is None or session.contact_id is None
        ):
            raise _refused("This is for existing customers only", 403, "not_a_contact")
        contact_id = session.contact_id if session else None
        _opened, contacts_only = await self.visitor_table(
            widget, token=None, table=table
        )
        if contacts_only and contact_id is None:
            raise _refused("Confirm your email first", 403, "needs_contact")
        if not await self.limiter.allow_submission(
            widget_id=widget.id, address=address
        ):
            raise _refused("Too many answers. Try again later.", 429, "rate_limited")
        try:
            await add_visitor_row(
                self.uow_factory,
                pod_id=widget.pod_id,
                table_name=table,
                answers=answers,
                contact_id=contact_id,
            )
        except PublicRowRefused as exc:
            raise _refused(exc.message, 422, "bad_answer") from exc
        except PublicRowsClosed as exc:
            raise _refused(
                "This isn't taking answers right now", 403, "table_closed"
            ) from exc
