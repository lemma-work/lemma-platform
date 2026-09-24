"""Dual-store file reads for agent tools.

The pod datastore (``/me/...`` and other pod-visible paths) is the source of
truth for user-facing files; the workspace sandbox (an absolute path under the
sandbox home, or one relative to the conversation cwd) is the agent's ephemeral
working area. Tools
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
import posixpath

from sandbox_runtime.paths import RUNTIME_FILESYSTEM_ROOTS
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
    paths; anything under a runtime filesystem root -- the sandbox user's home
    and ``/tmp`` -- and every relative path belong to the sandbox.

    The roots are read from `RUNTIME_FILESYSTEM_ROOTS` rather than spelled here,
    because the default when a path matches nothing is to route it at the pod:
    a root this list forgot does not fail, it silently addresses the wrong disk.

    The path is normalised first, so the prefix being compared is the one the
    path actually names: `/tmp/../me/report` reads as `/me/report` and goes to
    the pod, where a raw prefix check saw `/tmp/` and sent it to the sandbox.
    Lexical only, deliberately -- symlinks are resolved by the containment clamp
    on the sandbox side, which is where the filesystem to resolve them against
    actually is.

    Leading slashes collapse before that, because `normpath` will not do it:
    POSIX leaves exactly two implementation-defined, so `//tmp/x` survives while
    `///tmp/x` becomes `/tmp/x`. Joining a cwd that ends in `/` to an absolute
    name produces precisely that doubled form, and it named the sandbox.
    """
    raw = (path or "").strip()
    if raw.startswith("/"):
        raw = "/" + raw.lstrip("/")
    candidate = posixpath.normpath(raw)
    if not candidate.startswith("/"):
        return False
    return not any(
        candidate == root or candidate.startswith(f"{root}/")
        for root in RUNTIME_FILESYSTEM_ROOTS
    )


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
    if _on_the_host(deps, path):
        content = await _read_host_file(deps, path)
    else:
        raw = await deps.file_manager.read_file(path)
        content = raw.encode("utf-8") if isinstance(raw, str) else raw
    mime = mimetypes.guess_type(path)[0] or sniff_media_mime(content)
    return content, mime


def _on_the_host(deps: BaseAgentContext, path: str) -> bool:
    """On a host-execution run, whether ``path`` names a file on the Mac.

    Such a run has two filesystems: its own folder on the owner's Mac, and the
    VM workspace where the browser saves screenshots. A path under the VM's home
    (``/home/user/...``) is the VM's; everything else, relative paths included,
    is the Mac's -- that is where the run's commands wrote it.
    """
    if getattr(deps, "host_workspace", None) is None:
        return False
    return not any(
        path == root or path.startswith(f"{root}/")
        for root in RUNTIME_FILESYSTEM_ROOTS
        if root != "/tmp"
    )


async def _read_host_file(deps: BaseAgentContext, path: str) -> bytes:
    from app.modules.agent.tools.workspace_cli.workspace_cli import (
        get_workspace_session,
    )

    from sandbox_runtime.errors import SandboxPathNotFound

    session = await get_workspace_session(deps, session_id=None, close_on_exit=True)
    async with session:
        try:
            return await session.read_file(path)
        except SandboxPathNotFound as exc:
            raise FileNotFoundError(path) from exc


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
