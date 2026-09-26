from __future__ import annotations

from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.modules.agent.tools.tool_payload_limits import bounded_tool_text
from app.modules.agent.tools.tool_errors import safe_error_text
from app.core.log.log import get_logger
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

If you are using this skill through Lemma's local harness or MCP-routed
`lemma_*` MCP tools, run CLI examples through `lemma_exec_command`. The
workspace injects Lemma environment variables for the current user and pod. Do
not run raw localhost API/Auth probes from workspace exec: workspace
`localhost` is the isolated workspace container, not the host Lemma app.
`agent-browser` drives the browser the person sees only when run through
`lemma_exec_command`; never use a native or computer-use browser instead.
"""

_NATIVE_HOST_SKILL_OVERRIDE = """

## Local Lemma Workspace Override

You are using this skill through Lemma's `lemma_*` MCP tools on the user's own
Mac, where your own shell and file tools are the command tools: there is no
`lemma_exec_command`. Run the skill's shell and `lemma` CLI examples in your own
shell, in your working directory; the Lemma environment variables for the
current user and pod are already set. `agent-browser` does not exist on this
Mac: drive the browser the person sees with `lemma_browser`, one
`agent-browser` command per call, saving screenshots under `/home/user/`.
Never use a native or computer-use browser instead.
"""

_IN_PROCESS_SKILL_OVERRIDE = """

## Local Lemma Workspace Override

Run this skill's shell and `lemma` CLI examples through `exec_command`. The
workspace injects Lemma environment variables for the current user and pod. Do
not run raw localhost API/Auth probes from it: workspace `localhost` is the
isolated workspace container, not the Lemma app. `agent-browser` drives the
browser the person sees when run through `exec_command`; never use any other
browser instead.
"""

_IN_PROCESS_HOST_SKILL_OVERRIDE = """

## Local Lemma Workspace Override

Run this skill's shell and `lemma` CLI examples through `exec_command`, which
runs on the user's own Mac with the Lemma environment variables for the current
user and pod already set. `agent-browser` is not on this Mac: drive the browser
the person sees with the `browser` tool, one `agent-browser` command per call,
saving screenshots under `/home/user/`. Never use any other browser instead.
"""

#: Every form the block above takes, one per way a run reaches its commands.
#: Each names only tools the run it is appended for actually has: a skill that
#: sent an in-process run to `lemma_exec_command`, or a coding agent on the Mac
#: to a sandbox tool Lemma had withheld from it, pointed at nothing.
SKILL_RUNTIME_OVERRIDES = (
    LOCAL_WORKSPACE_SKILL_OVERRIDE,
    _NATIVE_HOST_SKILL_OVERRIDE,
    _IN_PROCESS_SKILL_OVERRIDE,
    _IN_PROCESS_HOST_SKILL_OVERRIDE,
)

#: The heading that identifies the block above inside a stored tool result.
#: `remote_payload` strips it when replaying history, because everything it
#: renders is concatenated into a single user turn and Lemma's instructions to
#: an agent must not reach a model as the user's own words.
LOCAL_WORKSPACE_SKILL_OVERRIDE_MARKER = "## Local Lemma Workspace Override"


def skill_runtime_override(deps: BaseAgentContext) -> str:
    """The block appended to a loaded skill, for the tools this run has.

    ``supports_pause_signal`` is set only for the in-process harness, so its
    absence is what says the call came in over MCP from a coding agent.
    """
    in_process = bool(getattr(deps, "supports_pause_signal", False))
    mode = getattr(deps, "host_execution_mode", None)
    if in_process:
        return (
            _IN_PROCESS_HOST_SKILL_OVERRIDE
            if mode == "sandbox"
            else _IN_PROCESS_SKILL_OVERRIDE
        )
    if mode == "native":
        return _NATIVE_HOST_SKILL_OVERRIDE
    return LOCAL_WORKSPACE_SKILL_OVERRIDE


logger = get_logger(__name__)


async def list_skills(
    ctx: RunContext[BaseAgentContext], request: ListSkillsRequest
) -> SkillListResult:
    """List the available skills. Pass `{}`; call before `load_skill`."""
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
            error=safe_error_text(exc),
            message="Failed to list skills",
        )


async def load_skill(
    ctx: RunContext[BaseAgentContext], request: SkillLookupRequest
) -> SkillContentResult:
    """
    Load a skill's `SKILL.md`, or one resource file from inside it.

    Omit `resource_path` to get `SKILL.md` plus the list of the skill's resource
    files; pass a path from that list to read one.
    """
    if request.resource_path:
        try:
            return SkillContentResult(
                success=True,
                name=request.name,
                resource_path=request.resource_path,
                content=bounded_tool_text(
                    await read_workspace_skill_resource(
                        request.name,
                        request.resource_path,
                        pod_id=ctx.deps.pod_id,
                        user_id=ctx.deps.user_id,
                    ),
                    what="skill resource",
                ),
            )
        except Exception as exc:
            # Telling the agent is not telling whoever operates the system.
            logger.warning(
                "agent.skills.resource_load_failed.degraded",
                skill_name=request.name,
                exc_info=True,
            )
            return SkillContentResult(
                success=False,
                name=request.name,
                resource_path=request.resource_path,
                error=safe_error_text(exc),
                message=f"Failed to load skill resource: {request.name}/{request.resource_path}",
            )

    try:
        content = bounded_tool_text(
            await read_workspace_skill(
                request.name,
                pod_id=ctx.deps.pod_id,
                user_id=ctx.deps.user_id,
            ),
            what="skill",
        )
    except Exception as exc:
        logger.warning(
            "agent.skills.load_failed.degraded", skill_name=request.name, exc_info=True
        )
        return SkillContentResult(
            success=False,
            name=request.name,
            error=safe_error_text(exc),
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
        content=content + skill_runtime_override(ctx.deps),
        resources=resources,
    )


skills_toolset = FunctionToolset[BaseAgentContext](tools=[list_skills, load_skill])
