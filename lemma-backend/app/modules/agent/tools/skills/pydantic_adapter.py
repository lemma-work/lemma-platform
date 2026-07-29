from __future__ import annotations

from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.skills.models import (
    ListSkillsRequest,
    SkillContentResult,
    SkillLookupRequest,
    SkillListResult,
    SkillResourceSummary,
    SkillSummary,
)
from app.modules.agent.tools.skills.skill_loader import (
    list_workspace_skill_resources,
    list_workspace_skills,
    read_workspace_skill,
    read_workspace_skill_resource,
)

LOCAL_WORKSPACE_SKILL_OVERRIDE = """

## Local Lemma Workspace Override

If you are using this skill through Lemma's local harness or remote-harness-routed
`lemma_*` MCP tools, run CLI examples through `lemma_exec_command`. The
workspace injects Lemma environment variables for the current user and pod. Do
not run raw localhost API/Auth probes from workspace exec: workspace
`localhost` is the isolated workspace container, not the host Lemma app.
"""


async def list_skills(
    ctx: RunContext[BaseAgentContext], request: ListSkillsRequest
) -> SkillListResult:
    """
    List the skills currently available from the workspace `skills/` directory.

    Call this first when you need to discover which skills exist before loading one.
    The request object is intentionally empty; pass `{}`.
    """
    del request
    try:
        return SkillListResult(
            success=True,
            skills=[
                SkillSummary(**item)
                for item in await list_workspace_skills(
                    pod_id=ctx.deps.pod_id,
                    user_id=ctx.deps.user_id,
                )
            ],
        )
    except Exception as exc:
        return SkillListResult(
            success=False,
            error=str(exc),
            message="Failed to list skills",
        )


async def load_skill(
    ctx: RunContext[BaseAgentContext], request: SkillLookupRequest
) -> SkillContentResult:
    """
    Load a skill's `SKILL.md`, or a single resource file from inside the skill.

    Call this after `list_skills`. Two modes on one tool:
    - Omit `resource_path` → returns the full `SKILL.md` content **and** the list of
      the skill's extra resource files (their relative `path`s under `resources`).
    - Pass a `resource_path` from that list → returns that resource's content.

    Always pass the skill name in `request.name`.
    """
    if request.resource_path:
        try:
            return SkillContentResult(
                success=True,
                name=request.name,
                resource_path=request.resource_path,
                content=await read_workspace_skill_resource(
                    request.name,
                    request.resource_path,
                    pod_id=ctx.deps.pod_id,
                    user_id=ctx.deps.user_id,
                ),
            )
        except Exception as exc:
            return SkillContentResult(
                success=False,
                name=request.name,
                resource_path=request.resource_path,
                error=str(exc),
                message=f"Failed to load skill resource: {request.name}/{request.resource_path}",
            )

    try:
        content = await read_workspace_skill(
            request.name,
            pod_id=ctx.deps.pod_id,
            user_id=ctx.deps.user_id,
        )
    except Exception as exc:
        return SkillContentResult(
            success=False,
            name=request.name,
            error=str(exc),
            message=f"Unknown or unavailable skill: {request.name}",
        )

    # Resource listing is best-effort — SKILL.md still loads if it fails.
    resources: list[SkillResourceSummary] = []
    try:
        resources = [
            SkillResourceSummary(**item)
            for item in await list_workspace_skill_resources(
                request.name,
                pod_id=ctx.deps.pod_id,
                user_id=ctx.deps.user_id,
            )
        ]
    except Exception:
        resources = []

    return SkillContentResult(
        success=True,
        name=request.name,
        content=content + LOCAL_WORKSPACE_SKILL_OVERRIDE,
        resources=resources,
    )


skills_toolset = FunctionToolset[BaseAgentContext](tools=[list_skills, load_skill])
