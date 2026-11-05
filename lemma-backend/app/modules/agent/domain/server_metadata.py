"""The conversation metadata only the server may write.

One key is written once, by the server, and decides what may happen in a
conversation: ``ask`` -- which conversation in another pod its answer is owed
to (``pod_asks``). A client may write any other key. This one it can neither
set nor drop.

Whom a conversation answers is not metadata at all: it is the conversation's
``contact_id`` column, which no client request reaches.
"""

from __future__ import annotations

from app.modules.agent.domain.pod_asks import with_ask_kept, without_ask


def client_metadata(metadata: dict[str, object] | None) -> dict[str, object] | None:
    """Metadata a client sent for a new conversation, minus the server's keys."""
    return without_ask(metadata)


def with_server_keys_kept(
    existing: dict[str, object] | None, incoming: dict[str, object] | None
) -> dict[str, object] | None:
    """``incoming`` metadata replacing ``existing``, with the server's keys kept."""
    return with_ask_kept(existing, incoming)
