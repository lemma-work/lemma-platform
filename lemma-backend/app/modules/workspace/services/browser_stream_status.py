"""What the screen's toolbar shows, read from the sandbox's own relay.

Which transport this person gets, at what density, the display's size and who
holds it, who else is watching, and whether the agent is driving. Asked only
once the browser is known to be running, and each part degrades to "unknown"
on an older image rather than failing the status the pane renders.
"""

from __future__ import annotations

import asyncio
from typing import TypedDict
from uuid import UUID

import httpx

from app.modules.workspace.services.browser_relay_client import (
    BrowserRelayClient,
    BrowserRelayUnavailable,
)
from app.modules.workspace.services.browser_stream import transport_for

#: How recently an agent command counts as "driving". The agent issues a
#: command every few seconds while it works; a person reading the page between
#: two of them should still see the badge.
AGENT_DRIVING_SECONDS = 8.0


class Watcher(TypedDict):
    viewer: str
    mode: str
    transport: str


class StreamDetails(TypedDict, total=False):
    transport: str
    density: int
    size: str | None
    presets: dict[str, str]
    lease: str | None
    watching: list[Watcher]
    agent_driving: bool


async def stream_details(relay: BrowserRelayClient, user_id: UUID) -> StreamDetails:
    """Everything above, each part as far as the relay can say."""
    health, display = await asyncio.gather(
        _json(relay, "/health"), _json(relay, "/display")
    )
    active = health.get("agent_active_seconds")
    density = display.get("density")
    details: StreamDetails = {
        "transport": transport_for(user_id),
        # The relay's word, which is what Chrome is running at: the server
        # only allows density 2 (`display.json`), the machine decides.
        "density": density if density in (1, 2) else 1,
        "agent_driving": isinstance(active, (int, float))
        and active < AGENT_DRIVING_SECONDS,
        "watching": _watchers(health.get("watching")),
    }
    size = display.get("size")
    details["size"] = size if isinstance(size, str) else None
    lease = display.get("lease")
    details["lease"] = lease if isinstance(lease, str) else None
    presets = display.get("presets")
    if isinstance(presets, dict):
        details["presets"] = {str(k): str(v) for k, v in presets.items()}
    return details


async def _json(relay: BrowserRelayClient, path: str) -> dict[str, object]:
    """A relay answer as a dict, or an empty one if it has none to give.

    The relay's JSON is untyped until each field is checked by the caller.
    """
    try:
        response = await relay.request("GET", path, timeout=10.0)
    except BrowserRelayUnavailable, httpx.HTTPError:
        return {}
    if response.status_code != 200:
        return {}
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _watchers(raw: object) -> list[Watcher]:
    if not isinstance(raw, list):
        return []
    found: list[Watcher] = []
    for entry in raw:
        if isinstance(entry, dict) and all(
            isinstance(entry.get(key), str) for key in ("viewer", "mode", "transport")
        ):
            found.append(
                Watcher(
                    viewer=entry["viewer"],
                    mode=entry["mode"],
                    transport=entry["transport"],
                )
            )
    return found


__all__ = ["AGENT_DRIVING_SECONDS", "StreamDetails", "Watcher", "stream_details"]
