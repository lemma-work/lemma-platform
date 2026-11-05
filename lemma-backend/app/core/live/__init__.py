"""The pod's live socket, ``/pods/{pod_id}/live``: the web app's one realtime
transport.

Core owns the transport -- authentication, the per-socket queue and loop,
limits, presence and the frame protocol -- and modules own what flows over it,
as topics, calls and streams registered through ``LemmaModule.live``
(``topics.py``). Hubs are woken by signals on the pod's Redis channel
(``signals.py``) and read what changed from where it is stored. The wire
protocol, for clients, is ``docs/modules/live.md``.
"""

from __future__ import annotations

from app.core.live.presence import PresenceCall
from app.core.live.topics import LiveContribution


def core_contribution() -> LiveContribution:
    """What core itself puts on the socket: who is online."""
    return LiveContribution(calls=(PresenceCall(),))


__all__ = ["core_contribution"]
