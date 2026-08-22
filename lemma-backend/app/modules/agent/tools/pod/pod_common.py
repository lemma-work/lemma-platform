"""Shared plumbing for the pod tools: paths, grants, and result shapes.

`run_pod_tool` is the piece worth knowing about. Every pod tool goes through it, and it
turns a missing grant into a structured `needs_approval` result rather than an
exception -- because the model can act on that: it re-issues the same action
through `request_approval` and a person decides. An exception would just end the
turn with the agent unable to say what it wanted.

`pod_id` always comes from the run context. A tool argument would let the model
name a pod it was never given access to.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from pydantic_ai import ToolReturn

from app.core.domain.errors import DomainError
from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.pod.pod_data_access import PodServices, pod_services
from app.modules.agent.tools.pod.pod_paths import to_me_path
from app.modules.agent.tools.tool_errors import approval_error_result


def resolve_pod_path(deps: BaseAgentContext, path: str) -> str:
    """Resolve a possibly-relative pod path against the agent's pod cwd."""
    if path.startswith("/"):
        return path
    cwd = deps.get_pod_cwd().rstrip("/")
    return f"{cwd}/{path}" if path else cwd


def split_pod_path(path: str) -> tuple[str, str]:
    """Split an absolute pod path into (directory_path, name)."""
    normalized = path if path.startswith("/") else f"/{path}"
    trimmed = normalized.rstrip("/") or "/"
    if trimmed == "/":
        raise ValueError("A file path must include a file name.")
    directory, _, name = trimmed.rpartition("/")
    return (directory or "/", name)


def has_meaningful_data(data: JsonObject | None) -> bool:
    """True if ``data`` has at least one non-null, non-blank value.

    Rejects ``None``, ``{}``, and payloads whose values are all null or empty/
    whitespace strings — the shapes that would otherwise write a blank row. ``0``
    and ``False`` are real values and count as meaningful.
    """
    if not data:
        return False
    for value in data.values():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return True
    return False


async def run_pod_tool(
    deps: BaseAgentContext,
    *,
    tool_name: str,
    args: JsonObject,
    op: Callable[[PodServices], Awaitable["JsonObject | ToolReturn"]],
) -> "JsonObject | ToolReturn":
    """Run a pod operation, mapping authorization 403s to ``needs_approval``.

    Most ops return a ``JsonObject``; image tools return a ``ToolReturn`` so the
    model receives inline image content while only a reference is persisted.
    """
    try:
        async with pod_services(deps) as services:
            return await op(services)
    except DomainError as exc:
        return approval_error_result(exc, tool_name=tool_name, args=args)


def file_summary(entity: Any, user_id: Any) -> JsonObject:
    """Curated view of a file for listings — surfaces whether it's an indexed
    document and how many pages it has, so the agent knows what to read/view."""
    metadata = getattr(entity, "metadata", None) or {}
    status = getattr(entity, "status", None)
    status_value = status.value if hasattr(status, "value") else status
    kind = getattr(entity, "kind", None)
    kind_value = kind.value if hasattr(kind, "value") else kind
    return {
        "path": to_me_path(entity.path, user_id),
        "name": entity.name,
        "kind": kind_value,
        "mime_type": getattr(entity, "mime_type", None),
        "size_bytes": getattr(entity, "size_bytes", None),
        "status": status_value,
        "indexed": status_value == "COMPLETED",
        "page_count": metadata.get("page_count"),
        "has_markdown": metadata.get("has_markdown", False),
        "description": getattr(entity, "description", None),
    }
