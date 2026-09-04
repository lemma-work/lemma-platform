"""Who is actually in a pod, for a caller that only knows somebody's name.

`message_user` takes a pod member id, a user id, or an exact email address --
and an agent told "ask Priya about the invoice" has none of them. Without this
the tool is unusable unless an id happens to be sitting in the conversation
already, which is why it exists.

Replaces `app/composition/agent_pod_members.py`, which did this *and* decorated
each row with the channels an agent could reach the person on. The second half
is `agent_surfaces`' answer, not pod's: a pod is a membership list, and which
apps somebody has connected is not a fact about their membership. Held together
in one function it read as a single lookup, so the two lived in a file that
belonged to neither module and returned untyped dicts because neither module's
vocabulary could describe the mixture. Whoever wants both now asks both.

Authority is the caller's, not the agent's: `PodMemberService.list_pod_members`
already checks the requester's org role and pod access, so passing the run's
`user_id` through means an agent can never enumerate a pod its owner cannot see.

A submodule rather than `contracts/__init__`, which is a leaf: this reaches the
service layer, and everything wanting any pod contract would otherwise pay for
it.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory

# A pod is a team, not a mailing list. Paging past this many to satisfy one
# lookup means the search belongs in SQL (an ILIKE over name/email) rather than
# here — worth doing the moment a real pod gets close to it.
MAX_SCANNED_MEMBERS = 500

# One page of the underlying cursor pagination.
_PAGE_SIZE = 100


@dataclass(frozen=True, slots=True)
class PodDirectoryMember:
    """One member, in the terms a caller outside `pod` needs them in."""

    #: The pod-scoped id, which `resolve_pod_recipient` tries first. A string
    #: because it is meant to be copied through unaltered into whatever asked.
    to: str
    name: str | None
    email: str | None
    role: str | None
    #: True for the person whose authority the lookup ran under.
    is_you: bool
    #: The key anything *else* about this person is looked up by. Separate from
    #: `to` on purpose: they are different ids, and a caller rendering both
    #: invites whoever reads it to pass the wrong one.
    user_id: UUID | None


@dataclass(frozen=True, slots=True)
class PodDirectoryPage:
    """One page of matches, and how much was left behind.

    `total_matched` can exceed `len(members)`; `truncated` says whether it does,
    so a caller can tell the difference between "these are all of them" and
    "narrow your search" without comparing two numbers itself. Returning a
    partial list silently is how an agent messages the wrong person.
    """

    members: tuple[PodDirectoryMember, ...]
    total_matched: int
    truncated: bool


async def list_pod_members(
    *,
    pod_id: UUID,
    requester_user_id: UUID,
    search: str | None = None,
    limit: int = 50,
) -> PodDirectoryPage | None:
    """The pod's members matching `search`, or ``None`` when access is refused.

    ``None`` rather than an exception because the nearest caller is an agent
    tool: an agent asking about a pod it cannot see should get an answer it can
    act on, not a traceback the model has to interpret.

    Its own unit of work, like the surfaces contracts an agent tool reaches
    beside it: a lookup is not part of the asking run's transaction.
    """
    from app.modules.pod.api.dependencies import get_pod_member_service
    from app.modules.pod.domain.errors import PodAccessDeniedError, PodNotFoundError

    needle = (search or "").strip().lower()
    matched: list[PodDirectoryMember] = []
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
    return PodDirectoryPage(
        members=tuple(matched[:limit]),
        total_matched=total,
        truncated=total > limit,
    )


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


def _summarize(member, *, requester_user_id: UUID) -> PodDirectoryMember:
    return PodDirectoryMember(
        to=str(member.pod_member_id),
        name=member.user_name,
        email=member.user_email,
        role=member.role.value if member.role else None,
        is_you=member.user_id == requester_user_id,
        user_id=member.user_id,
    )


__all__ = [
    "MAX_SCANNED_MEMBERS",
    "PodDirectoryMember",
    "PodDirectoryPage",
    "list_pod_members",
]
