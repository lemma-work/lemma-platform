"""Reading and writing one pod file, for a module that is not importing a bundle.

Separate from `provisioning.py` beside it, which is the bundle importer's
twelve-operation surface: this pair is what a connector operation needs when it
uploads a file the pod already holds, or lands one it just downloaded. Same
service underneath, different use case, and drawing the contract around the use
case is what keeps either of them from growing the other's arguments.

Both return a value object rather than a `DatastoreFileEntity`. The entity has
eighteen fields, four of them about the indexing pipeline, and a caller handed
one starts reading them: `app/composition/connector_pod_files.py` reached for
three through `getattr(..., None)`, which is a caller guessing at another
module's schema and quietly answering `None` when it guesses wrong.

A submodule for the same reason as its siblings: these reach the service layer,
and `contracts/__init__` is imported by anything that wants any contract at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.authorization.context import Context
from app.modules.datastore.api.dependencies import build_file_service


@dataclass(frozen=True, slots=True)
class PodFileContent:
    """A file's bytes, with the two facts a caller needs to hand them on."""

    content: bytes
    media_type: str | None
    name: str


@dataclass(frozen=True, slots=True)
class StoredPodFile:
    """Where a written file landed, and what the pod recorded about it."""

    pod_path: str
    size_bytes: int
    media_type: str | None


async def read_pod_file(
    uow, *, pod_id: UUID, path: str, ctx: Context
) -> PodFileContent:
    """The file at this pod path: its bytes, its media type and its name.

    `provisioning.download_file` answers the same question with the bytes alone,
    which is all a bundle export wants. A caller forwarding the file somewhere
    else needs to say what it is and what it is called.
    """
    entity, content = await build_file_service(uow).download_file_content_by_path(
        pod_id, path, ctx
    )
    return PodFileContent(
        content=content, media_type=entity.mime_type, name=entity.name
    )


async def read_pod_file_by_id(
    uow, *, pod_id: UUID, file_id: UUID, ctx: Context
) -> PodFileContent:
    """The same, for a file named by id.

    An id is stable across renames and means the same file to everyone, where
    ``/me/...`` resolves against whoever asks -- which is why a caller handing a
    file to someone else's automation would rather pass one. Authorization is
    the by-path check, on the file's own path, and a file in another pod is
    reported missing rather than read.
    """
    service = build_file_service(uow)
    entity = await service.get_file(file_id, ctx)
    if entity.pod_id != pod_id:
        from app.modules.datastore.domain.errors import DatastoreFileNotFoundError

        raise DatastoreFileNotFoundError(f"File {file_id} not found")
    _, content = await service.download_file_content_by_path(pod_id, entity.path, ctx)
    return PodFileContent(
        content=content, media_type=entity.mime_type, name=entity.name
    )


async def write_pod_file(
    uow,
    *,
    pod_id: UUID,
    directory: str,
    name: str,
    content: bytes,
    ctx: Context,
) -> StoredPodFile:
    """Land bytes in the pod under ``directory``, and say where they went."""
    entity = await build_file_service(uow).create_file(
        pod_id,
        name,
        content,
        ctx,
        directory_path=directory or "/",
    )
    return StoredPodFile(
        pod_path=_as_requester_path(entity.path, ctx.user_id),
        size_bytes=entity.size_bytes,
        media_type=entity.mime_type,
    )


def _as_requester_path(path: str, user_id: object) -> str:
    """``/{user_id}/x`` as ``/me/x``: the form the caller wrote, and can reuse.

    The entity carries the raw storage path. Handing that back put a user id in
    a connector result, which a caller then had to translate before passing the
    same file to the next operation.
    """
    root = f"/{user_id}"
    if path == root:
        return "/me"
    if path.startswith(f"{root}/"):
        return f"/me{path.removeprefix(root)}"
    return path


__all__ = [
    "PodFileContent",
    "StoredPodFile",
    "read_pod_file",
    "read_pod_file_by_id",
    "write_pod_file",
]
