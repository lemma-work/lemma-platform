"""Turning files a run showed into attachments on the one reply it gets.

``display_resource`` on a surface that replies once has nowhere to deliver to,
so it holds the pod path instead. This is where the promise it made comes true,
and it happens at the moment the reply is built -- so a run that never replies
leaves nothing behind for the next one to pick up.

Split out of egress because it is the only thing there that needs
both the pod (to load bytes and to sign links) and the platform (to know which
of the two that platform can use), and because the file it was in was already
at the size ratchet's ceiling.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.contracts.platforms import platform_delivers_one_reply
from app.modules.agent_surfaces.domain.envelope import EnvelopeFile
from app.modules.agent_surfaces.services.display_resource_content import (
    resolve_pod_file_parts,
)
from app.modules.agent_surfaces.services.pending_envelope import (
    RunFiles,
    held_display_paths,
    release_display_paths,
)
from app.modules.agent_surfaces.services.surface_route_types import SurfaceEgressTarget

__all__ = ["files_for_held_paths", "held_files_for_run", "release_held_files"]


async def files_for_held_paths(
    *,
    uow: Any,
    target: SurfaceEgressTarget,
    conversation_id: UUID,
    paths: list[str],
) -> list[EnvelopeFile]:
    """The attachments for the pod files ``display_resource`` queued.

    The caller reads the paths (from Redis, outside any connection) and releases
    them once the reply has actually gone out, so a send that fails leaves the
    files for the next one instead of losing them.
    """
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


async def held_files_for_run(
    *,
    uow: SqlAlchemyUnitOfWork,
    target: SurfaceEgressTarget,
    conversation_id: UUID,
    run: RunFiles | None,
) -> tuple[list[EnvelopeFile], list[str]]:
    """The attachments a run has been holding for a one-reply surface, and their paths.

    Only the named run's: a later turn's reply never takes an earlier run's
    files. ``run`` is None for a send that is not any run's reply, which carries
    nothing. Empty everywhere but a surface that replies once: a chat surface
    delivered them when they were shown.

    Read, not drained: the caller releases the paths once the envelope has
    actually been delivered, so a send that fails leaves them for the retry.
    """
    if run is None or not platform_delivers_one_reply(
        target.surface.surface_type.value
    ):
        return [], []
    # Redis, not the database, so no connection is held for it.
    async with connection_released(uow.session):
        paths = await held_display_paths(conversation_id, run)
    if not paths:
        return [], []
    files = await files_for_held_paths(
        uow=uow, target=target, conversation_id=conversation_id, paths=paths
    )
    return files, paths


async def release_held_files(
    *, uow: SqlAlchemyUnitOfWork, conversation_id: UUID, run: RunFiles, paths: list[str]
) -> None:
    """Forget the files a delivered reply carried; see `connection_released`."""
    async with connection_released(uow.session):
        await release_display_paths(conversation_id, run, paths)
