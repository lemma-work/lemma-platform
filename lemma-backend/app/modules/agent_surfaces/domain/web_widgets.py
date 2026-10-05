"""A pod's web widgets: chat bubbles and forms on web pages it does not own.

See ``infrastructure/web_widget_models`` for what is stored and
``services/web_chat`` for what a visitor can do. The public key is not a
credential: anybody can copy it off the page, so whatever it allows, the
internet gets -- starting an anonymous chat, and submitting the widget's form.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.modules.agent_surfaces.domain.web_forms import FormSpec

PUBLIC_KEY_PREFIX = "pk_"
SECRET_PREFIX = "sk_"


class WidgetKind(StrEnum):
    CHAT = "chat"
    FORM = "form"


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
    kind: WidgetKind
    public_key: str
    allowed_origins: tuple[str, ...]
    answer: WidgetAnswer
    looked_after_by: UUID | None
    form_function: str | None
    form_requires_code: bool
    form: FormSpec | None = None
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


class WebSession(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    widget_id: UUID
    conversation_id: UUID | None
    contact_id: UUID | None
    contact_strength: str | None
    display_name: str | None


def normalize_origin(origin: str) -> str:
    return origin.strip().rstrip("/").lower()


def mint_public_key() -> str:
    return PUBLIC_KEY_PREFIX + secrets.token_urlsafe(24)


def mint_secret() -> str:
    return SECRET_PREFIX + secrets.token_urlsafe(32)


def mint_session_token() -> str:
    return secrets.token_urlsafe(32)


def mint_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def digest(value: str) -> str:
    """How a session token or a code is stored: never in the clear.

    A plain SHA-256 is enough for a 256-bit session token. A six-digit code is
    guessable from its digest, which is why codes expire in minutes, allow few
    attempts, and are salted with the session they belong to (see callers).
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
