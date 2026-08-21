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
    # Imported lazily to avoid a module-load cycle (this module is imported
    # by the platform services).
    from app.composition.surface_agent import is_datastore_path, pod_services

    inline: list[tuple[str, bytes, str]] = []
    links: list[tuple[str, str]] = []
    for path in paths:
        if is_datastore_path(path):
            async with pod_services(deps) as services:
                entity = await services.file.get_file_by_path(
                    deps.pod_id, path, services.ctx
                )
                size = entity.size_bytes
                # A known, positive size at/below the cap inlines. Treat 0 or an
                # unrecorded size as "not known to fit" and deliver a link, so an
                # unbounded file whose size wasn't stamped can't be inlined at full
                # size and blow the provider's hard limit.
                if isinstance(size, int) and 0 < size <= inline_cap_bytes:
                    (
                        _entity,
                        content,
                    ) = await services.file.download_file_content_by_path(
                        deps.pod_id, path, services.ctx
                    )
                    inline.append(
                        (
                            entity.name,
                            content,
                            entity.mime_type or guess_content_type(entity.name),
                        )
                    )
                else:
                    (
                        _entity,
                        signed_url,
                        _expires,
                        _hits,
                    ) = await services.file.create_signed_url(
                        deps.pod_id, path, services.ctx
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
    from app.composition.surface_agent import is_datastore_path, pod_services

    resolved: list[tuple[str, str]] = []
    unresolved: list[str] = []
    for path in paths:
        if is_datastore_path(path):
            async with pod_services(deps) as services:
                entity = await services.file.get_file_by_path(
                    deps.pod_id, path, services.ctx
                )
                (
                    _entity,
                    signed_url,
                    _expires,
                    _hits,
                ) = await services.file.create_signed_url(
                    deps.pod_id, path, services.ctx
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
