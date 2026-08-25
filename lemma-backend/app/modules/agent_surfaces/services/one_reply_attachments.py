"""Turning files a run showed into attachments on the one reply it gets.

``display_resource`` on a surface that replies once has nowhere to deliver to,
so it holds the pod path instead. This is where the promise it made comes true,
and it happens at the moment the reply is built -- so a run that never replies
leaves nothing behind for the next one to pick up.

Split out of ``surface_egress`` because it is the only thing there that needs
both the pod (to load bytes and to sign links) and the platform (to know which
of the two that platform can use), and because the file it was in was already
at the size ratchet's ceiling.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.modules.agent_surfaces.domain.envelope import EnvelopeFile
from app.modules.agent_surfaces.platforms.composio_email import (
    is_composio_credentials,
)
from app.modules.agent_surfaces.platforms.email_attachments import (
    resolve_outbound_email_attachment_urls,
)
from app.modules.agent_surfaces.services.display_resource_content import (
    resolve_pod_file_parts,
)
from app.modules.agent_surfaces.services.pending_envelope import take_display_paths
from app.modules.agent_surfaces.services.surface_route_types import SurfaceEgressTarget

__all__ = ["files_held_for_one_reply"]


class _PodPathSigningDeps:
    """The two fields the datastore URL signer reads off an agent context.

    It was written against ``ctx.deps`` from a tool call, and there is no tool
    call here any more -- the run observer builds the reply. Rather than widen
    the signer, hand it the shape it already expects.
    """

    __slots__ = ("pod_id", "conversation_id")

    def __init__(self, pod_id, conversation_id) -> None:
        self.pod_id = pod_id
        self.conversation_id = conversation_id


async def files_held_for_one_reply(
    *,
    uow: Any,
    conversation_service: Any,
    target: SurfaceEgressTarget,
    conversation_id: UUID,
) -> list[EnvelopeFile]:
    """Files ``display_resource`` queued for a surface that replies once.

    Empty everywhere else: a chat surface delivered them when they were shown.
    """
    if not target.adapter._delivers_one_reply():
        return []
    paths = take_display_paths(conversation_id)
    if not paths:
        return []
    files: list[EnvelopeFile] = []
    for path in paths:
        resolved = await resolve_pod_file_parts(
            uow=uow,
            conversation_service=conversation_service,
            target=target,
            conversation_id=conversation_id,
            path=path,
            caption=None,
        )
        files.extend(resolved.files)
    if not files or not is_composio_credentials(target.credentials):
        return files
    return await _signed(target, conversation_id, files)


async def _signed(
    target: SurfaceEgressTarget,
    conversation_id: UUID,
    files: list[EnvelopeFile],
) -> list[EnvelopeFile]:
    """Attach a signed link to each file, for a mailbox that cannot take bytes.

    Done here rather than in the adapter because signing needs pod services,
    which an adapter has no way to reach.
    """
    urls, _unresolved = await resolve_outbound_email_attachment_urls(
        _PodPathSigningDeps(target.surface.pod_id, conversation_id),
        [file.source_path for file in files if file.source_path],
    )
    by_name = dict(urls)
    return [
        file.model_copy(update={"signed_url": by_name.get(file.file_name)})
        for file in files
    ]
