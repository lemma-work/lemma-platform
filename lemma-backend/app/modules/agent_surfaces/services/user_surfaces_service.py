from __future__ import annotations

from dataclasses import dataclass
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
)
from app.modules.identity.contracts import UserPreferences, UserRepositoryPort


@dataclass(frozen=True)
class UserSurfaceGroup:
    """All of a user's surfaces for one platform, across every pod they belong
    to, with the platform's default (if set) and whether the choice is
    ambiguous (more than one surface → a conflict the user should resolve)."""

    platform: SurfacePlatform
    surfaces: list[AgentSurfaceEntity]
    default_surface_id: UUID | None
    conflict: bool


class UserSurfacesService:
    """Cross-pod, user-scoped surface listing + default-surface preference.

    Powers ``GET /surfaces/me`` and ``PUT /surfaces/me/default`` so a user
    reachable via a shared system bot/number in several orgs can see every
    surface that would answer them and pick a default when they conflict.
    """

    def __init__(
        self,
        *,
        surface_repository: SurfaceInstallationRepositoryPort,
        pod_membership_port: SurfacePodMembershipPort,
        user_repository: UserRepositoryPort,
    ):
        self._surfaces = surface_repository
        self._membership = pod_membership_port
        self._users = user_repository

    async def _load_preferences(self, user_id: UUID) -> UserPreferences:
        user = await self._users.get(user_id)
        if user is None or user.preferences is None:
            return UserPreferences()
        return user.preferences

    async def list_user_surfaces(self, user_id: UUID) -> list[UserSurfaceGroup]:
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

        groups: list[UserSurfaceGroup] = []
        for platform, surfaces in by_platform.items():
            surfaces.sort(key=lambda s: (s.created_at, s.id))
            groups.append(
                UserSurfaceGroup(
                    platform=platform,
                    surfaces=surfaces,
                    default_surface_id=preferences.default_surface_for(platform.value),
                    conflict=len(surfaces) > 1,
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

    async def clear_default_surface(
        self,
        *,
        user_id: UUID,
        platform: SurfacePlatform,
    ) -> UserPreferences:
        preferences = await self._load_preferences(user_id)
        updated = preferences.without_default_surface(platform.value)
        await self._users.set_preferences(user_id, updated)
        return updated
