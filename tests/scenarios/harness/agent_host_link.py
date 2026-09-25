"""A paired machine's side of the Agent Host link, for scenarios.

The host speaks to Lemma over one WebSocket, ``/agent-host/link``, and nothing
else (docs/architecture/agent-host.md#the-link). A websocket handshake is not an
httpx request, so this builds its own URL from the address the server is really
listening on, as the records-watching scenarios do.

Deliberately small: send a frame, read its answer, read a push, read the close.
What a frame means is the backend's business and is asserted by the scenarios.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import websockets

JSON = dict[str, Any]

#: The protocol this suite speaks. It is the backend's
#: ``AGENT_HOST_PROTOCOL_VERSION``, spelled out because the suite is its own uv
#: project and cannot import the backend.
PROTOCOL_VERSION = 3


def link_url(api) -> str:
    base = api.base_url.replace("https://", "wss://").replace("http://", "ws://")
    return f"{base}/agent-host/link"


class HostLink:
    def __init__(self, api, *, secret: str | None) -> None:
        self._url = link_url(api)
        self._headers = {"Authorization": f"Bearer {secret}"} if secret else {}
        self._socket: Any = None
        self._next_id = 0
        self.pushes: list[JSON] = []
        self.close_code: int | None = None

    async def __aenter__(self) -> HostLink:
        self._socket = await websockets.connect(
            self._url, additional_headers=self._headers
        )
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self._socket.close()

    async def send(self, frame_type: str, body: JSON | None = None) -> str:
        self._next_id += 1
        frame_id = str(self._next_id)
        await self._socket.send(
            json.dumps({"type": frame_type, "id": frame_id, "body": body or {}})
        )
        return frame_id

    async def _frame(self, timeout: float) -> JSON | None:
        try:
            raw = await asyncio.wait_for(self._socket.recv(), timeout=timeout)
        except websockets.exceptions.ConnectionClosed as closed:
            self.close_code = closed.rcvd.code if closed.rcvd else None
            return None
        return json.loads(raw)

    async def request(
        self, frame_type: str, body: JSON | None = None, *, timeout: float = 30.0
    ) -> JSON:
        frame_id = await self.send(frame_type, body)
        while True:
            frame = await self._frame(timeout)
            assert frame is not None, (
                f"the link closed ({self.close_code}) before answering {frame_type}"
            )
            if frame.get("re") == frame_id:
                return frame
            self.pushes.append(frame)

    async def closed(self, *, timeout: float = 30.0) -> int | None:
        while self.close_code is None:
            frame = await self._frame(timeout)
            if frame is None:
                break
            self.pushes.append(frame)
        return self.close_code


async def pair_machine(api, *, pairing_code: str, display_name: str, hello: JSON) -> JSON:
    """Spend a pairing code on the link; the answer carries the secret, once."""
    async with HostLink(api, secret=None) as link:
        paired = await link.request(
            "pair",
            {"pairing_code": pairing_code, "display_name": display_name, "hello": hello},
        )
        assert paired["type"] == "paired", paired
        return paired["body"]
