"""What other modules may know about a pod's agents, in bulk.

Mirrors ``apps.contracts.pod_summaries``: the organization landing page wants
every pod's agents at once, and asking per pod is exactly the per-pod query
count that page exists to avoid.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from app.modules.agent.infrastructure.models import AgentModel


@dataclass(frozen=True, slots=True)
class PodAgentSummary:
    """An agent as a listing entry."""

    id: UUID
    name: str
    description: str | None
    icon_url: str | None


async def list_agent_summaries_by_pod(
    *,
    session,
    pod_ids: list[UUID],
) -> dict[UUID, list[PodAgentSummary]]:
    """Agents for every given pod, keyed by pod, in one query."""
    if not pod_ids:
        return {}
    rows = (
        await session.execute(
            select(
                AgentModel.id,
                AgentModel.pod_id,
                AgentModel.name,
                AgentModel.description,
                AgentModel.icon_url,
            )
            .where(AgentModel.pod_id.in_(pod_ids))
            .order_by(AgentModel.name)
        )
    ).all()

    summaries: dict[UUID, list[PodAgentSummary]] = defaultdict(list)
    for agent_id, pod_id, name, description, icon_url in rows:
        summaries[pod_id].append(
            PodAgentSummary(
                id=agent_id,
                name=name,
                description=description,
                icon_url=icon_url,
            )
        )
    return dict(summaries)
