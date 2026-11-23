"""Telling an app that runs as a Lemma's assistant from the assistant itself.

An app framed inside an AI tool has no Lemma session cookie, so it is signed in
with the pod's own agent's standing (``agent/services/pod_mcp_apps``): its
context is the assistant's, as the assistant's own CLI calls are. What sets the
app's token apart is the delegation session it was minted under, which names
the AI tool's connection. A rule about the assistant *choosing* to do
something -- a function kept out of its reach -- asks ``is_the_assistant``,
not ``Context.is_assistant``, so the app's own flow is not refused with it.
"""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.context import Context

_APP_SESSION_PREFIX = "mcp:"


def app_session_id(grant_id: UUID) -> str:
    """The delegation session of an app framed through MCP grant ``grant_id``."""
    return f"{_APP_SESSION_PREFIX}{grant_id}"


def is_the_assistant(ctx: Context) -> bool:
    """The Lemma's assistant acting, and not an app signed in with its standing."""
    if not ctx.is_assistant:
        return False
    delegation = ctx.delegation
    session_id = delegation.session_id if delegation is not None else None
    return not (session_id or "").startswith(_APP_SESSION_PREFIX)


__all__ = ["app_session_id", "is_the_assistant"]
