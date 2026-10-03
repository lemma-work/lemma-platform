from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.errors import (
    AgentSurfaceNotFoundError,
    AgentSurfaceValidationError,
)
from app.modules.agent_surfaces.domain.ports import (
    SurfaceInstallationRepositoryPort,
    SurfacePodMembershipPort,
    SurfaceUserDirectoryPort,
)
from app.modules.agent_surfaces.platforms.platform_capabilities import (
    has_shared_system_bot,
)
from app.modules.agent_surfaces.services.credential_resolver import (
    has_native_credentials,
)
from app.modules.agent_surfaces.domain.available_pods import AvailablePod
from app.modules.agent_surfaces.services.surface_address import (
    contended_surface_ids,
)
from app.modules.identity.contracts import UserPreferences


class SharedBotPodsPort(Protocol):
    """The person's pods on a shared bot: what can be picked, and picking one.

    A port because it writes -- attaching a pod makes its shared-bot surface --
    and that needs the request's unit of work, which the listing service below
    was never given.
    """

    async def member_pods(self, user_id: UUID) -> list[AvailablePod]: ...

    async def attach(
        self, *, user_id: UUID, platform: SurfacePlatform, pod_id: UUID
    ) -> UUID: ...


@dataclass(frozen=True)
class UserSurfaceGroup:
    """All of a user's surfaces for one platform, across every pod they belong
    to, with the platform's default (if set) and which of them the user actually
    has to choose between.

    ``contended`` holds the surfaces sharing one address — the deployment's
    shared bot/number fronting several pods. More than one surface on a platform
    is not by itself ambiguous: a pod's own bot has its own handle, and a message
    to it can only ever arrive there."""

    platform: SurfacePlatform
    surfaces: list[AgentSurfaceEntity]
    default_surface_id: UUID | None
    contended: set[UUID]
    #: On a shared-bot platform, every pod the person may be answered from --
    #: including pods with no surface here yet, which picking one creates.
    #: Empty elsewhere: a pod's own bot is not something to choose between.
    available_pods: list[AvailablePod] = field(default_factory=list)

    @property
    def default_pod_id(self) -> UUID | None:
        """The pod the default surface belongs to, while it is still one of theirs."""
        return next(
            (
                surface.pod_id
                for surface in self.surfaces
                if surface.id == self.default_surface_id
            ),
            None,
        )

    @property
    def conflict(self) -> bool:
        """Whether the user has a routing choice to make on this platform."""
        return bool(self.contended)


class UserSurfacesService:
    """Cross-pod, user-scoped surface listing + default-surface preference.

    Powers ``GET /surfaces/me`` and ``PUT /surfaces/me/default`` so a user
    reachable via a shared system bot/number in several orgs can see every
    surface that would answer them and pick a default when they share one
    address (see ``surface_address``).
    """

    def __init__(
        self,
        *,
        surface_repository: SurfaceInstallationRepositoryPort,
        pod_membership_port: SurfacePodMembershipPort,
        user_directory: SurfaceUserDirectoryPort,
    ):
        self._surfaces = surface_repository
        self._membership = pod_membership_port
        self._users = user_directory

    async def _load_preferences(self, user_id: UUID) -> UserPreferences:
        return await self._users.preferences(user_id)

    async def list_user_surfaces(
        self, user_id: UUID, *, shared_pods: SharedBotPodsPort | None = None
    ) -> list[UserSurfaceGroup]:
        """Every surface across the person's pods, grouped by platform.

        With `shared_pods`, each platform that has a shared bot in this
        deployment also lists the pods that could answer there -- and gets a
        group even when nothing answers yet, which is exactly when the person
        needs one to pick from.
        """
        pod_ids = await self._membership.get_user_pod_ids(user_id)
        preferences = await self._load_preferences(user_id)

        by_platform: dict[SurfacePlatform, list[AgentSurfaceEntity]] = {}
        for pod_id in pod_ids:
            cursor: UUID | None = None
            while True:
                surfaces, cursor = await self._surfaces.list_by_pod(
                    pod_id, cursor=cursor
                )
                for surface in surfaces:
                    by_platform.setdefault(surface.surface_type, []).append(surface)
                if cursor is None:
                    break

        available: list[AvailablePod] = []
        if shared_pods is not None:
            available = await shared_pods.member_pods(user_id)
            for platform in _shared_bot_platforms():
                by_platform.setdefault(platform, [])

        groups: list[UserSurfaceGroup] = []
        for platform, surfaces in by_platform.items():
            surfaces.sort(key=lambda s: (s.created_at, s.id))
            groups.append(
                UserSurfaceGroup(
                    platform=platform,
                    surfaces=surfaces,
                    default_surface_id=preferences.default_surface_for(platform.value),
                    contended=contended_surface_ids(surfaces),
                    available_pods=available if has_shared_system_bot(platform) else [],
                )
            )
        groups.sort(key=lambda g: g.platform.value)
        return groups

    async def set_default_surface(
        self,
        *,
        user_id: UUID,
        platform: SurfacePlatform,
        surface_id: UUID,
    ) -> UserPreferences:
        """Save the person's choice of which surface answers them on a platform.

        Nothing else is written. A verified personal route is not touched: routing
        asks the router for a deliverable default before it uses the route (see
        `SurfaceRouter.deliverable_default`), so the choice takes effect on the
        next message and the route answers again if the default goes stale --
        rather than the route being deleted here and lost for good.
        """
        surface = await self._surfaces.get(surface_id)
        if surface is None:
            raise AgentSurfaceNotFoundError(str(surface_id))
        if surface.surface_type is not platform:
            raise AgentSurfaceValidationError(
                "Surface platform does not match the requested default platform."
            )
        pod_ids = set(await self._membership.get_user_pod_ids(user_id))
        if surface.pod_id not in pod_ids:
            # Don't leak existence of surfaces in pods the user can't see.
            raise AgentSurfaceNotFoundError(str(surface_id))

        preferences = await self._load_preferences(user_id)
        updated = preferences.with_default_surface(platform.value, surface_id)
        await self._users.set_preferences(user_id, updated)
        return updated

    async def set_default_pod(
        self,
        *,
        user_id: UUID,
        platform: SurfacePlatform,
        pod_id: UUID,
        shared_pods: SharedBotPodsPort,
    ) -> UserPreferences:
        """Be answered from this pod on the shared bot -- even if it has no surface yet.

        The same preference `set_default_surface` writes, reached from a pod
        rather than a surface: the person picks a workspace, not a row they
        have never seen. Picking one with no surface on the platform makes it,
        exactly as chat onboarding would; a pod whose assistant already answers
        there through a connection of its own is refused with that reason.
        """
        if not has_shared_system_bot(platform.value):
            raise AgentSurfaceValidationError(
                f"{platform.value.title()} has no shared bot to choose a pod for."
            )
        surface_id = await shared_pods.attach(
            user_id=user_id, platform=platform, pod_id=pod_id
        )
        preferences = await self._load_preferences(user_id)
        updated = preferences.with_default_surface(platform.value, surface_id)
        await self._users.set_preferences(user_id, updated)
        return updated


def _shared_bot_platforms() -> list[SurfacePlatform]:
    """The platforms where this deployment runs a shared bot people can pick a pod on."""
    return [
        platform
        for platform in SurfacePlatform
        if has_shared_system_bot(platform.value) and has_native_credentials(platform)
    ]
