"""Files a line shares: checked when written, read through the line.

A file part names a pod file. Whoever writes the line must be able to read it
(the datastore's own rule, with their own access), and it is stored by the
file's id and its own path -- ``/me/…`` means somebody else to every other
reader. It is read back through the line it is on
(``GET …/messages/{message_id}/files/{file_id}``): by whoever may see the line,
as its writer reads the file. So a file shared in a private group, a member's
own channel or a contact's is read by that channel's readers and nobody else,
wherever it lives -- the app keeps each channel's uploads in the writer's own
``/me/channels/{channel_id}/``, which no other member can open directly.
"""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Response

from app.core.api.dependencies import UoWDep
from app.core.authorization.context import Context
from app.core.authorization.dependencies import require_action
from app.core.authorization.factory import create_authorization_data_service
from app.core.authorization.permissions import Permissions
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.api.access import (
    ERRORS,
    CallerDep,
    missing,
    unprocessable,
    visible,
)
from app.modules.channels.api.message_schemas import hidden_from
from app.modules.channels.domain.entities import SenderKind
from app.modules.channels.domain.parts import FilePart, Part
from app.modules.channels.infrastructure.lines import LineRepository
from app.modules.datastore.contracts.pod_files import (
    read_pod_file_by_id,
    readable_pod_file,
)

router = APIRouter(
    prefix="/pods/{pod_id}/channels", tags=["Channels"], responses=ERRORS
)

#: Shown in place: raster images. Anything else -- SVG and HTML included,
#: which can carry script -- is a download.
_INLINE = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})

_BINARY = {
    200: {
        "description": "The file's bytes",
        "content": {
            "application/octet-stream": {
                "schema": {"type": "string", "format": "binary"}
            }
        },
    }
}


async def checked_parts(
    uow: SqlAlchemyUnitOfWork,
    ctx: Context,
    *,
    pod_id: UUID,
    parts: Sequence[Part],
) -> tuple[Part, ...]:
    """``parts`` as stored: each file one the writer may read, named by its
    id and its own path. A file they may not read -- or that is not there --
    refuses the line."""
    shared: list[Part] = []
    for part in parts:
        if not isinstance(part, FilePart):
            shared.append(part)
            continue
        found = await readable_pod_file(uow, pod_id=pod_id, path=part.path, ctx=ctx)
        if found is None:
            raise unprocessable(
                f"“{part.name}” is not a file of the pod you can read, so it "
                "cannot be shared here"
            )
        shared.append(
            part.model_copy(
                update={
                    "path": found.path,
                    "file_id": found.file_id,
                    "mime_type": part.mime_type or found.media_type,
                    "size": found.size_bytes,
                }
            )
        )
    return tuple(shared)


@router.get(
    "/{channel_id}/messages/{message_id}/files/{file_id}",
    operation_id="channel.message.file",
    response_class=Response,
    responses=_BINARY,
    dependencies=[require_action(Permissions.CHANNEL_READ)],
)
async def line_file(
    pod_id: UUID,
    channel_id: UUID,
    message_id: UUID,
    file_id: UUID,
    uow: UoWDep,
    caller: CallerDep,
) -> Response:
    """A file a line shares, for a reader of the line: read as its writer
    reads it. Gone with the line, or once its writer can no longer read it."""
    channel = await visible(uow, caller, channel_id)
    line = await LineRepository(uow.session).get(message_id)
    if (
        line is None
        or line.channel_id != channel.id
        or line.is_deleted
        or hidden_from(line, channel, caller.user_id)
        or line.sender.kind is not SenderKind.MEMBER
        or line.sender.user_id is None
    ):
        raise missing("File")
    if not any(
        isinstance(part, FilePart) and part.file_id == file_id for part in line.parts
    ):
        raise missing("File")
    try:
        writer = await create_authorization_data_service(uow).build_user_context(
            user_id=line.sender.user_id, pod_id=pod_id
        )
        found = await read_pod_file_by_id(
            uow, pod_id=pod_id, file_id=file_id, ctx=writer
        )
    except DomainError as gone:
        raise missing("File") from gone
    media_type = found.media_type or "application/octet-stream"
    disposition = "inline" if media_type in _INLINE else "attachment"
    return Response(
        content=found.content,
        media_type=media_type
        if disposition == "inline"
        else "application/octet-stream",
        headers={
            "Content-Disposition": (
                f"{disposition}; filename*=UTF-8''{quote(found.name, safe='')}"
            ),
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


__all__ = ["checked_parts", "router"]
