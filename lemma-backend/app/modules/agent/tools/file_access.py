"""Dual-store file reads for agent tools.

The pod datastore (``/me/...`` and other pod-visible paths) is the source of
truth for user-facing files; the workspace sandbox (``/workspace/...`` or paths
relative to the conversation cwd) is the agent's ephemeral working area. Tools
that read a file should target the store they mean: ``read_pod_file_bytes`` for
the datastore (grant-checked) and ``read_workspace_file_bytes`` for the sandbox.

Some tools (speech ``listen``) accept a single user-supplied path and infer the
store from its shape via ``read_agent_file_bytes`` / ``is_datastore_path``; tools
that already know the store (view-image) call the explicit readers directly so
there is no ambiguous path-shape routing.

Writes are intentionally NOT bridged: user-facing artifacts are written to the
datastore via ``DatastoreFileService.create_file`` (the same path the lemma CLI
uses); scratch files stay in the sandbox via ``file_manager.write_file``.
"""

from __future__ import annotations

import mimetypes

from app.core.file_types import is_untyped_mime, sniff_media_mime
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.pod.pod_data_access import pod_services


def _best_mime(stored: str | None, path: str, content: bytes) -> str | None:
    """The type of a file, preferring what is known over what was assumed.

    ``application/octet-stream`` had been treated as an answer, and it is the
    opposite: the datastore types a file by its name alone, so anything saved
    without an extension comes back claiming to be a blob. That claim is truthy,
    so it won every ``or`` chain and the byte sniffer sitting at the end of them
    never ran. A Telegram photo -- saved as bare ``photo``, because the platform
    sends neither a filename nor a type -- reached `view_image` as
    ``application/octet-stream`` and was refused for not being an image.

    Checked in order of how much each source actually knows: a stored type that
    names something, then the extension, then the bytes. The bytes are last
    because they are the most expensive to be wrong about and the least likely to
    be needed.
    """
    if not is_untyped_mime(stored):
        return stored
    return mimetypes.guess_type(path)[0] or sniff_media_mime(content) or stored or None


def is_datastore_path(path: str) -> bool:
    """True when ``path`` addresses the pod datastore rather than the sandbox.

    Absolute paths (``/me/...`` and other pod-visible roots) are datastore
    paths; ``/workspace/...`` and relative paths belong to the sandbox.
    """
    candidate = (path or "").strip()
    if not candidate.startswith("/"):
        return False
    return candidate != "/workspace" and not candidate.startswith("/workspace/")


async def read_pod_file_bytes(
    deps: BaseAgentContext, path: str
) -> tuple[bytes, str | None]:
    """Read a file's bytes + best-effort mime from the pod datastore.

    Runs under the agent's delegated-workload authorization; raises ``DomainError``
    (404 for a missing file, ``MISSING_WORKLOAD_RESOURCE_GRANT``/403 without a
    read grant) which callers translate into a tool-level error.
    """
    async with pod_services(deps) as services:
        entity, content = await services.file.download_file_content_by_path(
            deps.pod_id, path, services.ctx
        )
    return content, _best_mime(entity.mime_type, path, content)


async def read_workspace_file_bytes(
    deps: BaseAgentContext, path: str
) -> tuple[bytes, str | None]:
    """Read a file's bytes + best-effort mime from the workspace sandbox.

    Raises ``FileNotFoundError`` when the file is missing; callers translate that
    into a tool-level error.
    """
    raw = await deps.file_manager.read_file(path)
    content = raw.encode("utf-8") if isinstance(raw, str) else raw
    mime = mimetypes.guess_type(path)[0] or sniff_media_mime(content)
    return content, mime


async def read_agent_file_bytes(
    deps: BaseAgentContext, path: str
) -> tuple[bytes, str | None]:
    """Read a file's bytes + best-effort mime, inferring the store from ``path``.

    Retained for callers (speech ``listen``) that take a single user-supplied
    path; tools that already know the store should call ``read_pod_file_bytes`` /
    ``read_workspace_file_bytes`` directly.
    """
    if is_datastore_path(path):
        return await read_pod_file_bytes(deps, path)
    return await read_workspace_file_bytes(deps, path)
