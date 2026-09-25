"""The Agent Host's one route: the link WebSocket.

A paired machine used to call five HTTP routes here -- a 25-second long poll,
one POST per streamed event batch, harness publication, self-revocation and
pairing -- plus a conversation MCP mount for its agents' Lemma tools. All of it
travels on this socket now; see docs/architecture/agent-host.md#the-link.

The route does no authentication of its own. The global session gate exempts
``/agent-host/`` (a machine has no user session and never will), and the first
frame on the socket is the credential: a pairing code, or ``hello`` under the
host secret in ``Authorization``. The session checks it.
"""

from __future__ import annotations

from fastapi import APIRouter, WebSocket

from app.core.infrastructure.channels.channel_service import get_channel_service
from app.modules.agent.domain.agent_host_link import AGENT_HOST_LINK_PATH
from app.modules.agent.services.agent_host_link_mcp import AgentHostLinkMcp
from app.modules.agent.services.agent_host_link_registry import link_registry
from app.modules.agent.services.agent_host_link_session import AgentHostLinkSession
from app.modules.agent.services.agent_host_link_store import AgentHostLinkStore
from app.modules.agent.services.conversation_mcp_service import (
    conversation_mcp_service,
)


router = APIRouter(tags=["agent_host"])

_store = AgentHostLinkStore()


@router.websocket(AGENT_HOST_LINK_PATH)
async def agent_host_link(websocket: WebSocket) -> None:
    channels = await get_channel_service()
    session = AgentHostLinkSession(
        websocket,
        store=_store,
        mcp=AgentHostLinkMcp(conversation_mcp_service, channels),
        channels=channels,
        registry=link_registry,
    )
    await session.serve_link()
