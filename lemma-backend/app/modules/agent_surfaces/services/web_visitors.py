"""Who a web visitor is: their session, and what makes them a contact.

A page starts a session with nothing (an anonymous visitor, after a
proof-of-work when bot protection is on), with a host token (a signed-in user
of the customer's product), or with the secret it kept from last time. Each
answer is a fifteen-minute access token; the secret comes back only when it is
new, because only then does the page need to store it.

A visitor becomes a contact by a host token or by entering a code sent to an
email address. Either way the session gets a new secret, and a conversation
they already started becomes theirs in place. A session is one person's: once
it names a contact it never names another, and somebody else signing in on the
same page gets a session -- and a conversation -- of their own.

Every step that reaches Redis, the mail service or the proof-of-work store runs
with no unit of work open, so a slow dependency never holds a pooled
connection.
"""

from __future__ import annotations

import hmac
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt

from app.core.crypto.factory import get_secret_signer
from app.core.email.email_sender import EmailNotConfiguredError, EmailSender
from app.core.email.transactional import render_transactional_email
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.contracts.contact_conversations import (
    mark_conversation_contact,
)
from app.modules.agent_surfaces.domain.web_widgets import (
    WebWidget,
    WidgetAnswer,
    digest,
    mint_code,
    refused,
)
from app.modules.agent_surfaces.services.outsider_limits import WebWidgetLimiter
from app.modules.agent_surfaces.services.visitor_access import (
    ACCESS_TOKEN_SECONDS,
    Visitor,
    mint_visitor_access,
)
from app.modules.agent_surfaces.infrastructure.repositories.web_widget_repository import (  # noqa: E501
    WebWidgetRepository,
)
from app.modules.contacts.contracts import (
    ContactRef,
    IdentityKind,
    IdentityStrength,
    contact_by_id,
    find_contact,
    open_contact,
)
from app.modules.contacts.contracts.visitor_sessions import (
    VisitorSession,
    VisitorStrength,
    add_visitor_code,
    consume_visitor_code,
    identify_visitor_session,
    open_visitor_session,
    renew_visitor_session,
    spend_code_attempt,
    visitor_session,
    visitor_session_by_secret,
)
from app.modules.identity.contracts.altcha import (
    AltchaRejected,
    issue_challenge,
    verify_proof,
)
from app.modules.pod.contracts.members import pod_name, pod_organization_id

logger = get_logger(__name__)

#: The longest a host token may live: a token copied out of a page should be
#: worthless minutes later.
HOST_TOKEN_MAX_SECONDS = 600
_LEEWAY_SECONDS = 30
#: The longest user id a host token may name. Refused rather than cut short:
#: two ids sharing their first 200 characters must not become one contact.
MAX_HOST_SUBJECT = 200

CODE_TTL = timedelta(minutes=10)
MAX_CODE_ATTEMPTS = 5

#: What a proof-of-work is for, so a proof solved for one cannot open the other.
SESSION_PROOF = "web-session"
CODE_PROOF = "web-code"

