"""``set_what_you_own``: the pod's agent sets what the pod owns.

The same path a person's ``PUT /pods/{pod_id}/outcome`` takes, as the agent's
authority for this call: it must be able to update the pod, and a new or
changed measure is read once before it is kept, which also means the agent
can only count rows it can read in full. Deferred, because setting what the
pod owns happens once in the setup chat and rarely after -- no reason to carry
the schema on every turn.
"""

from __future__ import annotations

from pydantic_ai.tools import RunContext

from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.pod.pod_common import run_pod_tool
from app.modules.agent.tools.pod.pod_data_access import PodServices
from app.modules.datastore.contracts.outcome_measures import build_outcome_measures
from app.modules.pod.contracts.outcome import OutcomeInput, set_pod_outcome

TOOL_NAME = "set_what_you_own"


async def set_what_you_own(
    ctx: RunContext[BaseAgentContext],
    request: OutcomeInput,
) -> JsonObject:
    """Set what this pod owns, and the measure that counts it.

    One sentence a person can check, as a bar held every week (kind=week) or
    a finish line by a date (kind=date, with due). The measure is one
    read-only SQL query over this pod's tables that answers one number -- one
    row, one column; with unit "%" it answers 0 to 100, as the target is
    written. It is read for everyone in the pod alike, so it may only count
    rows you can read in full, and it is read once now: the answer comes back
    as ``value``. Build the tables it counts first. Replaces what the pod
    owned; the plan for getting there stays in /plan.md.
    """

    async def op(services: PodServices) -> JsonObject:
        kept, reading = await set_pod_outcome(
            services.uow,
            pod_id=services.ctx.pod_id,
            outcome=request,
            ctx=services.ctx,
            measures=build_outcome_measures(services.uow),
        )
        assert kept is not None
        return {
            "success": True,
            "outcome": kept.model_dump(mode="json"),
            "value": reading.value if reading else None,
            "measured_at": reading.measured_at.isoformat() if reading else None,
        }

    return await run_pod_tool(
        ctx.deps,
        tool_name=TOOL_NAME,
        args=request.model_dump(mode="json"),
        op=op,
    )


__all__ = ["TOOL_NAME", "set_what_you_own"]
