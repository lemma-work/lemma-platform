"""A pod's web widgets: its chat on web pages it does not own.

See ``infrastructure/web_widget_models`` for what is stored and
``services/web_chat`` for what a visitor can do. The public key is not a
credential: anybody can copy it off the page, so whatever it allows, the
internet gets -- starting an anonymous chat, and adding rows to whichever of
the pod's tables are open to visitors (see ``datastore/contracts/public_rows``).
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime
from enum import StrEnum
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.core.domain.errors import DomainError

PUBLIC_KEY_PREFIX = "pk_"
SECRET_PREFIX = "sk_"


class WidgetAnswer(StrEnum):
    """Whom a widget answers. Mirrors a bot's ``contacts.answer``."""

    #: Nobody: the widget is switched off.
    OFF = "off"
    #: Only visitors who are already the pod's contacts, identified by a host
    #: token or a code. Anonymous visitors are refused.
    KNOWN = "known"
    #: Anybody: anonymous visitors chat as outsiders, and become contacts when
    #: a host token or a code identifies them.
    ANYONE = "anyone"


class WebWidget(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    pod_id: UUID
    agent_id: UUID
    name: str
    public_key: str
    allowed_origins: tuple[str, ...]
    answer: WidgetAnswer
    looked_after_by: UUID | None
    created_at: datetime

    def allows_origin(self, origin: str | None) -> bool:
        """Whether a browser on ``origin`` may read this widget's answers.

        No origins configured means any page may embed it. The check stops
        another site embedding the widget, not a script calling it, so it is a
        courtesy and never a reason to trust a request.
        """
        if not self.allowed_origins:
            return True
        return bool(origin) and normalize_origin(origin or "") in self.allowed_origins


class WebChatRefused(DomainError):
    """What a visitor is told when the widget will not do what they asked.

    ``code`` is the short word a page acts on (``no_session``, ``bad_token``,
    ``needs_contact``...), carried in the API's usual error envelope.
    """

    def __init__(self, message: str, *, status_code: int, code: str) -> None:
        super().__init__(message, code=code, status_code=status_code)


def refused(message: str, status_code: int, code: str) -> WebChatRefused:
    return WebChatRefused(message, status_code=status_code, code=code)


def normalize_origin(origin: str) -> str:
    return origin.strip().rstrip("/").lower()


#: The only pages a widget may name over plain HTTP: the developer's own machine.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1"})


def parse_origin(value: str) -> str:
    """``value`` as an origin a widget may name, or ``ValueError`` saying why.

    A scheme and a host, with a port if it has one, and nothing else: a path or
    a query would never match the ``Origin`` a browser sends, so a member who
    typed one would be refused on their own site without knowing why. HTTPS,
    except for exactly ``localhost`` and ``127.0.0.1``.
    """
    text = value.strip()
    parsed = urlsplit(text)
    try:
        port = parsed.port
    except ValueError:
        raise ValueError(f"That port isn't a number: {text}") from None
    host = (parsed.hostname or "").lower()
    if not parsed.scheme or not host:
        raise ValueError(f"Origins look like https://example.com: {text}")
    if parsed.username or parsed.password:
        raise ValueError(f"Origins carry no user name: {text}")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError(f"Origins have no path or query: {text}")
    scheme = parsed.scheme.lower()
    if scheme != "https" and not (scheme == "http" and host in _LOOPBACK_HOSTS):
        raise ValueError(f"Origins are https:// addresses: {text}")
    return f"{scheme}://{host}" + (f":{port}" if port is not None else "")


def mint_public_key() -> str:
    return PUBLIC_KEY_PREFIX + secrets.token_urlsafe(24)


def mint_secret() -> str:
    return SECRET_PREFIX + secrets.token_urlsafe(32)


def mint_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def digest(value: str) -> str:
    """How a code is stored: never in the clear.

    A six-digit code is guessable from its digest, which is why codes expire in
    minutes, allow few attempts, and are salted with the session they belong to
    (see callers).
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
