"""Letting an agent find out who is actually in the pod.

``message_user`` takes a pod member id, a user id, or an exact email address —
and an agent that has been told "ask Priya about the invoice" has none of them.
Without this the tool is unusable unless the id happens to be sitting in the
conversation already, which is why it exists.

Lives in ``composition`` for the usual reason: the agent module must not import
``pod`` directly, and the lazy import inside the function keeps that true. Same
shape as ``agent_notifications.py``, including opening its own unit of work.

Authority is the caller's, not the agent's: ``list_pod_members`` already checks
the requester's org role and pod access, so passing the run's ``user_id``
through means an agent can never enumerate a pod its owner cannot see.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory

# A pod is a team, not a mailing list. Paging past this many to satisfy one
# lookup means the search belongs in SQL (an ILIKE over name/email) rather than
# here — worth doing the moment a real pod gets close to it.
MAX_SCANNED_MEMBERS = 500

# One page of the underlying cursor pagination.
_PAGE_SIZE = 100


def _matches(member, needle: str) -> bool:
    if not needle:
        return True
    haystacks = (
        str(member.user_name or ""),
        str(member.user_email or ""),
        str(member.pod_member_id),
        str(member.user_id or ""),
    )
    return any(needle in value.lower() for value in haystacks)


def _summarize(member, *, requester_user_id: UUID) -> dict:
    return {
        # The pod-scoped id, which ``resolve_pod_recipient`` tries first.
        "to": str(member.pod_member_id),
        "name": member.user_name,
        "email": member.user_email,
        "role": member.role.value if member.role else None,
        "is_you": member.user_id == requester_user_id,
        "user_id": member.user_id,
        "reachable_on": [],
    }


async def list_pod_members(
    *,
    pod_id: UUID,
    requester_user_id: UUID,
    search: str | None = None,
    limit: int = 50,
    actor_agent_id: UUID | None = None,
) -> tuple[list[dict], int, bool] | None:
    """``(members, total_matched, truncated)``, or None when access is refused.

    None rather than an exception because the caller is a tool: an agent asking
    about a pod it cannot see should get an answer it can act on, not a
    traceback the model has to interpret.

    Each member carries the channels ``actor_agent_id`` can reach them on, so
    that choosing one is a read rather than a guess a refused send has to
    correct.
    """
    from app.modules.pod.api.dependencies import get_pod_member_service
    from app.modules.pod.domain.errors import PodAccessDeniedError, PodNotFoundError

    needle = (search or "").strip().lower()
    matched: list[dict] = []
    scanned = 0
    cursor: str | None = None

    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        service = get_pod_member_service(uow)
        try:
            while scanned < MAX_SCANNED_MEMBERS:
                members, cursor = await service.list_pod_members(
                    pod_id, requester_user_id, limit=_PAGE_SIZE, cursor=cursor
                )
                scanned += len(members)
                matched.extend(
                    _summarize(member, requester_user_id=requester_user_id)
                    for member in members
                    if _matches(member, needle)
                )
                if not cursor:
                    break
        except PodAccessDeniedError, PodNotFoundError:
            return None

    total = len(matched)
    page = matched[:limit]
    # Only the page that is actually returned: reachability costs queries per
    # surface, and answering it for members nobody will see is work spent on
    # rows the model never reads.
    await _attach_reach(page, pod_id=pod_id, actor_agent_id=actor_agent_id)
    return page, total, total > limit


async def _attach_reach(
    members: list[dict], *, pod_id: UUID, actor_agent_id: UUID | None
) -> None:
    """Fill in each member's ``reachable_on``, then drop the id it needed.

    ``user_id`` is the key reachability is computed against and is not part of
    what the tool returns — ``to`` is the value the model copies, and offering a
    second id would only invite it to pass the wrong one.
    """
    from app.composition.agent_notifications import reachable_channels

    recipients = {
        member["user_id"]: member["email"] for member in members if member["user_id"]
    }
    reach = (
        await reachable_channels(
            pod_id=pod_id, recipients=recipients, actor_agent_id=actor_agent_id
        )
        if recipients
        else {}
    )
    for member in members:
        member["reachable_on"] = reach.get(member.pop("user_id"), [])


__all__ = ["MAX_SCANNED_MEMBERS", "list_pod_members"]
