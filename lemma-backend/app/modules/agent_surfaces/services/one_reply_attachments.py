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
from app.modules.agent_surfaces.services.display_resource_content import (
    resolve_pod_file_parts,
)
from app.modules.agent_surfaces.services.pending_envelope import take_display_paths
from app.modules.agent_surfaces.services.surface_route_types import SurfaceEgressTarget

__all__ = ["files_held_for_one_reply"]


async def files_held_for_one_reply(
    *,
    uow: Any,
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
            target=target,
            conversation_id=conversation_id,
            path=path,
            caption=None,
        )
        files.extend(resolved.files)
    return files
