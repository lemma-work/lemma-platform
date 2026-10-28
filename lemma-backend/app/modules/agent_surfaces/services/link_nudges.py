"""Asking a member whether somebody talking in a group, unlinked, is them.

A member whose Telegram account Lemma does not know is, in a group, somebody
from outside: a contact, read-only. They can prove the account is theirs
(``telegram_identity_link``), but only if they know to. So when somebody new
speaks in a group and their first name is a member's, that member is told, in
their own chat, once -- and nothing else happens. A name is whatever its owner
typed, so it never binds anybody (``identity_resolution_service``); it only
decides who is asked.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.domain.groups import SurfaceGroup
from app.modules.agent_surfaces.services.direct_messages import tell_member
from app.modules.pod.contracts.directory import PodDirectoryPage, list_pod_members

logger = get_logger(__name__)

#: A name is somebody else's words in a member's own chat: one line of it.
_MAX_NAME_CHARS = 60


#: Who in the pod matches a search, as the asking member may see them.
Directory = Callable[..., Awaitable[PodDirectoryPage | None]]
#: A notice in one member's own chat, said once per ``key``.
Notice = Callable[..., Awaitable[object]]


async def nudge_lookalikes(
    uow: SqlAlchemyUnitOfWork,
    *,
    group: SurfaceGroup,
    contact_id: UUID,
    name: str | None,
    directory: Directory | None = None,
    notice: Notice | None = None,
) -> None:
    """Tell each member whose first name this newcomer shares. Best-effort:
    the line is logged either way."""
    shown = " ".join(str(name or "").split())[:_MAX_NAME_CHARS]
    first = _first(shown)
    if (
        group.platform != SurfacePlatform.TELEGRAM.value
        or group.owner_user_id is None
        or not first
    ):
        return
    try:
        page = await (directory or list_pod_members)(
            pod_id=group.pod_id, requester_user_id=group.owner_user_id, search=first
        )
        for member in page.members if page is not None else ():
            if member.user_id is None or _first(member.name) != first:
                continue
            await (notice or tell_member)(
                uow,
                pod_id=group.pod_id,
                user_id=member.user_id,
                text=_notice(shown, group.title),
                key=f"link-nudge:{contact_id}:{member.user_id}",
            )
    except SQLAlchemyError, OSError:
        logger.warning(
            "agent_surfaces.link_nudges.nudge_failed.degraded",
            group_id=str(group.id),
            exc_info=True,
        )


def _first(name: str | None) -> str:
    words = str(name or "").split()
    return words[0].casefold() if words else ""


def _notice(name: str, title: str | None) -> str:
    where = f"“{title}”" if title else "a Telegram group"
    return (
        f"“{name}” is writing in {where} on Telegram, and Lemma doesn’t know "
        "them, so they are answered as someone from outside. If that’s you, "
        f"open {where} here, go to People, and tap This is me."
    )
