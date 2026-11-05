"""Who a per-member connector is still waiting on, and asking them.

Design §3.7: an editor sees "5/7 connected" and who is missing, and can ask
them to connect. Both answers are about other people, so both keep to what an
editor may know of them: who they are and whether they have connected --
never which account, and never anything about it.

**Who counts.** Only members who may use the connector: a POD_VIEWER, or
anyone a RESTRICTED connector was not granted to, is not "missing" an account
for something they could not run. Each member's own authority answers that,
through the same authorizer a call of theirs would meet.

**Cost.** That is one authorization context per member, so it is never paid
on a listing: the connectors list stays a per-caller read, and the editor's
view asks ``GET /pods/{pod}/connectors/{slug}/coverage`` for the one connector
it is showing.

**Asking.** One line in each member's own channel with the pod -- the path the
agents' ``channel_post`` takes: the pod's agent speaking for the editor who
asked. A member is asked about one connector at most once a day, whoever asks:
the ledger is read before asking and written only after the line is committed,
so a post that failed never leaves somebody marked as asked.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from app.core.authorization.context import Context, ResourceRef
from sqlalchemy.exc import SQLAlchemyError

from app.core.authorization.permissions import Permissions
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.connectors.api.schemas.pod_connector_schemas import (
    AskMembersResponse,
    MemberAwaited,
    MemberConnectStanding,
    PodConnectorCoverage,
)
from app.modules.connectors.domain.account import AccountStatus
from app.modules.connectors.domain.pod_connector import (
    PodConnectorEntity,
    PodConnectorMode,
)
from app.modules.connectors.domain.pod_connector_errors import (
    PodConnectorNotFoundError,
    PodConnectorValidationError,
)
from app.modules.connectors.infrastructure.repositories.member_standing_queries import (
    member_account_statuses,
)
from app.modules.connectors.infrastructure.repositories.pod_connector_repository import (
    PodConnectorRepository,
)
from app.modules.connectors.services.pod_connector_resolution import (
    acting_person,
    connect_url,
)
from app.modules.pod.contracts.people import MAX_POD_PEOPLE, PodPerson

logger = get_logger(__name__)


class MemberNotTold(Exception):
    """The line to one member could not be posted: their channel refused it."""


class PodPeople(Protocol):
    async def __call__(
        self, uow: SqlAlchemyUnitOfWork, pod_id: UUID, *, limit: int
    ) -> list[PodPerson]: ...


class MemberContext(Protocol):
    """A member's own authority in the pod, as a request of theirs would hold it."""

    async def __call__(
        self, uow: SqlAlchemyUnitOfWork, *, user_id: UUID, pod_id: UUID
    ) -> Context: ...


class AskLedger(Protocol):
    """Once a day per connector per member."""

    async def asked_today(
        self, *, pod_connector_id: UUID, user_id: UUID
    ) -> bool | None: ...

    async def mark(self, *, pod_connector_id: UUID, user_id: UUID) -> None: ...


class TellMember(Protocol):
    """One line in a member's own channel with the pod, said for ``asked_by``."""

    async def __call__(
        self,
        uow: SqlAlchemyUnitOfWork,
        *,
        pod_id: UUID,
        user_id: UUID,
        asked_by: UUID,
        text: str,
        key: str,
    ) -> None: ...


#: The catalog title of an installation's connector -- "Gmail" -- if known.
ConnectorTitle = Callable[[UUID], Awaitable[str | None]]


