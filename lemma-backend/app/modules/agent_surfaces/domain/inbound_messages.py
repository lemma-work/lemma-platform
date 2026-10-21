"""One inbound delivery, stored before anything acts on it.

A webhook or a receiver poll writes one row per delivery, unique on
``(receiver_scope, source_event_id)``: the unique conflict *is* the dedup, and
it lasts as long as the row -- 7 days once ROUTED or DROPPED (Meta retries for
up to 7), 30 once FAILED (``events/periodic_work.py``) -- so a platform
retrying within that is answered once.
The row then moves through ``surface.ingest``::

    RECEIVED -> INGESTING -> ROUTED     (a run started, or joined one)
                          -> DROPPED    (nothing to answer: a duplicate, a lifecycle event)
                          -> FAILED     (attempts exhausted; the sender is asked to resend)
"""

from __future__ import annotations

from enum import StrEnum


class InboundStatus(StrEnum):
    RECEIVED = "RECEIVED"
    INGESTING = "INGESTING"
    ROUTED = "ROUTED"
    DROPPED = "DROPPED"
    FAILED = "FAILED"

    @property
    def finished(self) -> bool:
        return self in _FINISHED


_FINISHED = frozenset(
    {InboundStatus.ROUTED, InboundStatus.DROPPED, InboundStatus.FAILED}
)

#: ``receiver_scope`` for the shared platform-level webhook endpoint.
SHARED_RECEIVER_SCOPE = "shared"


def part_source_event_id(source_event_id: str, index: int) -> str:
    """The id of part ``index`` of a delivery that carried several messages.

    The first part keeps the delivery's own id, so a single-message delivery --
    every platform but WhatsApp, almost always -- is keyed exactly as before.
    """
    return source_event_id if index == 0 else f"{source_event_id}#{index}"


__all__ = ["SHARED_RECEIVER_SCOPE", "InboundStatus", "part_source_event_id"]
