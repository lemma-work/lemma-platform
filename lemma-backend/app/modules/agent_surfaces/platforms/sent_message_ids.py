"""Which platform message ids a stretch of sending produced.

A delivery status arrives later and names only the platform's own message id
(WhatsApp's ``wamid``), so whoever wants to answer "that reply never reached
them" has to have written down which ids a delivery produced. The ids are known
deep inside a platform client and wanted several layers up, where the
conversation is known -- and threading a return value through every send verb
in between would change every signature on the way for one caller's sake.

So the caller opens a collector around the sends it cares about, and the client
records into whichever collector is open. Outside one, recording is a no-op:
nothing is kept for a send nobody asked about.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_collector: ContextVar[list[str] | None] = ContextVar(
    "agent_surfaces_sent_message_ids", default=None
)


@contextmanager
def collect_sent_message_ids() -> Iterator[list[str]]:
    """Collect the ids of every message sent inside the block, in order.

    Nested collectors each see their own sends; the outer one does not see the
    inner one's, because the inner block is a separate question.
    """
    collected: list[str] = []
    token = _collector.set(collected)
    try:
        yield collected
    finally:
        _collector.reset(token)


def record_sent_message_id(message_id: str) -> None:
    """Note a sent message's id, if anyone is collecting. Otherwise nothing."""
    collected = _collector.get()
    if collected is not None and message_id:
        collected.append(message_id)
