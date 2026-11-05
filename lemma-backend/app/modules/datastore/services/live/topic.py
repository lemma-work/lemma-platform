"""The ``records`` topic: a pod's table rows on its live socket.

What the topic adds to the hub is the socket's side of a subscription: the
frame's parameters parsed (``domain.live.records_request``) and handed to the
pod's hub in this process, whose start failing is the socket's "unavailable".
"""

from __future__ import annotations

from app.core.live.connection import LiveConnection
from app.core.live.protocol import LiveUnavailable, SubscribeRequest
from app.modules.datastore.domain.live import LiveCursorExpired, records_request
from app.modules.datastore.services.live import live_hubs


class RecordsTopic:
    name = "records"

    async def subscribe(
        self, connection: LiveConnection, request: SubscribeRequest
    ) -> None:
        parsed = records_request(request)
        hub = await live_hubs().hub(connection.pod_id)
        try:
            await hub.subscribe(connection, parsed)
        except LiveCursorExpired as expired:
            # The hub could not even find where its log begins.
            raise LiveUnavailable("the pod's change log is not readable") from expired


__all__ = ["RecordsTopic"]
