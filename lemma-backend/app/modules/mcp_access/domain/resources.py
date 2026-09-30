"""The URL a pod is reachable at over MCP, and the reverse.

One URL per pod: ``{api}/mcp/{pod_id}``. The URL is the RFC 8707 resource
indicator a client asks for a token for, and the audience the token is then
bound to, so a token issued for one pod is refused by every other pod's
endpoint even though they share a server.

One URL for every pod, with the pod picked on the consent screen, was the
alternative. It would save a person adding a second connector for a second
pod, and it would make the resource indicator name nothing in particular:
every token would have the same audience, and which pod it opened would be a
fact stored beside it rather than one the client asked for and can see.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

MCP_MOUNT_PATH = "/mcp"
PROTECTED_RESOURCE_METADATA_PATH = "/.well-known/oauth-protected-resource"


def _canonical(url: str) -> str:
    """RFC 3986 normal form as RFC 8707 compares it: case-folded scheme and
    host, no fragment, no trailing slash on the path."""
    parts = urlsplit(url.strip())
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path.rstrip("/"),
            parts.query,
            "",
        )
    )


def pod_resource_url(api_url: str, pod_id: UUID) -> str:
    return _canonical(f"{api_url.rstrip('/')}{MCP_MOUNT_PATH}/{pod_id}")


def protected_resource_metadata_url(api_url: str, pod_id: UUID) -> str:
    """RFC 9728 §3.1: the well-known segment goes between host and path --
    all of the path, including any prefix the API is served under, so a client
    that probes the well-known location instead of reading the challenge finds
    the same document."""
    parts = urlsplit(pod_resource_url(api_url, pod_id))
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            PROTECTED_RESOURCE_METADATA_PATH + parts.path,
            "",
            "",
        )
    )


def api_path_prefix(api_url: str) -> str:
    """The path the API is served under, ``""`` at the root, e.g. ``/api``."""
    return urlsplit(_canonical(api_url)).path


def pod_id_for_resource(api_url: str, resource: str | None) -> UUID | None:
    """The pod a resource indicator names, if it names one on this server.

    ``None`` for anything else -- another host, another path, a query string --
    so the caller answers ``invalid_target`` rather than guessing.
    """
    if not resource:
        return None
    canonical = _canonical(resource)
    prefix = _canonical(api_url) + MCP_MOUNT_PATH + "/"
    if not canonical.startswith(prefix):
        return None
    tail = canonical[len(prefix) :]
    try:
        pod_id = UUID(tail)
    except ValueError:
        return None
    return pod_id if pod_resource_url(api_url, pod_id) == canonical else None
