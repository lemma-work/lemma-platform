"""The shared bot's surface in one pod, and the person's choice to be answered there.

The shared WhatsApp number (and the shared Telegram bot) belongs to no
workspace. A pod becomes reachable on it by having a SYSTEM-credential surface
for its own assistant, and a person is answered by that pod because their
preferences name that surface as their default for the platform. Two separate
facts, written by two separate functions here, because two callers need them in
different combinations: chat onboarding creates the surface and sets the
default in one go, and the web profile's "answers from" picker does the same
for a pod the person chooses after the fact.

Lifted out of `onboarding_workspace` so that second caller does not have to
import the whole signup flow to reach it.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.contracts.provisioning import ensure_pod_default_agent
from app.modules.agent_surfaces.domain.available_pods import AvailablePod
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    AgentSurfaceStatus,
    SurfaceCredentialMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.errors import AgentSurfaceError
from app.modules.agent_surfaces.infrastructure.adapters.routing_resolution_adapter import (
    SqlAlchemySurfaceRoutingResolutionAdapter,
)
from app.modules.agent_surfaces.infrastructure.models import AgentSurface
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.identity.contracts.onboarding import ChallengeRejected
from app.modules.identity.contracts.organizations import (
    organization_member_ids_for_user,
    organization_names,
)
from app.modules.identity.contracts.surfaces import (
    set_user_preferences,
    user_preferences,
)
from app.modules.pod.contracts.user_pods import list_attachable_pods


class SharedSurfaceUnavailable(ChallengeRejected):
    """This workspace cannot carry the shared bot, but another one might.

    Separate from every other refusal because it is the only one with a next
    step the person can take, and saying "pick another workspace" is not the
    same as letting them. Carries the pod that refused, so the list offered
    next does not lead with it.
    """

    def __init__(self, message: str, *, pod_id: UUID) -> None:
        super().__init__(message)
        self.pod_id = pod_id


def _is_shared(surface: AgentSurfaceEntity, assistant_id: UUID) -> bool:
    return (
        surface.account_id is None
        and surface.credential_mode == SurfaceCredentialMode.SYSTEM
        and surface.agent_id == assistant_id
    )


def _refusal(platform: SurfacePlatform, pod_id: UUID) -> SharedSurfaceUnavailable:
    return SharedSurfaceUnavailable(
        "That workspace's assistant already answers on "
        f"{platform.value.title()} through its own connection. "
        "Pick another workspace, or message it there.",
        pod_id=pod_id,
    )


async def pods_blocked_for_shared_bot(
    uow: SqlAlchemyUnitOfWork, *, pod_ids: list[UUID], platform: SurfacePlatform
) -> set[UUID]:
    """Which of these pods `ensure_shared_surface` would refuse, answered without writing.

    So a list of workspaces can leave out the ones that cannot carry the shared
    bot before anybody picks one. Offering a pod and then refusing it is two
    messages where none was needed -- and when it is the only pod, attaching it
    automatically would refuse with nobody having asked anything.

    One read for the whole list. The pod's own assistant shares the pod's id
    (`AgentRepository.create_pod_default`, pinned by a check constraint), which
    is what lets a single query ask about every pod's assistant at once. A pod
    with no surface of its assistant's on the platform is not blocked; nor is
    one where the assistant's surface is already the shared one.
    """
    if not pod_ids:
        return set()
    rows = await uow.session.execute(
        select(
            AgentSurface.pod_id, AgentSurface.account_id, AgentSurface.credential_mode
        ).where(
            AgentSurface.pod_id.in_(pod_ids),
            AgentSurface.agent_id == AgentSurface.pod_id,
            AgentSurface.surface_type == platform.value,
        )
    )
    has_shared: dict[UUID, bool] = {}
    for pod_id, account_id, credential_mode in rows:
        shared = account_id is None and credential_mode == SurfaceCredentialMode.SYSTEM
        has_shared[pod_id] = has_shared.get(pod_id, False) or shared
    return {pod_id for pod_id, shared in has_shared.items() if not shared}


async def ensure_shared_surface(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    assistant_id: UUID,
    platform: SurfacePlatform,
) -> AgentSurfaceEntity:
    """The pod's surface on the shared bot, made or reactivated if need be."""
    repository = SurfaceRepository(uow)
    surfaces, _ = await repository.list_by_pod(pod_id, platform=platform.value)
    surface = next(
        (item for item in surfaces if _is_shared(item, assistant_id)),
        None,
    )
    if surface is None:
        # An agent reaches a platform in one place, so if this one already has
        # a surface here that the lookup above did not match -- a bot on the
        # company's own credentials -- there is no second place to put the
        # shared one. Creating it anyway is what `uq_agent_surface_agent_type`
        # refuses, and an IntegrityError is a poor way to tell someone their
        # workspace is already reachable another way.
        #
        # Nor can the existing one simply be reused: shared routing considers
        # system-credential surfaces only, so a default pointing at a custom
        # bot is a default that is always ignored.
        if any(item.agent_id == assistant_id for item in surfaces):
            raise _refusal(platform, pod_id)
        surface = await repository.create(
            AgentSurfaceEntity.create(
                pod_id=pod_id,
                surface_type=platform,
                agent_id=assistant_id,
                name=f"lemma-{platform.value.lower()}-{str(assistant_id)[:8]}",
            )
        )
    if not surface.is_active or surface.status != AgentSurfaceStatus.ACTIVE:
        surface.activate()
        surface = await repository.update(surface)
    return surface


