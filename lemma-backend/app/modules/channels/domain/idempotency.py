"""The names clients give the lines and things they write, so a retry is the
first write and not a second.

A line is the same line however often it is sent when the client names it
(``client_message_id``, unique per channel). A member's name is kept in their
own namespace (``member_line_name``): two members who happen to pick one name
never name one line, and no member can pick a name the pod writes its own
lines by (``run:``, ``wf:``, ``channel-line:`` ...). The member is shown back
the name they sent (``client_name_of``), so they can match it to their line.

An ``Idempotency-Key`` is a name too: for a line, the client's name for it
when it sends no ``client_message_id``; elsewhere, hashed with its sender's id
(``keyed_client_id``).
"""

from __future__ import annotations

import hashlib
from uuid import UUID

#: The most a client's own name for a line may be.
MAX_CLIENT_NAME = 64

_MEMBER = "member"


def keyed_client_id(idempotency_key: str | None, *, user_id: UUID) -> str | None:
    """A name for this caller's key, nobody else's; ``None`` without a key."""
    if idempotency_key is None:
        return None
    return hashlib.sha256(f"{user_id}:{idempotency_key}".encode()).hexdigest()


def client_line_name(
    client_message_id: str | None, idempotency_key: str | None
) -> str | None:
    """What a member's client named their line: its ``client_message_id``,
    else its ``Idempotency-Key`` -- a long key by a digest of it, so the name
    fits. ``ValueError`` when both are sent and disagree."""
    if client_message_id is not None:
        if idempotency_key is not None and idempotency_key != client_message_id:
            raise ValueError(
                "client_message_id and Idempotency-Key name this line "
                "differently; send one, or the same in both"
            )
        return client_message_id
    if idempotency_key is None:
        return None
    if len(idempotency_key) <= MAX_CLIENT_NAME:
        return idempotency_key
    return "key:" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:56]


def member_line_name(client_name: str | None, *, user_id: UUID) -> str | None:
    """A member's name for their line, as it is kept: in their namespace."""
    if client_name is None:
        return None
    return f"{_MEMBER}:{user_id}:{client_name}"


def client_name_of(stored: str | None, *, user_id: UUID | None) -> str | None:
    """The name ``user_id``'s client gave a line kept as ``stored``; ``None``
    for a line named by anybody else, or by the pod."""
    if stored is None or user_id is None:
        return None
    prefix = f"{_MEMBER}:{user_id}:"
    return stored[len(prefix) :] if stored.startswith(prefix) else None
