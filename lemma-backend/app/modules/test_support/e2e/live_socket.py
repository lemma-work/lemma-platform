"""A client of the pod's live socket for e2e tests, over a real websocket.

The tests run the app under uvicorn (``runtime.backend_server``) and talk to
it the way a browser does: one TCP websocket per client, frames read in the
order the server sent them. Writes go through the REST API or the modules'
contracts and reach the socket by the same signals and reads production uses.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from itertools import count

from websockets.asyncio.client import ClientConnection, connect

Frame = dict[str, object]


class LiveClient:
    """One socket: send ops and calls, read frames, keep what was read."""

    def __init__(self, ws: ClientConnection, ready: Frame) -> None:
        self.ws = ws
        self.ready = ready
        self.frames: list[Frame] = []
        self._ids = count(1)

    async def send(self, **frame: object) -> None:
        await self.ws.send(json.dumps(frame))

    async def recv(self, timeout: float = 15) -> Frame:
        frame = json.loads(await asyncio.wait_for(self.ws.recv(), timeout))
        self.frames.append(frame)
        return frame

    async def until(
        self, kind: str, of: str | None = None, timeout: float = 20
    ) -> Frame:
        """The next frame of type ``kind`` (for request ``of``), skipping others."""

        async def _next() -> Frame:
            while True:
                frame = await self.recv(timeout)
                if frame["type"] == kind and (of is None or frame.get("id") == of):
                    return frame

        return await asyncio.wait_for(_next(), timeout)

    async def event(
        self, sub: str, kind: str, *, timeout: float = 20, **match: object
    ) -> dict[str, object]:
        """The next event of type ``kind`` on subscription ``sub`` whose
        fields include ``match`` -- looking first at events already read."""
        for seen in self.events(sub, kind):
            if _matches(seen, match):
                return seen

        async def _next() -> dict[str, object]:
            while True:
                frame = await self.recv(timeout)
                if frame["type"] != "events" or frame.get("id") != sub:
                    continue
                for found in _events_of(frame, kind):
                    if _matches(found, match):
                        return found

        return await asyncio.wait_for(_next(), timeout)

    async def quiet(self, seconds: float = 1.5) -> list[Frame]:
        """Frames that arrive within ``seconds``; normally none."""
        got: list[Frame] = []
        try:
            async with asyncio.timeout(seconds):
                while True:
                    got.append(await self.recv(seconds))
        except TimeoutError:
            return got

    async def subscribe(self, sub: str, topic: str, **params: object) -> Frame:
        """Subscribe and return its answer: ``subscribed`` or ``error``."""
        await self.send(op="subscribe", id=sub, topic=topic, **params)

        async def _answer() -> Frame:
            while True:
                frame = await self.recv()
                if frame.get("id") == sub and frame["type"] in ("subscribed", "error"):
                    return frame

        return await asyncio.wait_for(_answer(), 20)

    async def call(self, method: str, **params: object) -> Frame:
        """Call ``method`` and return its ``result`` or ``error`` frame."""
        call_id = f"c{next(self._ids)}"
        await self.send(op="call", id=call_id, method=method, params=params)

        async def _answer() -> Frame:
            while True:
                frame = await self.recv()
                if frame.get("id") == call_id and frame["type"] in ("result", "error"):
                    return frame

        return await asyncio.wait_for(_answer(), 20)

    def events(self, sub: str, kind: str | None = None) -> list[dict[str, object]]:
        """Every event read so far on subscription ``sub``."""
        return [
            event
            for frame in self.frames
            if frame["type"] == "events" and frame.get("id") == sub
            for event in _events_of(frame, kind)
        ]

    def cursor(self, sub: str) -> str | None:
        """The newest cursor read for ``sub``."""
        cursors = [
            frame.get("cursor")
            for frame in self.frames
            if frame.get("id") == sub
            and frame["type"] in ("subscribed", "events", "resync")
        ]
        found = [c for c in cursors if isinstance(c, str)]
        return found[-1] if found else None


def _events_of(frame: Frame, kind: str | None) -> list[dict[str, object]]:
    events = frame.get("events")
    if not isinstance(events, list):
        return []
    return [e for e in events if isinstance(e, dict) and kind in (None, e.get("type"))]


def _matches(event: dict[str, object], match: dict[str, object]) -> bool:
    for key, wanted in match.items():
        value: object = event
        for part in key.split("__"):
            value = value.get(part) if isinstance(value, dict) else None
        if value != wanted:
            return False
    return True


def ws_url(server: dict[str, str], path: str) -> str:
    return server["host_base_url"].replace("http://", "ws://") + path


@asynccontextmanager
async def live_socket(
    server: dict[str, str], pod_id: str, token: str
) -> AsyncIterator[LiveClient]:
    """An open live socket on the pod, ``ready`` already read."""
    async with connect(
        ws_url(server, f"/pods/{pod_id}/live"),
        additional_headers={"Authorization": f"Bearer {token}"},
    ) as ws:
        ready = json.loads(await asyncio.wait_for(ws.recv(), 15))
        assert ready["type"] == "ready", ready
        yield LiveClient(ws, ready)


__all__ = ["Frame", "LiveClient", "live_socket", "ws_url"]
