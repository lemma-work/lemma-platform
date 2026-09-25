"""The Agent Host links this process is serving, so a shutdown can drain them.

A process-local set of live session objects, not a cache: nothing here is data
another replica could want, and no replica needs to know which one holds a
host's socket. Commands reach a host through its notice channel and the command
queue, both of which any replica can serve.

Draining is the one thing only this process can do for its own sockets: tell
each host when to come back, spread over a few seconds, so a deploy does not
reconnect every host in the same instant.
"""

from __future__ import annotations

import asyncio

from app.core.log.log import get_logger
from app.modules.agent.services.agent_host_link_session import AgentHostLinkSession


logger = get_logger(__name__)

#: How long a drain waits for its sockets to finish closing.
DRAIN_TIMEOUT_SECONDS = 5.0


class AgentHostLinkRegistry:
    def __init__(self) -> None:
        self._sessions: set[AgentHostLinkSession] = set()

    def add(self, session: AgentHostLinkSession) -> None:
        self._sessions.add(session)

    def discard(self, session: AgentHostLinkSession) -> None:
        self._sessions.discard(session)

    def __len__(self) -> int:
        return len(self._sessions)

    async def drain(self, *, timeout: float = DRAIN_TIMEOUT_SECONDS) -> None:
        """Send every live link ``reconnect`` and close it with 1012.

        Under uvicorn this usually finds nothing to do: the server closes its
        open WebSockets with 1012 itself before the application's lifespan is
        told to shut down, so the host sees the close without ``reconnect`` and
        picks its own delay. It matters wherever the lifespan ends first -- and
        it is the only place a ``reconnect`` can come from at all.
        """
        sessions = list(self._sessions)
        if not sessions:
            return
        for session in sessions:
            await session.drain_link()
        _, pending = await asyncio.wait(
            [asyncio.ensure_future(session.finished()) for session in sessions],
            timeout=timeout,
        )
        for waiter in pending:
            waiter.cancel()
        logger.info(
            "agent.agent_host_link.drained",
            link_count=len(sessions),
            still_open=len(pending),
        )


link_registry = AgentHostLinkRegistry()
