"""Connecting pods: one pod letting another ask it, with nobody present.

The storage is grants (``infrastructure/pod_link_queries``); this is who may
make one and what it may share.

* **Who connects.** An admin of the pod being asked, who is also in the pod that
  will ask. The route checks the first (``pod.member.manage`` on the asked pod:
  letting another pod in is the same call as letting a person in); this checks
  the second, so nobody links a pod they cannot see into one they run.
* **What it shares.** Tables and folders, read-only. A link run reaches this
  pod through the outsider machinery, which writes nothing, so a write grant
  would promise what no run could use.
* **Who looks after it.** Whoever connected it. They own the conversations the
  asks open, get the questions the run passes on, and the link stops working
  once they could no longer connect it: out of either pod, or no longer an
  admin of this one (``PodAskService._still_stewards``).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from uuid import UUID

from app.core.authorization.authorizer import Authorizer
from app.core.authorization.context import (
    Context,
    ResourceRef,
    ResourceType,
    ResourceVisibility,
)
from app.core.authorization.grants import (
    NormalizedResourceGrant,
    ResourceGrantInputProtocol,
    normalize_pod_resource_grants,
    validate_pod_resource_grant_permissions,
)
from app.core.authorization.permissions import Permissions
from app.core.domain.errors import BadRequestError, DomainError
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent.infrastructure.pod_ask_queries import PodAskQueries
from app.modules.agent.infrastructure.repositories import (
    AgentRepository,
    ConversationRepository,
)
from app.modules.agent.services.conversation_service import ConversationService
from app.modules.agent.infrastructure.pod_link_queries import (
    LinkRow,
    PodLinkQueries,
    SharedResource,
)
from app.modules.identity.contracts.profiles import UserProfileRef, user_profile
from app.modules.pod.contracts.agent_access import pod_organization_id
from app.modules.pod.contracts.members import pod_member_id
from app.modules.pod.contracts.user_pods import AttachablePod, pods_by_ids
from app.modules.usage.contracts.execution import build_usage_service

logger = get_logger(__name__)

#: What a link may share, per kind of resource: reading, and nothing else.
SHAREABLE: dict[ResourceType, frozenset[str]] = {
    ResourceType.DATASTORE_TABLE: frozenset(
        {Permissions.DATASTORE_TABLE_READ, Permissions.DATASTORE_RECORD_READ}
    ),
    ResourceType.FOLDER: frozenset({Permissions.FOLDER_READ}),
    ResourceType.DOCUMENT: frozenset({Permissions.FOLDER_READ}),
}


class LinkRefused(DomainError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="pod_link_refused", status_code=409)


@dataclass(frozen=True, slots=True)
class PodLinkView:
    """A link as a page shows it: the other pod, who looks after it, what it shares."""

    pod: AttachablePod
    steward_user_id: UUID | None
    steward_name: str | None
    shared: list[SharedResource]


class PodLinkService:
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow
        self.links = PodLinkQueries(uow)

    async def connect(
        self,
        ctx: Context,
        *,
        answering_pod_id: UUID,
        asking_pod_id: UUID,
        user_id: UUID,
        shares: Sequence[ResourceGrantInputProtocol],
    ) -> None:
        """Let ``asking_pod_id`` ask ``answering_pod_id``, sharing ``shares``."""
        if answering_pod_id == asking_pod_id:
            raise LinkRefused("A pod can't be connected to itself.")
        organization = await pod_organization_id(self.uow, answering_pod_id)
        if (
            organization is None
            or await pod_organization_id(self.uow, asking_pod_id) != organization
        ):
            raise LinkRefused("Only pods in the same organization can be connected.")
        if await pod_member_id(self.uow, asking_pod_id, user_id) is None:
            raise LinkRefused(
                "You can only connect a pod you're in. Ask someone in it to connect."
            )
        _refuse_beyond_reading(shares)
        validate_pod_resource_grant_permissions(shares)
        normalized = await normalize_pod_resource_grants(
            self.uow.session, pod_id=answering_pod_id, grants=shares
        )
        await self._refuse_personal(normalized)
        await self.links.connect(
            ctx,
            answering_pod_id=answering_pod_id,
            asking_pod_id=asking_pod_id,
            shares=normalized,
            steward_user_id=user_id,
        )

    async def _refuse_personal(self, shares: Sequence[NormalizedResourceGrant]) -> None:
        """Refuse sharing somebody's own table or folder -- a /me folder, say.

        No grant would reach it (``Authorizer._pod_decision`` refuses PERSONAL
        first), so the link would list a share that gives nothing.
        """
        authorizer = Authorizer(self.uow.session)
        for share in shares:
            hydrated = await authorizer.hydrate_resource_ref(
                ResourceRef(
                    resource_type=share.resource_type, resource_id=share.resource_id
                )
            )
            if hydrated.visibility == ResourceVisibility.PERSONAL:
                raise BadRequestError(
                    "A link can't share somebody's own table or folder.",
                    code="POD_LINK_PERSONAL",
                )

    async def disconnect(self, *, answering_pod_id: UUID, asking_pod_id: UUID) -> bool:
        """Take the link away, and stop its asks. False when there was none.

        The grants go first, so a run still answering reads nothing it was
        shared from here on. Each of its threads still working is then stopped;
        one that finishes anyway is never delivered (``settled_answer``).
        """
        if (
            await self.links.link(
                answering_pod_id=answering_pod_id, asking_pod_id=asking_pod_id
            )
            is None
        ):
            return False
        await self.links.disconnect(
            answering_pod_id=answering_pod_id, asking_pod_id=asking_pod_id
        )
        await self._stop_asks(
            answering_pod_id=answering_pod_id, asking_pod_id=asking_pod_id
        )
        return True

    async def _stop_asks(self, *, answering_pod_id: UUID, asking_pod_id: UUID) -> None:
        threads = await PodAskQueries(self.uow).busy_link_threads(
            to_pod_id=answering_pod_id, from_pod_id=asking_pod_id
        )
        conversations = ConversationService(
            uow=self.uow,
            conversation_repository=ConversationRepository(self.uow),
            agent_repository=AgentRepository(self.uow),
            usage_service=build_usage_service(self.uow),
        )
        for conversation_id, owner in threads:
            try:
                await conversations.stop_conversation(
                    conversation_id=conversation_id,
                    user_id=owner,
                    pod_id=answering_pod_id,
                )
            except DomainError:
                # Its answer is withheld either way; a run left going costs
                # only what it reads, and what it reads is Public now.
                logger.warning(
                    "agent.pod_link.stop_ask_failed.degraded",
                    conversation_id=str(conversation_id),
                    exc_info=True,
                )

    async def incoming(self, *, answering_pod_id: UUID) -> list[PodLinkView]:
        """The pods that may ask this one, and what each was shared."""
        rows = await self.links.incoming(answering_pod_id=answering_pod_id)
        return await self._views(rows, other=lambda row: row.asking_pod_id)

    async def outgoing(self, *, asking_pod_id: UUID) -> list[PodLinkView]:
        """The pods this one may ask, and what each shares with it."""
        rows = await self.links.outgoing(asking_pod_id=asking_pod_id)
        return await self._views(rows, other=lambda row: row.answering_pod_id)

    async def _views(
        self, rows: list[LinkRow], *, other: Callable[[LinkRow], UUID]
    ) -> list[PodLinkView]:
        pods = {
            pod.id: pod
            for pod in await pods_by_ids(
                session=self.uow.session, pod_ids=[other(row) for row in rows]
            )
        }
        views: list[PodLinkView] = []
        for row in rows:
            pod = pods.get(other(row))
            if pod is None:
                continue
            steward = (
                await user_profile(self.uow.session, row.steward_user_id)
                if row.steward_user_id
                else None
            )
            views.append(
                PodLinkView(
                    pod=pod,
                    steward_user_id=row.steward_user_id,
                    steward_name=_name(steward),
                    shared=await self.links.shared(
                        answering_pod_id=row.answering_pod_id,
                        asking_pod_id=row.asking_pod_id,
                    ),
                )
            )
        return views


def _name(profile: UserProfileRef | None) -> str | None:
    if profile is None:
        return None
    full = " ".join(p for p in (profile.first_name, profile.last_name) if p).strip()
    return full or profile.email


def _refuse_beyond_reading(shares: Sequence[ResourceGrantInputProtocol]) -> None:
    for share in shares:
        allowed = SHAREABLE.get(share.resource_type)
        if allowed is None:
            raise BadRequestError(
                f"A link can share tables and folders, not {share.resource_type.value}.",
                code="POD_LINK_UNSHAREABLE",
            )
        beyond = set(share.permission_ids) - allowed
        if beyond:
            raise BadRequestError(
                "A link shares for reading only: "
                + ", ".join(sorted(beyond))
                + " can't be shared.",
                code="POD_LINK_READ_ONLY",
            )