async def make_default(
    uow: SqlAlchemyUnitOfWork,
    *,
    user_id: UUID,
    platform: SurfacePlatform,
    surface_id: UUID,
) -> None:
    """Record that this surface answers the person on this platform.

    The same preference `PUT /surfaces/me/default` writes, and the one routing
    reads first -- so onboarding writing it whenever it attaches a pod is what
    keeps the router's oldest-pod tiebreak from ever deciding for somebody who
    was asked.
    """
    preferences = await user_preferences(uow, user_id)
    await set_user_preferences(
        uow,
        user_id,
        preferences.with_default_surface(platform.value, surface_id),
    )


#: How many pods the profile picker lists. A bound on the read, well past the
#: number of workspaces anybody actually switches between.
MAX_PICKER_PODS = 200


class SharedBotPods:
    """The web profile's side of "which pod answers me on the shared bot".

    Reads the person's pods -- every one they are a member of, whether or not
    it has a shared-bot surface yet, because "it has never been picked" is not a
    reason it cannot be -- and attaches one on request, making the surface the
    same way chat onboarding does. Built per request from the request's unit of
    work, so the attach and the preference write commit together.
    """

    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self._uow = uow

    async def member_pods(self, user_id: UUID) -> list[AvailablePod]:
        membership_ids = await organization_member_ids_for_user(
            self._uow, user_id=user_id
        )
        pods = await list_attachable_pods(
            session=self._uow.session,
            organization_member_ids=membership_ids,
            limit=MAX_PICKER_PODS,
        )
        names = await organization_names(
            self._uow, [pod.organization_id for pod in pods]
        )
        return [
            AvailablePod(pod.id, pod.name, names.get(pod.organization_id))
            for pod in pods
        ]

    async def attach(
        self, *, user_id: UUID, platform: SurfacePlatform, pod_id: UUID
    ) -> UUID:
        """The pod's shared-bot surface, made if need be; its id for the default.

        Membership is re-checked here rather than trusted from the listing the
        page was drawn from: access can be taken away between the two.
        """
        pod_ids = await SqlAlchemySurfaceRoutingResolutionAdapter(
            self._uow
        ).get_user_pod_ids(user_id)
        if pod_id not in set(pod_ids):
            raise AgentSurfaceError(
                f"Pod '{pod_id}' not found", code="POD_NOT_FOUND", status_code=404
            )
        assistant_id = await ensure_pod_default_agent(
            self._uow, pod_id=pod_id, user_id=user_id
        )
        try:
            surface = await ensure_shared_surface(
                self._uow, pod_id=pod_id, assistant_id=assistant_id, platform=platform
            )
        except SharedSurfaceUnavailable as conflict:
            raise AgentSurfaceError(
                conflict.message,
                code="AGENT_SURFACE_SHARED_BOT_UNAVAILABLE",
                status_code=409,
            ) from conflict
        return surface.id
