"""Turning attachment paths into the bytes or links an email can carry.

Datastore files can be signed into a URL and workspace files cannot, which is
the distinction every function here is built around: past a size cap a
datastore file becomes a link, while an oversize workspace file has nowhere to
go and is dropped with a warning rather than failing the send.
"""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any

from app.core.log.log import get_logger
from app.modules.agent.contracts.pod_files import (
    is_pod_datastore_path,
    pod_datastore_access,
)
from app.modules.datastore.contracts.surfaces import (
    download_pod_file,
    read_pod_file,
    sign_pod_file,
)

logger = get_logger(__name__)


def guess_content_type(file_name: str) -> str:
    return mimetypes.guess_type(file_name)[0] or "application/octet-stream"


def decode_base64_bytes(
    data: str,
    *,
    urlsafe: bool,
) -> bytes:
    normalized = str(data or "").strip()
    if not normalized:
        return b""
    padding = "=" * (-len(normalized) % 4)
    payload = normalized + padding
    if urlsafe:
        return base64.urlsafe_b64decode(payload.encode("ascii"))
    return base64.b64decode(payload.encode("ascii"))


def file_name_from_path(path: str) -> str:
    return Path(path).name or "attachment"


def outbound_paths_for_reply(deps: Any, requested: list[str]) -> list[str]:
    """Everything this reply should carry: what the agent asked for, and what it showed.

    ``display_resource`` on an email surface has nowhere to deliver to, so it
    holds the file for the one reply instead. Draining it here is what makes
    that promise true, and doing it in one helper is what keeps the three reply
    tools from drifting on it.

    The agent's own ``attachment_paths`` come first and win a tie: a file it
    listed deliberately is the same file, not a second copy of it.
    """
    from app.modules.agent_surfaces.services.pending_envelope import (
        take_display_paths,
    )

    conversation_id = getattr(deps, "conversation_id", None)
    held = take_display_paths(conversation_id) if conversation_id else []
    merged = list(requested)
    merged.extend(path for path in held if path not in merged)
    return merged


async def resolve_outbound_email_attachments(
    deps: Any,
    paths: list[str],
    *,
    inline_cap_bytes: int,
) -> tuple[list[tuple[str, bytes, str]], list[tuple[str, str]]]:
    """Resolve attachment paths into (inline files, link files) for an email.

    Datastore (``/me/...``) files are inlined when at/below ``inline_cap_bytes``,
    else returned as a (name, signed_url) link. Workspace paths are always
    inlined. Returns ``(inline, links)`` where ``inline`` is a list of
    ``(file_name, bytes, mime)`` and ``links`` is ``(file_name, url)``.
    """
    inline: list[tuple[str, bytes, str]] = []
    links: list[tuple[str, str]] = []
    for path in paths:
        if is_pod_datastore_path(path):
            async with pod_datastore_access(deps) as pod:
                entity = await read_pod_file(
                    pod.uow, pod_id=pod.pod_id, path=path, ctx=pod.ctx
                )
                size = entity.size_bytes
                # A known, positive size at/below the cap inlines. Treat 0 or an
                # unrecorded size as "not known to fit" and deliver a link, so an
                # unbounded file whose size wasn't stamped can't be inlined at full
                # size and blow the provider's hard limit.
                if isinstance(size, int) and 0 < size <= inline_cap_bytes:
                    _entity, content = await download_pod_file(
                        pod.uow, pod_id=pod.pod_id, path=path, ctx=pod.ctx
                    )
                    inline.append(
                        (
                            entity.name,
                            content,
                            entity.mime_type or guess_content_type(entity.name),
                        )
                    )
                else:
                    _entity, signed_url = await sign_pod_file(
                        pod.uow, pod_id=pod.pod_id, path=path, ctx=pod.ctx
                    )
                    links.append((entity.name, signed_url))
        else:
            raw = await deps.file_manager.read_file(path)
            content = raw.encode("utf-8") if isinstance(raw, str) else raw
            name = file_name_from_path(path)
            # Workspace files can't be signed into a link, so bound them by the
            # actual byte length — an oversize workspace file inlined unconditionally
            # would fail the whole send. Skip (with a warning) rather than hard-fail.
            if len(content) > inline_cap_bytes:
                logger.debug(
                    "agent_surfaces.email_attachments.skipping_oversize_workspace_email_attachment.diagnostic",
                    count=len(content),
                    inline_cap_bytes=inline_cap_bytes,
                )
                continue
            inline.append((name, content, guess_content_type(name)))
    return inline, links


async def resolve_outbound_email_attachment_urls(
    deps: Any,
    paths: list[str],
) -> tuple[list[tuple[str, str]], list[str]]:
    """Resolve attachment paths to signed URLs for a Composio email send.

    Composio's Gmail/Outlook actions accept a public/signed URL in their
    ``attachment`` field and download it server-side, so datastore files become
    ``(name, signed_url)``. Workspace files can't be signed into a URL and are
    returned as unresolved names (the caller notes them). Returns
    ``(url_attachments, unresolved_names)``.
    """
    resolved: list[tuple[str, str]] = []
    unresolved: list[str] = []
    for path in paths:
        if is_pod_datastore_path(path):
            async with pod_datastore_access(deps) as pod:
                entity, signed_url = await sign_pod_file(
                    pod.uow, pod_id=pod.pod_id, path=path, ctx=pod.ctx
                )
                resolved.append((entity.name, signed_url))
        else:
            unresolved.append(file_name_from_path(path))
    return resolved, unresolved


def append_attachment_links(content: str, links: list[tuple[str, str]]) -> str:
    """Append large-file download links to an email body (plain text block)."""
    if not links:
        return content
    block = "\n\n".join(f"{name}: {url}" for name, url in links)
    return f"{content}\n\n{block}" if content else block
