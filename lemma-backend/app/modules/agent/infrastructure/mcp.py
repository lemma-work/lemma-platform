"""Shared MCP constants and helpers for Lemma agent tools."""

from __future__ import annotations

LEMMA_MCP_SERVER_NAME = "lemma_tools"
LEMMA_TOOL_PREFIX = "lemma_"
LEMMA_MCP_TOKEN_ENV = "LEMMA_MCP_TOKEN"
LEMMA_MCP_AUTHORIZATION_ENV = "LEMMA_MCP_AUTHORIZATION"

# Server names whose tools are ours. One name is the contract: every run
# publishes `LEMMA_MCP_SERVER_NAME`, and every path — pod agent, local agent
# over ACP — registers the server under it. The others are names Lemma itself
# shipped before that was true (a hyphenated MCP config, and bare `lemma` from
# Agent Host builds that named the server locally instead of using the run's).
# They still sit in stored conversations, so reading them keeps working;
# nothing writes them any more. Longest first, so `lemma_tools` is recognised
# as itself rather than as `lemma` followed by `_tools`.
_SERVER_NAMES = tuple(
    sorted((LEMMA_MCP_SERVER_NAME, "lemma-tools", "lemma"), key=len, reverse=True)
)

# An agent namespaces an MCP tool with the server it came from, and does it in
# whatever shape it likes: `mcp__lemma_tools__lemma_exec_command`,
# `lemma_tools.lemma_exec_command`. The namespace carries no meaning of its own
# — it is there so one agent's `exec_command` cannot collide with another's —
# so recovering Lemma's name is: drop the MCP marker, drop our server name,
# drop whatever separator joined them.
_MCP_MARKERS = ("mcp__", "mcp.", "mcp/")
# No `-`: `lemma-tools` is a server name in its own right above, and treating
# `-` as a separator would read someone else's `lemma-corp` server as ours.
_NAMESPACE_SEPARATORS = "_./:"


def strip_provider_namespace(tool_name: str) -> str:
    """Drop the MCP namespace an agent added, if the server named is ours.

    Returns the name unchanged when it is not — a third-party
    ``mcp__github__create_issue`` is not a Lemma tool and must not be renamed
    into one.
    """
    candidate = tool_name
    for marker in _MCP_MARKERS:
        if candidate.startswith(marker):
            candidate = candidate[len(marker) :]
            break
    for name in _SERVER_NAMES:
        if not candidate.startswith(name):
            continue
        remainder = candidate[len(name) :]
        without_separator = remainder.lstrip(_NAMESPACE_SEPARATORS)
        # A separator has to follow the server name, or `lemmatize` would read
        # as the `lemma` server's `tize`.
        if without_separator != remainder:
            return without_separator
    return tool_name


def exported_tool_name(tool_name: str) -> str:
    return f"{LEMMA_TOOL_PREFIX}{tool_name}"


def normalize_exported_tool_name(tool_name: str) -> str:
    return (
        tool_name[len(LEMMA_TOOL_PREFIX) :]
        if tool_name.startswith(LEMMA_TOOL_PREFIX)
        else tool_name
    )


def normalize_local_mcp_tool_name(tool_name: str) -> str:
    """The canonical Lemma name for a tool, however an agent spelled it.

    ``mcp__lemma_tools__lemma_exec_command`` and ``exec_command`` are the same
    tool, and everything that reads a tool name — cards, icons, approvals,
    analytics — has to see the same one either way.
    """
    return normalize_exported_tool_name(strip_provider_namespace(tool_name))


def is_provider_scoped_lemma_mcp_tool_name(tool_name: object) -> bool:
    return (
        isinstance(tool_name, str) and strip_provider_namespace(tool_name) != tool_name
    )


def looks_like_lemma_mcp_payload(payload: object) -> bool:
    if isinstance(payload, dict):
        server_name = (
            payload.get("serverName")
            or payload.get("server_name")
            or payload.get("server")
            or payload.get("mcp_server")
            or payload.get("mcpServer")
        )
        if server_name in _SERVER_NAMES:
            return True
        for key in ("toolName", "tool_name", "tool", "name"):
            value = payload.get(key)
            if is_provider_scoped_lemma_mcp_tool_name(value):
                return True
            if isinstance(value, dict) and looks_like_lemma_mcp_payload(value):
                return True
        return any(looks_like_lemma_mcp_payload(value) for value in payload.values())
    if isinstance(payload, list):
        return any(looks_like_lemma_mcp_payload(item) for item in payload)
    return False
