"""A visitor's session: somebody outside a pod, reaching it through the web.

A session is what a browser holds between visits. Its secret is opaque, stored
only as a digest, and kept in the page's storage; it is exchanged for a
short-lived access token (``visitor-access``) that rides on every request. The
session says who the visitor is as strongly as anything has vouched for them:

``ANONYMOUS``
    Nobody has. Answered from what the pod made Public.
``CODE``
    They entered a code sent to an email address: a contact.
``HOST``
    The customer's own server signed a token naming them: a contact. The token
    lives ten minutes, so the session never outlives it on the secret alone --
    refreshing it takes a fresh host token, which is what stops a token copied
    out of a page minting a session that lives forever.

How long each lives is here because it is the rule, not a storage detail.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict

#: An anonymous session ends this long after it began, however often it is used.
ANONYMOUS_LIFETIME = timedelta(days=90)
#: A contact's session ends this long after it was last used.
CONTACT_LIFETIME = timedelta(days=30)


class VisitorStrength(StrEnum):
    ANONYMOUS = "ANONYMOUS"
    CODE = "CODE"
    HOST = "HOST"


class VisitorSession(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    pod_id: UUID
    widget_id: UUID
    contact_id: UUID | None
    strength: VisitorStrength
    conversation_id: UUID | None
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    revoked_at: datetime | None

    def is_live(self, now: datetime) -> bool:
        return self.revoked_at is None and self.expires_at > now

    @property
    def refreshes_from_secret(self) -> bool:
        """Whether the stored secret alone may mint a new access token."""
        return self.strength is not VisitorStrength.HOST


def expiry_on_use(session: VisitorSession, now: datetime) -> datetime:
    """When a session that was just used should end.

    A contact's session is renewed by use; an anonymous one keeps the end it
    was given, so a visitor nobody knows cannot hold a session open forever by
    visiting once a month.
    """
    if session.strength is VisitorStrength.ANONYMOUS:
        return session.expires_at
    return now + CONTACT_LIFETIME


def first_expiry(strength: VisitorStrength, now: datetime) -> datetime:
    if strength is VisitorStrength.ANONYMOUS:
        return now + ANONYMOUS_LIFETIME
    return now + CONTACT_LIFETIME
