"""The Lemma an organization is made with, and putting a person into it.

Every new organization opens on a Lemma everyone in it shares, the way a chat
app's first channel is one everybody is in: open to the organization's members
and joined by everyone who arrives later (``auto_join_new_members``). Made here,
rather than by each way in, so the web, a plugin's install page and a chat
sign-up all leave behind the same pod -- the one the web app made through
``POST /pods`` before sign-up moved to the server.

"The organization's Lemma" is not a column. It is the oldest live pod that is
open to the organization and takes in its newcomers, which is what the one made
here is until somebody closes it -- and an older organization that turned a pod
into one by hand has one too.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.contracts.provisioning import ensure_pod_default_agent
from app.modules.pod.api.dependencies import (
    get_pod_join_request_service,
    get_pod_service,
)
from app.modules.pod.domain.errors import PodLimitReachedError
from app.modules.pod.domain.pod_entities import (
    PodConfig,
    PodCreationSource,
    PodEntity,
    PodJoinPolicy,
)
from app.modules.pod.infrastructure.open_pods import organization_lemma_sql

#: The face the web app gives a new organization's Lemma: the first of its
#: cast, served by the web app at this path. Kept identical so a Lemma made on
#: the server is indistinguishable from one the web app made.
ORGANIZATION_LEMMA_ICON_URL = "/teammates/cast/arch.webp"


@dataclass(frozen=True, slots=True)
class OrganizationLemma:
    pod_id: UUID
    name: str
    assistant_id: UUID
    created: bool


async def create_organization_lemma(
    uow: SqlAlchemyUnitOfWork,
    *,
    organization_id: UUID,
    owner_user_id: UUID,
    name: str,
    shared: bool = True,
) -> OrganizationLemma | None:
    """Make the first Lemma of a new organization, its maker as its admin.

    A team's is ``shared``: open to every member and joined by everyone who
    arrives later -- the organization's own Lemma. A workspace of one gets a
    Lemma of that person's alone, private like any other: somebody invited
    into the workspace later is not put into another person's own Lemma, and
    it is never the one a newcomer lands in.

    ``None`` when the maker's plan has no room for another pod: the
    organization stands without one, and its list of Lemmas offers to make one.
    """
    try:
        pod = await get_pod_service(uow).create_pod(
            PodEntity(
                user_id=owner_user_id,
                organization_id=organization_id,
                name=name,
                icon_url=ORGANIZATION_LEMMA_ICON_URL,
                config=(
                    PodConfig(
                        join_policy=PodJoinPolicy.ORG_MEMBERS,
                        auto_join_new_members=True,
                    )
                    if shared
                    else PodConfig()
                ),
            ),
            owner_user_id,
            source=PodCreationSource.PROVISIONED,
        )
    except PodLimitReachedError:
        # Refused before anything was written, so the transaction is intact.
        return None
    assistant_id = await ensure_pod_default_agent(
        uow, pod_id=pod.id, user_id=owner_user_id
    )
    return OrganizationLemma(pod.id, pod.name, assistant_id, True)


async def organization_lemma_id(
    uow: SqlAlchemyUnitOfWork, *, organization_id: UUID
) -> UUID | None:
    """Which pod is the organization's Lemma, or ``None`` if it has none."""
    found = (
        await uow.session.execute(organization_lemma_sql(organization_id))
    ).one_or_none()
    return found.id if found is not None else None


async def enter_organization_lemma(
    uow: SqlAlchemyUnitOfWork, *, organization_id: UUID, user_id: UUID, newcomer: bool
) -> OrganizationLemma | None:
    """The organization's Lemma, with this member in it, or ``None`` if it has none.

    A ``newcomer`` -- somebody whose membership of the organization this
    request made -- is added now rather than left to the arrival handler,
    because the caller answers with where they landed and a pod they are not
    yet in is not somewhere they can be. Anybody else is answered with it only
    if they are in it already: a member who left it chose to, and asking again
    where they work (a second sign-up in a chat, an install page) does not
    put them back.
    """
    found = (
        await uow.session.execute(organization_lemma_sql(organization_id))
    ).one_or_none()
    if found is None:
        return None
    member = await get_pod_join_request_service(uow).join_default_pod(
        pod_id=found.id, organization_id=organization_id, user_id=user_id, add=newcomer
    )
    if member is None:
        return None
    assistant_id = await ensure_pod_default_agent(uow, pod_id=found.id, user_id=user_id)
    return OrganizationLemma(found.id, found.name, assistant_id, False)
