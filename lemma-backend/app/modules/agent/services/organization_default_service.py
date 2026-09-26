"""Choosing, and un-choosing, the model an organization's teammates run on.

The mark itself is plain metadata (`domain/organization_default`); this is the
write side, which has to keep "at most one profile carries it" true. Setting
walks every organization-wide profile, archived ones included, because a mark
left on an archived profile would come back to life on restore.
"""

from __future__ import annotations

from uuid import UUID

from app.modules.agent.domain.organization_default import (
    ORGANIZATION_WIDE_VIEWER,
    is_marked_default,
    organization_default_of,
    with_default_mark,
    without_default_mark,
)
from app.modules.agent.domain.runtime_profiles import AgentRuntimeProfile
from app.modules.agent.domain.value_objects import AgentRuntimeConfig
from app.modules.agent.infrastructure.repositories import (
    AgentRuntimeProfileRepository,
)


class OrganizationDefaultNotFoundError(LookupError):
    """The profile named is not one of this organization's."""


class OrganizationDefaultService:
    """Set or clear the organization's default model."""

    def __init__(self, repository: AgentRuntimeProfileRepository) -> None:
        self._repository = repository

    async def set_default(
        self,
        *,
        organization_id: UUID,
        profile_id: str,
        model_name: str | None,
    ) -> AgentRuntimeConfig:
        """Mark ``profile_id`` and unmark every other profile.

        Raises `OrganizationDefaultNotFoundError` when the profile is not an
        organization-wide one here (a personal key is not visible to the
        organization-wide read, so it lands here too), and ``ValueError`` when
        it is visible but cannot be the default.
        """
        profiles = await self._organization_profiles(organization_id)
        target = next((item for item in profiles if item.id == profile_id), None)
        if target is None:
            raise OrganizationDefaultNotFoundError(profile_id)
        marked = await self._repository.update(
            with_default_mark(target, model_name=model_name)
        )
        for profile in profiles:
            if profile.id != profile_id and is_marked_default(profile):
                await self._repository.update(without_default_mark(profile))
        chosen = organization_default_of([marked])
        if chosen is None:
            # with_default_mark refuses anything organization_default_of would
            # skip, so this is a broken invariant rather than a caller mistake.
            raise RuntimeError("The organization default did not take")
        return chosen

    async def clear_default(self, *, organization_id: UUID) -> None:
        """Remove the mark wherever it is; a no-op when nothing is chosen."""
        for profile in await self._organization_profiles(organization_id):
            if is_marked_default(profile):
                await self._repository.update(without_default_mark(profile))

    async def _organization_profiles(
        self, organization_id: UUID
    ) -> list[AgentRuntimeProfile]:
        return await self._repository.get_visible(
            organization_id=organization_id,
            user_id=ORGANIZATION_WIDE_VIEWER,
            include_disabled=True,
        )


__all__ = ["OrganizationDefaultNotFoundError", "OrganizationDefaultService"]
