"""What other modules may know about a pod's agents, in bulk.

Mirrors ``apps.contracts.pod_summaries``: the organization landing page wants
every pod's agents at once, and asking per pod is exactly the per-pod query
count that page exists to avoid.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, func, or_, select

from app.core.authorization.context import Context, ResourceType
from app.core.authorization.permissions import Permissions
from app.core.authorization.sql_actions import (
    allowed_actions_contains,
    allowed_actions_expr,
)
from app.modules.agent.infrastructure.models import AgentModel


@dataclass(frozen=True, slots=True)
class PodAgentSummary:
    """An agent as a listing entry."""

    id: UUID
    name: str
    description: str | None
    icon_url: str | None


def readable_agents_ranked_by_pod(contexts: Mapping[UUID, Context]):
    """Agents each pod's own viewer may read, numbered within their pod.

    **A context per pod, not one context and a list of pod ids.** An agent
    carries its own ``visibility`` and ``user_id``, so "may this person read
    it" is the question ``allowed_actions_expr`` already answers -- but the
    role permissions that feed it are *pod-scoped*. One context built for pod A
    holds none of the caller's permissions in pod B, so asking it about B's
    rows filters out every pod-visible agent there. The mapping's keys are the
    pods to read, which is what stops a caller passing a pod without the
    context that authorizes it.

    The OR is what keeps this one statement. The landing page above exists to
    replace a fetch per pod, so the per-pod expressions are combined rather
    than run in a loop.

    A module-level builder, like ``apps.infrastructure.repositories.
    release_prefix_statement``: the row numbering is the whole reason a caller
    can cap each pod rather than the page, and a statement explained in one
    place and built in another drifts.
    """
    return (
        select(
            AgentModel.id.label("id"),
            AgentModel.pod_id.label("pod_id"),
            AgentModel.name.label("name"),
            AgentModel.description.label("description"),
            AgentModel.icon_url.label("icon_url"),
            func.row_number()
            .over(partition_by=AgentModel.pod_id, order_by=AgentModel.name)
            .label("rank"),
        )
        # Narrowed to the pods the caller already holds a context for, then to
        # the rows those contexts authorize. The first clause is implied by the
        # second, and is written out because the bound on this read should be
        # legible without evaluating an OR of per-pod expressions to find it.
        .where(
            AgentModel.pod_id.in_(list(contexts)),
            or_(
                *[
                    and_(
                        AgentModel.pod_id == pod_id,
                        allowed_actions_contains(
                            allowed_actions_expr(
                                ctx=ctx,
                                resource_type=ResourceType.AGENT,
                                resource_id_col=AgentModel.id,
                                pod_id_col=AgentModel.pod_id,
                                owner_user_id_col=AgentModel.user_id,
                                visibility_col=AgentModel.visibility,
                            ),
                            Permissions.AGENT_READ,
                        ),
                    )
                    for pod_id, ctx in contexts.items()
                ]
            ),
        )
        .subquery()
    )


async def list_agent_summaries_by_pod(
    *,
    session,
    contexts: Mapping[UUID, Context],
    limit: int,
) -> dict[UUID, list[PodAgentSummary]]:
    """Each pod's agents, filtered to what that pod's own viewer may read.

    Pods with no readable agents are absent rather than mapped to an empty
    list; callers read through ``.get(pod_id, [])`` so the difference never
    reaches a response.

    ``limit`` is required, and it caps each pod rather than the whole result:
    a single pod with a thousand agents must not crowd every other pod out of
    the page. A bare LIMIT would mean the pods sorting last silently lose their
    agents instead of showing fewer, which is why the rows arrive numbered
    within their pod (``readable_agents_ranked_by_pod``).
    """
    if not contexts:
        return {}

    ranked = readable_agents_ranked_by_pod(contexts)
    rows = (
        await session.execute(
            select(
                ranked.c.id,
                ranked.c.pod_id,
                ranked.c.name,
                ranked.c.description,
                ranked.c.icon_url,
            )
            .where(ranked.c.rank <= limit)
            .order_by(ranked.c.pod_id, ranked.c.name)
            # The rank already caps each pod, so this can only bind if that
            # went wrong. It is here because the ceiling on the whole read
            # should be a number in the statement rather than a property of a
            # window function someone has to reconstruct to be sure of.
            .limit(limit * len(contexts))
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
