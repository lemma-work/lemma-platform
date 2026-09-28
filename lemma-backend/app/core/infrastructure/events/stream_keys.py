"""Redis keys the stream guard writes and the publish path reads.

Separate from ``stream_guard`` so the message bus can read them without
importing the guard, which imports the bus's own dependencies.
"""

from __future__ import annotations

#: Set while Redis is above the critical ratio; read by the outbox dispatcher.
#: Expires on its own, so a guard that stops running cannot leave publishing
#: paused forever.
MEMORY_PRESSURE_KEY = "lemma:redis:memory-pressure"

#: Per-stream entry cap applied on publish; see ``stream_budget.publish_entry_cap``.
STREAM_CAP_KEY_PREFIX = "lemma:stream-cap:"


def stream_cap_key(stream: str) -> str:
    return f"{STREAM_CAP_KEY_PREFIX}{stream}"
