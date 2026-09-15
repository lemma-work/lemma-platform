"""Reads behind AgentContextBriefBuilder.

Keeps the brief builder SQLAlchemy-free; it aggregates read-only display data
across pod, identity, workflow, schedule, apps, surfaces and core authorization,
so the raw queries live here.

Everything past the grants is what the agent needs to describe *itself*: the pod
it belongs to, the standing work wired to it, the apps it runs and the channels
it answers on. Each comes from the owning module's own contract rather than from
a join written here, so this file stays a list of one-line delegations and no
module's table layout leaks into `agent`.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from app.core.authorization.context import ResourceType
from app.core.authorization.models import ResourcePermissionGrantModel
from app.core.authorization.resource_names import resolve_resource_names_by_ids
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.contracts.pod_summaries import (
    PodSurfaceSummary,
    list_surface_summaries,
)
from app.modules.apps.contracts.pod_summaries import (
    PodAppSummary,
    list_app_summaries_by_pod,
)
from app.modules.identity.contracts.profiles import user_profile
from app.modules.pod.contracts.members import PodProfile, pod_name, pod_profile
from app.modules.schedule.contracts.pod_summaries import (
    PodScheduleSummary,
    list_schedule_summaries,
)
from app.modules.workflow.contracts.pod_summaries import (
    PodWorkflowSummary,
    list_workflow_summaries,
)


@dataclass(frozen=True, slots=True)
class UserProfile:
    """The identity fields the runtime brief puts in front of the agent.

    A name and a timezone, not only an address, because the brief is the
    agent's only source for either: it addresses the person by what it reads
    here, and every clock it is handed reads UTC.
    """

    email: str | None = None
    display_name: str | None = None
    timezone: str | None = None


class AgentContextBriefRepository:
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self._session = uow.session

    async def get_pod_name(self, pod_id: UUID) -> str | None:
        return await pod_name(self._session, pod_id)

    async def get_pod_profile(self, pod_id: UUID) -> PodProfile:
        return await pod_profile(self._session, pod_id)

    async def list_workflows(
        self, *, pod_id: UUID, limit: int
    ) -> tuple[list[PodWorkflowSummary], int]:
        return await list_workflow_summaries(
            session=self._session, pod_id=pod_id, limit=limit
        )

    async def list_schedules(
        self, *, pod_id: UUID, limit: int
    ) -> tuple[list[PodScheduleSummary], int]:
        return await list_schedule_summaries(
            session=self._session, pod_id=pod_id, limit=limit
        )

    async def list_apps(self, *, pod_id: UUID) -> list[PodAppSummary]:
        """This pod's apps.

        The apps contract answers for many pods at once, because the page that
        drove it needed that; one pod is the degenerate case rather than a
        second query worth writing.
        """
        by_pod = await list_app_summaries_by_pod(
            session=self._session, pod_ids=[pod_id]
        )
        return by_pod.get(pod_id, [])

    async def list_surfaces(
        self, *, pod_id: UUID, limit: int
    ) -> list[PodSurfaceSummary]:
        return await list_surface_summaries(
            session=self._session, pod_id=pod_id, limit=limit
        )

    async def get_user_profile(self, user_id: UUID) -> UserProfile:
        """Name, address and timezone in one read.

        The row was already being fetched whole for the address alone, so the
        other two are free -- and an empty profile for a missing user rather
        than a raise, because a brief is still worth rendering without one.
        """
        user = await user_profile(self._session, user_id)
        if user is None:
            return UserProfile()
        name = " ".join(
            part.strip() for part in (user.first_name, user.last_name) if part
        ).strip()
        return UserProfile(
            email=user.email, display_name=name or None, timezone=user.timezone
        )

    async def get_agent_grants(
        self, *, pod_id: UUID, agent_id: UUID
    ) -> list[tuple[str, UUID, str]]:
        """(resource_type, resource_id, permission_id) granted to an agent."""
        rows = (
            await self._session.execute(
                select(
                    ResourcePermissionGrantModel.resource_type,
                    ResourcePermissionGrantModel.resource_id,
                    ResourcePermissionGrantModel.permission_id,
                ).where(
                    ResourcePermissionGrantModel.pod_id == pod_id,
                    ResourcePermissionGrantModel.grantee_type == "AGENT",
                    ResourcePermissionGrantModel.grantee_id == agent_id,
                )
            )
        ).all()
        return [(rt, rid, pid) for rt, rid, pid in rows]

    async def resolve_resource_names(
        self, *, pod_id: UUID, refs: list[tuple[ResourceType, UUID]]
    ) -> dict:
        return await resolve_resource_names_by_ids(
            self._session, pod_id=pod_id, refs=refs
        )
