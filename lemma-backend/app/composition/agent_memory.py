"""The `/memory` folder and grant an agent's MEMORY toolset implies.

The derivation itself lives in
``agent.services.agent_memory_grant.sync_memory_folder_grant``. This wrapper
exists so that *every* caller who makes an agent gets it, not only the ones who
remember to ask.

That distinction was a live bug. The sync was called from the agent HTTP
controller and nowhere else, so an agent created straight through
``AgentService`` -- which is what the pod bundle applier does -- got MEMORY in
its toolsets and no folder to write to. Dormant until MEMORY became a default
for new agents (#476), and then fatal: the source agent held ``folder:/memory``,
the export recorded the grant, and applying it against a pod where nothing had
created that folder failed the whole import with ``Unknown resource name(s):
folder:/memory``.

Best-effort, and that is the reason the swallow lives here rather than in
``agent_service``. Creating or editing an agent must not fail because a folder
could not be provisioned -- the agent is still perfectly usable, and the grant
comes back on the next save, since it is re-derived every time. Putting the
``except`` in ``app/composition`` also keeps it out of the architecture
ratchet's broad-catch budget for the ``agent`` module, which has one unit of
headroom and better uses for it.

**This is a floor, not the whole story.** An inline ``permissions`` list
*replaces* every grant a grantee holds, so a derived grant applied before one of
those is the first thing wiped. The three call sites that run after a replace --
two in ``agent_controller`` and ``BundleApplier._apply_agent_grants`` -- must
keep re-deriving. What this removes is the need for a caller to know that in
order to get the ordinary case right.
"""

from __future__ import annotations

from uuid import UUID

from app.core.log.log import get_logger

logger = get_logger(__name__)


async def derive_agent_memory_grant(
    uow,
    *,
    pod_id: UUID,
    agent_id: UUID,
    toolsets: object,
    ctx: object,
    created_by_user_id: UUID,
) -> None:
    """Add or remove this agent's `/memory` grant to match its toolsets."""
    from app.modules.agent.services.agent_memory_grant import sync_memory_folder_grant

    try:
        await sync_memory_folder_grant(
            uow,
            pod_id=pod_id,
            agent_id=agent_id,
            toolsets=toolsets,  # type: ignore[arg-type]
            ctx=ctx,  # type: ignore[arg-type]
            created_by_user_id=created_by_user_id,
        )
    except Exception:  # noqa: BLE001
        # The agent is the thing the caller asked for; the folder is a
        # convenience the next save re-derives.
        logger.warning("agent.memory.derivation_failed.degraded")