class PodConnectorCoverageService:
    def __init__(
        self,
        *,
        uow: SqlAlchemyUnitOfWork,
        pod_connectors: PodConnectorRepository,
        people: PodPeople,
        member_context: MemberContext,
        ledger: AskLedger,
        tell: TellMember,
        connector_title: ConnectorTitle,
    ):
        self.uow = uow
        self.pod_connectors = pod_connectors
        self._people = people
        self._member_context = member_context
        self._ledger = ledger
        self._tell = tell
        self._connector_title = connector_title

    # -- coverage -------------------------------------------------------------

    async def coverage(
        self, ctx: Context, pod_id: UUID, slug: str
    ) -> PodConnectorCoverage:
        """Who of the members allowed to use a PER_MEMBER connector has
        connected an account. Editors only (``pod.connector.manage``)."""
        connector = await self._per_member_connector(ctx, pod_id, slug)
        return await self._count(pod_id, connector)

    async def _per_member_connector(
        self, ctx: Context, pod_id: UUID, slug: str
    ) -> PodConnectorEntity:
        await ctx.require(Permissions.POD_CONNECTOR_MANAGE, ResourceRef.pod(pod_id))
        connector = await self.pod_connectors.get_by_slug(pod_id, slug)
        if connector is None:
            raise PodConnectorNotFoundError(slug)
        if connector.mode is not PodConnectorMode.PER_MEMBER:
            raise PodConnectorValidationError(
                "Only a connector that uses each person's own account waits on "
                "members to connect.",
                reason="not_per_member",
            )
        return connector

    async def _count(
        self, pod_id: UUID, connector: PodConnectorEntity
    ) -> PodConnectorCoverage:
        people = await self._people(self.uow, pod_id, limit=MAX_POD_PEOPLE + 1)
        truncated = len(people) > MAX_POD_PEOPLE
        people = people[:MAX_POD_PEOPLE]
        ref = ResourceRef.connector(pod_id, connector.id)
        eligible: list[PodPerson] = []
        for person in people:
            member = await self._member_context(
                self.uow, user_id=person.user_id, pod_id=pod_id
            )
            if await member.can(Permissions.CONNECTOR_USE, ref):
                eligible.append(person)
        statuses = await member_account_statuses(
            self.uow.session,
            pod_id=pod_id,
            auth_config_id=connector.auth_config_id,
            user_ids=[person.user_id for person in eligible],
        )
        waiting_on = [
            MemberAwaited(
                user_id=person.user_id,
                pod_member_id=person.pod_member_id,
                name=person.name,
                email=person.email,
                standing=standing,
            )
            for person in eligible
            if (standing := _standing(statuses.get(person.user_id)))
            is not MemberConnectStanding.CONNECTED
        ]
        return PodConnectorCoverage(
            eligible=len(eligible),
            connected=len(eligible) - len(waiting_on),
            waiting_on=waiting_on,
            truncated=truncated,
        )

    # -- asking ---------------------------------------------------------------

    async def ask_members(
        self,
        ctx: Context,
        pod_id: UUID,
        slug: str,
        *,
        user_ids: list[UUID] | None = None,
    ) -> AskMembersResponse:
        """Ask the members a per-member connector is waiting on to connect.

        Each is asked once a day at most: an editor pressing the button twice,
        or two editors pressing it, asks nobody twice.
        """
        connector = await self._per_member_connector(ctx, pod_id, slug)
        editor = acting_person(ctx)
        if editor is None:
            raise PodConnectorValidationError(
                "Asking members is a person's act.", reason="requires_person"
            )
        waiting_on = (await self._count(pod_id, connector)).waiting_on
        if user_ids is not None:
            chosen = set(user_ids)
            waiting_on = [one for one in waiting_on if one.user_id in chosen]
        link = connect_url(pod_id, connector.slug)
        title = await self._connector_title(connector.auth_config_id) or connector.slug
        # By its own name: a pod with several connectors on one app (three on
        # one REST API) would otherwise ask everyone about "OpenAPI".
        called = title if title == connector.slug else f"{connector.slug} ({title})"
        text = f"This pod uses {called} — connect yours: {link}"
        day = datetime.now(UTC).date().isoformat()
        asked: list[MemberAwaited] = []
        recently: list[MemberAwaited] = []
        unreached: list[MemberAwaited] = []
        for member in waiting_on:
            already = await self._ledger.asked_today(
                pod_connector_id=connector.id, user_id=member.user_id
            )
            if already is None:
                unreached.append(member)
                continue
            if already:
                recently.append(member)
                continue
            if await self._told(
                pod_id=pod_id,
                member=member,
                editor=editor,
                text=text,
                key=f"connect-ask:{connector.id}:{member.user_id}:{day}",
            ):
                asked.append(member)
            else:
                unreached.append(member)
        self._mark_once_committed(connector.id, asked)
        return AskMembersResponse(
            asked=asked,
            asked_recently=recently,
            not_asked=unreached,
            connect_url=link,
        )

    async def _told(
        self,
        *,
        pod_id: UUID,
        member: MemberAwaited,
        editor: UUID,
        text: str,
        key: str,
    ) -> bool:
        """Post one member's line in a savepoint of its own, so one member's
        channel failing neither aborts the others nor marks them asked."""
        try:
            async with self.uow.session.begin_nested():
                await self._tell(
                    self.uow,
                    pod_id=pod_id,
                    user_id=member.user_id,
                    asked_by=editor,
                    text=text,
                    key=key,
                )
        except MemberNotTold, DomainError, SQLAlchemyError:
            logger.warning(
                "connectors.connect_ask.post_failed.degraded",
                pod_id=str(pod_id),
                exc_info=True,
            )
            return False
        return True

    def _mark_once_committed(
        self, pod_connector_id: UUID, asked: list[MemberAwaited]
    ) -> None:
        if not asked:
            return
        ledger = self._ledger

        async def mark() -> None:
            for member in asked:
                await ledger.mark(
                    pod_connector_id=pod_connector_id, user_id=member.user_id
                )

        self.uow.after_commit(mark)


def _standing(status: str | None) -> MemberConnectStanding:
    if status is None:
        return MemberConnectStanding.MISSING
    if status == AccountStatus.CONNECTED.value:
        return MemberConnectStanding.CONNECTED
    # REAUTH_REQUIRED, and a DISCONNECTED account: either way its owner signs
    # in again rather than connecting a new one.
    return MemberConnectStanding.REAUTH_REQUIRED


__all__ = [
    "AskLedger",
    "MemberContext",
    "MemberNotTold",
    "PodConnectorCoverageService",
    "PodPeople",
    "TellMember",
]
