"""Who is in a group whose history the platform hands over live (Slack, Teams).

Where the pod keeps no log of a group, a platform's lines are the only record
of who is there, and a line says who spoke, never who holds access. These are
the pod's own answers about those speakers: which of them are its members, the
roster a member's run is told, and each line marked by whether somebody
outside the pod wrote it. The pod's own log (Telegram, WhatsApp) answers the
same questions from its lines' senders (``group_log``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence, Set
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.models import SurfaceGroupParticipant
from app.modules.agent_surfaces.domain.ports import SurfacePodMembershipPort
from app.modules.agent_surfaces.infrastructure.adapters.routing_resolution_adapter import (  # noqa: E501
    SqlAlchemySurfaceRoutingResolutionAdapter,
)
from app.modules.agent_surfaces.infrastructure.repositories.external_user_repository import (  # noqa: E501
    ExternalSurfaceUserRepository,
)

logger = get_logger(__name__)

#: How many of a group's people go in front of the model.
MAX_PARTICIPANTS = 20


async def pod_members_in_lines(
    *,
    lines: Sequence[Mapping[str, object]],
    pod_id: UUID,
    platform: str,
    tenant_id: str | None,
    membership: SurfacePodMembershipPort,
    external_users: ExternalSurfaceUserRepository,
) -> set[str]:
    """Which of a fetched history's speakers the pod knows to be members of it.

    Where the pod keeps no log of a group -- Slack and Teams read their own
    history live -- a platform's lines are the only record of who is there, and
    a line says who spoke, never who holds access. What the platform marks is
    not that either: Slack marks a line from another company's workspace, and a
    colleague in the pod's own workspace who has no Lemma account reads exactly
    like one who does. So the question is put to the pod instead: the speakers
    it has already resolved to a Lemma user on this platform, and of those the
    ones still in this pod. A speaker with no such row -- the colleague who has
    never spoken to the bot, and anybody here without an account -- is in
    neither answer, and is not a member the run may speak from.

    One read per table for a whole window, because the alternative is a lookup
    per person on a path that already runs several.
    """
    wanted = sorted({external for line in lines if (external := _external_id(line))})
    if not wanted:
        return set()
    resolved = await external_users.resolved_users_by_external_ids(
        platform=platform, tenant_id=tenant_id, external_user_ids=wanted
    )
    members = await membership.pod_members_among(pod_id, resolved.values())
    return {external for external, user_id in resolved.items() if user_id in members}


#: The two reads a live group's roster asks, over one unit of work: who the pod
#: has resolved each platform sender to, and which of those are in the pod.
RosterReads = Callable[
    [SqlAlchemyUnitOfWork],
    tuple[SurfacePodMembershipPort, ExternalSurfaceUserRepository],
]


def roster_reads(
    uow: SqlAlchemyUnitOfWork,
) -> tuple[SurfacePodMembershipPort, ExternalSurfaceUserRepository]:
    membership = SqlAlchemySurfaceRoutingResolutionAdapter(uow)
    return membership, ExternalSurfaceUserRepository(uow)


async def live_members(
    uow_factory: UnitOfWorkFactory,
    *,
    lines: Sequence[Mapping[str, object]],
    pod_id: UUID,
    platform: str,
    tenant_id: str | None,
    conversation_id: UUID,
    reads: RosterReads = roster_reads,
) -> set[str] | None:
    """Which speakers of a history fetched live (Slack, Teams) hold access here:
    the pod's own answer (``pod_members_in_lines``), since a line says who
    spoke, never who holds access.

    ``None`` when the read fails, which is not "nobody": the caller marks
    every speaker as outside and names no roster, rather than fail the
    member's turn over a best-effort history.
    """
    if not lines:
        return set()
    try:
        async with uow_factory() as uow:
            membership, external_users = reads(uow)
            return await pod_members_in_lines(
                lines=lines,
                pod_id=pod_id,
                platform=platform,
                tenant_id=tenant_id,
                membership=membership,
                external_users=external_users,
            )
    except (SQLAlchemyError, OSError) as exc:
        # The database's family, because the database is all this touches.
        logger.warning(
            "agent_surfaces.ingress_service.group_roster_failed.degraded",
            conversation_id=str(conversation_id),
            error_type=type(exc).__name__,
            exc_info=True,
        )
        return None


def participants_in_lines(
    lines: Sequence[Mapping[str, object]],
    *,
    verified: Set[str] = frozenset(),
    asker_external_id: str | None = None,
) -> tuple[SurfaceGroupParticipant, ...]:
    """The people a fetched history names, and which of them hold access here.

    Where the pod keeps no log of a group -- Slack and Teams read their own
    history live -- the platform's own lines are the only record of who is
    there. Membership is not read off them: ``verified`` is the platform ids the
    pod itself says belong to it (``pod_members_in_lines``), and a speaker whose
    id is not in it is somebody the pod cannot vouch for. Saying otherwise is
    how a room of clients came to read like a room of colleagues.

    One entry per person, told apart by their platform id where the line has
    one: two people may show the same name, and folding them by it let an
    outsider who took a member's name drop out of the roster behind them. The
    asker is not on it -- the roster is who *else* is there -- and nor is an
    app's line, the bot's own answers among them: an app is nobody to answer
    in front of, and named as outside the pod it read as a stranger. An author
    still showing their bare platform id has no name, and is counted as such.
    """
    seen: list[SurfaceGroupParticipant] = []
    people: set[str] = set()
    for line in lines:
        external_id = _external_id(line)
        shown = " ".join(str(line.get("author") or "").split())
        name = "" if shown == external_id else shown
        if (
            not (name or external_id)
            or line.get("from_bot")
            or (asker_external_id and external_id == asker_external_id)
        ):
            continue
        person = f"id:{external_id}" if external_id else f"name:{name.casefold()}"
        if person in people:
            continue
        people.add(person)
        seen.append(SurfaceGroupParticipant(name=name, in_pod=external_id in verified))
        if len(seen) >= MAX_PARTICIPANTS:
            break
    return tuple(seen)


def _external_id(line: Mapping[str, object]) -> str:
    """The platform id a line's author was sent under, where the line has one."""
    return str(line.get("author_external_id") or "").strip()


def marked_for_member_run(
    lines: Sequence[dict[str, object]],
    *,
    verified: Set[str] | None,
    asker_external_id: str | None,
) -> list[dict[str, object]]:
    """A platform's history as a member's run is shown it: every line, each
    marked by whether somebody outside the pod wrote it.

    The same rule ``group_background`` applies to the pod's own log, for the
    platforms whose history is read live (Slack). The mark is the pod's answer
    about who holds access (``live_members``), not the platform's: Slack marks
    only another company's lines, and a colleague here with no Lemma account
    reads exactly like a member. The asker's own lines and an app's are no
    stranger's. Where the pod's answer could not be read (``verified`` is
    ``None``), every other person's line is marked: a stranger left unmarked is
    the mistake that costs, a colleague marked only asks the member before the
    run acts.
    """
    marked: list[dict[str, object]] = []
    for line in lines:
        external_id = _external_id(line)
        somebody_else = not line.get("from_bot") and not (
            asker_external_id and external_id == asker_external_id
        )
        outside = somebody_else and (verified is None or external_id not in verified)
        marked.append({**line, "outside_pod": outside})
    return marked
