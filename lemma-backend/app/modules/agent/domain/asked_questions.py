"""Questions one conversation puts to members (``channel_post`` with
``wants_answer``): how they are named.

A question's ref is ``msg:{asking conversation}:{key}`` (``-`` for none) and
every question one run asks shares a batch -- the run -- so the asking
conversation is woken once: when the run has finished and the last of its
batch has closed.
"""

from __future__ import annotations

from hashlib import sha256
from uuid import UUID

#: What a message's question ``ref`` starts with.
MESSAGE_REF = "msg"


def message_ref(conversation_id: UUID | None, key: str) -> str:
    return f"{MESSAGE_REF}:{conversation_id or '-'}:{key}"


#: The longest client id a channel line takes.
MAX_CLIENT_ID = 64


def line_client_id(key: str) -> str:
    """A line's client id from a key of any length: the key itself when it
    fits, else a digest of it -- the same key is always the same line."""
    if len(key) <= MAX_CLIENT_ID:
        return key
    return "h:" + sha256(key.encode()).hexdigest()[: MAX_CLIENT_ID - 2]


def batch_of(run_id: UUID | None) -> str | None:
    """The batch a run's questions share."""
    return batch_of_run(run_id) if run_id is not None else None


def batch_of_run(run_id: UUID) -> str:
    return str(run_id)


def run_of_batch(batch_id: str) -> UUID | None:
    """The run that asked a batch."""
    try:
        return UUID(batch_id)
    except ValueError:
        return None


__all__ = [
    "MESSAGE_REF",
    "batch_of",
    "batch_of_run",
    "line_client_id",
    "message_ref",
    "run_of_batch",
]