_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,63}$")
#: What a sender name may contain: a widget's name is a member's free text,
#: and it goes into a mail header.
_NAME_UNSAFE = re.compile(r"[^\w .\'&()-]+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class HostClaims:
    subject: str
    name: str | None


@dataclass(frozen=True, slots=True)
class StartedSession:
    access_token: str
    #: Only when it is new: the page stores it to come back.
    secret: str | None
    is_contact: bool
    display_name: str | None
    expires_in: int


def host_contact_key(subject: str) -> str:
    """How a customer's user is known in a pod, through any of its widgets."""
    return f"host:{subject}"


def verify_host_token(token: str, *, secret: str, public_key: str) -> HostClaims | None:
    """Who the customer's server says this visitor is, or ``None``.

    HS256 under the widget's secret, addressed to its public key, with a subject
    of at most 200 characters and an expiry no more than ten minutes away.
    Anything else is refused.
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
    subject = str(claims["sub"]).strip()
    if not subject or len(subject) > MAX_HOST_SUBJECT:
        return None
    name = claims.get("name")
    return HostClaims(
        subject=subject,
        name=str(name).strip()[:255]
        if isinstance(name, str) and name.strip()
        else None,
    )


def address_hash(address: str) -> str:
    """A browser's address as a session keeps it: keyed, so not reversible by
    hashing every IPv4 address."""
    return get_secret_signer().sign("visitor-ip", address.encode()).split(".", 1)[1]


def sender_name(widget_name: str) -> str:
    """Who a code email says it is from: the widget, via Lemma."""
    cleaned = " ".join(_NAME_UNSAFE.sub(" ", widget_name).split())[:60]
    return f"{cleaned} via Lemma" if cleaned else "Lemma"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class WebVisitors:
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

    # -- proof-of-work ------------------------------------------------------

    async def visitor_challenge(self, *, for_code: bool) -> Mapping[str, object]:
        """What a page solves before it starts an anonymous session, or before
        it asks for a code: ``{"enabled": false}`` when protection is off."""
        try:
            return await issue_challenge(CODE_PROOF if for_code else SESSION_PROOF)
        except RuntimeError as exc:
            raise refused(
                "The security check is unavailable. Try again shortly.",
                503,
                "altcha_unavailable",
            ) from exc

    @staticmethod
    async def _prove_human(payload: str | None, *, purpose: str) -> None:
        try:
            await verify_proof(payload, purpose=purpose)
        except AltchaRejected as exc:
            raise refused(
                "The security check didn't pass. Try again.", 400, "altcha_failed"
            ) from exc

    # -- sessions -----------------------------------------------------------

    async def start_visitor_session(
        self,
        widget: WebWidget,
        *,
        secret: str | None,
        host_token: str | None,
        altcha: str | None,
        address: str,
    ) -> StartedSession:
        """Refresh the session ``secret`` opens, or start a new one.

        A secret that opens nothing live is ``no_session`` unless a host token
        came with it, which starts a session of its own.
        """
        claims = await self._host_claims(widget, host_token) if host_token else None
        if secret:
            resumed = await self._resume_session(widget, secret, claims)
            if resumed is not None:
                return resumed
            if claims is None:
                raise refused(
                    "This chat has ended. Start a new one.", 401, "no_session"
                )
        return await self._open_session(widget, claims, altcha=altcha, address=address)

    async def _host_claims(self, widget: WebWidget, host_token: str) -> HostClaims:
        async with self.uow_factory() as uow:
            signing = await WebWidgetRepository(uow.session).signing_secret(widget.id)
        claims = verify_host_token(
            host_token, secret=signing or "", public_key=widget.public_key
        )
        if claims is None:
            raise refused("The sign-in token was not accepted", 401, "bad_host_token")
        return claims

    async def _resume_session(
        self, widget: WebWidget, secret: str, claims: HostClaims | None
    ) -> StartedSession | None:
        async with self.uow_factory() as uow:
            session = await visitor_session_by_secret(
                uow, widget_id=widget.id, secret=secret
            )
            if session is None or not session.is_live(_now()):
                return None
            if claims is None:
                if not session.refreshes_from_secret:
                    raise refused(
                        "Sign in again to keep chatting.", 401, "host_token_required"
                    )
                started = await self._renewed_session(uow, widget, session)
            else:
                started = await self._session_with_host(uow, widget, session, claims)
            await uow.commit()
        return started

    async def _renewed_session(
        self, uow: SqlAlchemyUnitOfWork, widget: WebWidget, session: VisitorSession
    ) -> StartedSession:
        await renew_visitor_session(uow, session)
        return await self._issue_access(uow, widget, session, secret=None)

    async def _session_with_host(
        self,
        uow: SqlAlchemyUnitOfWork,
        widget: WebWidget,
        session: VisitorSession,
        claims: HostClaims,
    ) -> StartedSession | None:
        """The session, with a host token presented: renewed, upgraded, or
        ``None`` when it is somebody else's."""
        contact = await self._visitor_contact(
            uow,
            widget,
            IdentityKind.HOST,
            host_contact_key(claims.subject),
            IdentityStrength.HOST,
            claims.name,
        )
        if session.contact_id == contact.id:
            return await self._renewed_session(uow, widget, session)
        if session.contact_id is not None:
            return None
        return await self._become_contact(
            uow, widget, session, contact=contact, strength=VisitorStrength.HOST
        )

    async def _open_session(
        self,
        widget: WebWidget,
        claims: HostClaims | None,
        *,
        altcha: str | None,
        address: str,
    ) -> StartedSession:
        if claims is None:
            await self._prove_human(altcha, purpose=SESSION_PROOF)
        if not await self.limiter.allow_session(widget_id=widget.id, address=address):
            raise refused("Too many chats. Try again later.", 429, "rate_limited")
        async with self.uow_factory() as uow:
            contact = (
                await self._visitor_contact(
                    uow,
                    widget,
                    IdentityKind.HOST,
                    host_contact_key(claims.subject),
                    IdentityStrength.HOST,
                    claims.name,
                )
                if claims is not None
                else None
            )
            session, secret = await open_visitor_session(
                uow,
                pod_id=widget.pod_id,
                widget_id=widget.id,
                contact_id=contact.id if contact else None,
                strength=VisitorStrength.HOST if contact else VisitorStrength.ANONYMOUS,
                ip_hash=address_hash(address),
            )
            started = await self._issue_access(uow, widget, session, secret=secret)
            await uow.commit()
        return started

    async def _become_contact(
        self,
        uow: SqlAlchemyUnitOfWork,
        widget: WebWidget,
        session: VisitorSession,
        *,
        contact: ContactRef,
        strength: VisitorStrength,
    ) -> StartedSession:
        """Name the contact on the session and its conversation, under a new secret."""
        secret = await identify_visitor_session(
            uow, session, contact_id=contact.id, strength=strength
        )
        if session.conversation_id is not None:
            await mark_conversation_contact(
                uow, conversation_id=session.conversation_id, contact_id=contact.id
            )
        upgraded = await visitor_session(uow, session.id)
        if upgraded is None:
            raise refused("This chat has ended. Start a new one.", 401, "no_session")
        return await self._issue_access(uow, widget, upgraded, secret=secret)

    async def _issue_access(
        self,
        uow: SqlAlchemyUnitOfWork,
        widget: WebWidget,
        session: VisitorSession,
        *,
        secret: str | None,
    ) -> StartedSession:
        contact = (
            await contact_by_id(
                uow, pod_id=widget.pod_id, contact_id=session.contact_id
            )
            if session.contact_id
            else None
        )
        organization_id = await pod_organization_id(uow, widget.pod_id)
        return StartedSession(
            access_token=mint_visitor_access(session, organization_id=organization_id),
            secret=secret,
            is_contact=contact is not None,
            display_name=contact.display_name if contact else None,
            expires_in=ACCESS_TOKEN_SECONDS,
        )

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
            raise refused(
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

    # -- codes --------------------------------------------------------------

    @staticmethod
    def _one_identity(visitor: Visitor) -> None:
        if visitor.session.contact_id is not None:
            raise refused(
                "You're already confirmed in this chat. Start a new chat to use "
                "another address.",
                409,
                "already_a_contact",
            )

    async def send_visitor_code(
        self,
        widget: WebWidget,
        visitor: Visitor,
        *,
        email: str,
        altcha: str | None,
        address: str,
    ) -> None:
        email = email.strip().lower()
        if not _EMAIL.match(email):
            raise refused("That doesn't look like an email address", 400, "bad_email")
        self._one_identity(visitor)
        await self._prove_human(altcha, purpose=CODE_PROOF)
        if not await self.limiter.allow_code(
            widget_id=widget.id, email=email, address=address
        ):
            raise refused("Too many codes. Try again later.", 429, "rate_limited")
        try:
            sender = self.email_sender()
        except EmailNotConfiguredError as exc:
            raise refused(
                "Email is not available here", 503, "email_unavailable"
            ) from exc
        code = mint_code()
        session_id = visitor.session.session_id
        async with self.uow_factory() as uow:
            await add_visitor_code(
                uow,
                session_id=session_id,
                email=email,
                code_hash=digest(f"{session_id}:{code}"),
                expires_at=_now() + CODE_TTL,
            )
            space = await pod_name(uow.session, widget.pod_id) or widget.name
            await uow.commit()
        sender.from_name = sender_name(widget.name)
        rendered = render_transactional_email(
            preheader=f"Your code is {code}",
            eyebrow=widget.name,
            heading=f"Your code is {code}",
            body=[
                (
                    "Enter it in the chat to confirm this is your email address. "
                    "It works for ten minutes."
                ),
                f"Lemma sent this on behalf of {space}, whose chat asked for it.",
                "If you did not ask for it, you can ignore this email.",
            ],
        )
        await sender.send_email(
            email, f"Your code is {code}", rendered.html, rendered.text
        )

    async def verify_visitor_code(
        self,
        widget: WebWidget,
        visitor: Visitor,
        *,
        email: str,
        code: str,
        address: str,
    ) -> StartedSession:
        email = email.strip().lower()
        self._one_identity(visitor)
        session_id = visitor.session.session_id
        if not await self.limiter.allow_verify(
            session_id=session_id, email=email, address=address
        ):
            raise refused("Too many tries. Try again later.", 429, "rate_limited")
        # Counted and committed before the comparison, so a guess is spent
        # whether or not it was right and whatever happens after it.
        async with self.uow_factory() as uow:
            spent = await spend_code_attempt(
                uow, session_id=session_id, email=email, max_attempts=MAX_CODE_ATTEMPTS
            )
            await uow.commit()
        if spent is None:
            raise refused("That code has expired. Ask for a new one.", 400, "bad_code")
        code_id, code_hash = spent
        if not hmac.compare_digest(digest(f"{session_id}:{code.strip()}"), code_hash):
            raise refused("That code is not right", 400, "bad_code")
        async with self.uow_factory() as uow:
            started = await self._spend_code(uow, widget, visitor, code_id, email)
            await uow.commit()
        return started

    async def _spend_code(
        self,
        uow: SqlAlchemyUnitOfWork,
        widget: WebWidget,
        visitor: Visitor,
        code_id: UUID,
        email: str,
    ) -> StartedSession:
        session = await visitor_session(uow, visitor.session.session_id)
        if session is None or not await consume_visitor_code(uow, code_id):
            raise refused("That code has expired. Ask for a new one.", 400, "bad_code")
        if session.contact_id is not None:
            # Confirmed by another request since this one's token was minted.
            raise refused(
                "You're already confirmed in this chat.", 409, "already_a_contact"
            )
        contact = await self._visitor_contact(
            uow, widget, IdentityKind.EMAIL, email, IdentityStrength.CODE, None
        )
        return await self._become_contact(
            uow, widget, session, contact=contact, strength=VisitorStrength.CODE
        )
