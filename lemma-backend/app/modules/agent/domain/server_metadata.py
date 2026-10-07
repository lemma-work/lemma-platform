"""The conversation metadata only the server may write.

Two keys decide what a run in a conversation may do, and both are written once,
by the server, when the conversation is opened:

* ``audience`` -- whether it answers people outside the pod (``outsiders``);
* ``ask`` -- which conversation in another pod its answer is owed to
  (``pod_asks``).

A client may write any other key. These two it can neither set nor drop.
"""

from __future__ import annotations

from app.modules.agent.domain.outsiders import with_audience_kept, without_audience
from app.modules.agent.domain.pod_asks import with_ask_kept, without_ask


def client_metadata(metadata: dict[str, object] | None) -> dict[str, object] | None:
    """Metadata a client sent for a new conversation, minus the server's keys."""
    return without_ask(without_audience(metadata))


def with_server_keys_kept(
    existing: dict[str, object] | None, incoming: dict[str, object] | None
) -> dict[str, object] | None:
    """``incoming`` metadata replacing ``existing``, with the server's keys kept."""
    return with_ask_kept(existing, with_audience_kept(existing, incoming))
