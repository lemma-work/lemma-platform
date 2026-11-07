"""Applying one agent, or one pod machine, out of a bundle.

Split out of ``applier.py`` for the reason ``schedule_apply.py`` was: that file
sits at the 600-line ceiling the architecture ratchet sets. The two live
together because an agent may name a machine as the one its commands run on,
which is also why the plan applies machines first.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING
from uuid import UUID

from app.modules.pod_bundle.domain.state import PlanStep

if TYPE_CHECKING:
    from app.modules.agent.contracts import AgentToolset

#: The applier's manifest loader: ``(resource_type, name)`` to the manifest,
#: with ``$file`` refs and ``${var}`` placeholders already resolved.
Load = Callable[[str, str], Mapping[str, object]]


async def apply_machine(
    step: PlanStep, *, uow, pod_id: UUID, user_id: UUID, load: Load
) -> None:
    """Create the pod machine, or bring an existing one to the bundle's
    definition. An upsert by name, like every step: a replay converges."""
    from app.modules.workspace.contracts.pod_machines import upsert_pod_machine

    payload = load("machines", step.name)
    run_slots = payload.get("run_slots")
    display_name = payload.get("display_name")
    await upsert_pod_machine(
        uow,
        pod_id=pod_id,
        user_id=user_id,
        name=step.name,
        template=str(payload.get("template") or "workspace"),
        size=str(payload["size"]) if payload.get("size") else None,
        visibility=str(payload.get("visibility") or "pod"),
        run_slots=int(run_slots) if run_slots is not None else None,
        display_name=str(display_name) if display_name else None,
    )


async def apply_agent(
    step: PlanStep, *, uow, ctx, pod_id: UUID, user_id: UUID, load: Load
) -> None:
    """Create the agent, or overwrite an existing one's definition."""
    from app.modules.agent.contracts.provisioning import (
        create_agent,
        get_agent,
        update_agent,
    )

    payload = load("agents", step.name)
    common = {
        "description": payload.get("description"),
        "icon_url": payload.get("icon_url"),
        "agent_runtime": _agent_runtime(payload),
        # Toolsets are what let an imported agent actually *use* tools (POD,
        # WEB_SEARCH, …). Without them, a granted agent still can't act — so
        # they travel with the agent, not the deferred grants step.
        "toolsets": _agent_toolsets(payload),
        "input_schema": payload.get("input_schema"),
        "output_schema": payload.get("output_schema"),
        "metadata": payload.get("metadata"),
        # Which machine its commands run on. Absent means its own default,
        # and a bundle saying so clears a machine the target agent had.
        "machine": payload.get("machine"),
    }
    existing = await get_agent(uow, pod_id=pod_id, name=step.name, ctx=ctx)
    if existing is None:
        await create_agent(
            uow,
            pod_id=pod_id,
            user_id=user_id,
            name=step.name,
            instruction=str(payload.get("instruction") or ""),
            visibility=payload.get("visibility"),
            ctx=ctx,
            **common,
        )
    else:
        await update_agent(
            uow,
            pod_id=pod_id,
            name=step.name,
            instruction=payload.get("instruction"),
            user_id=user_id,
            ctx=ctx,
            **common,
        )


def _agent_runtime(payload: Mapping[str, object]):
    from app.core.domain.runtime import AgentRuntimeConfig

    raw = payload.get("agent_runtime")
    if isinstance(raw, dict) and raw.get("profile_id"):
        return AgentRuntimeConfig.model_validate(raw)
    return None


def _agent_toolsets(payload: Mapping[str, object]) -> list[AgentToolset]:
    """Map the manifest's ``toolsets`` list to :class:`AgentToolset` values,
    dropping any the runtime doesn't recognize (forward-compat) and the reserved
    ``VIEW_IMAGE`` toolset, which is never persisted."""
    from app.modules.agent.contracts import AgentToolset

    toolsets: list[AgentToolset] = []
    listed = payload.get("toolsets")
    for raw in listed if isinstance(listed, list) else []:
        try:
            toolset = AgentToolset(str(raw))
        except ValueError:
            continue
        if toolset is AgentToolset.VIEW_IMAGE:
            continue
        toolsets.append(toolset)
    return toolsets
