"""``delegate``: hand several tasks to sub-agents in one call, and wait once.

The one delegation tool an agent sees, with no tool search in front of it.
Each task becomes a child conversation (``SubAgentService.spawn``, idempotent
per call and task, capped per run). With ``wait="all"`` the run then suspends
on a single wait over every child and wakes once -- when all have ended, with
each one's report, status and error, or as soon as one stops to ask. With
``wait="none"`` it carries on, and the reports arrive later together, as one
message.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.domain.context import ToolContext
from app.modules.agent.domain.delegation import MAX_TASKS_PER_CALL
from app.modules.agent.domain.pauses import DELEGATE_CALL_NAME
from app.modules.agent.domain.state_kinds import PauseKind
from app.modules.agent.domain.tools import ReplayClass, ToolGroup, ToolPolicy, tool
from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.infrastructure.tools.tool_errors import safe_error_text
from app.modules.agent.infrastructure.tools.waiting.models import (
    MAX_WAIT_SECONDS,
    WaitForRequest,
)
from app.modules.agent.services.subagent_service import SubAgentError, SubAgentService

#: How long a delegation waits by default before waking with what it has.
#: Longer than ``wait_for``'s default: the children are doing real work.
DEFAULT_DELEGATION_SECONDS = 60 * 60
DELEGATE = DELEGATE_CALL_NAME


class DelegateTask(BaseModel):
    brief: str = Field(
        min_length=1,
        description=(
            "Everything the sub-agent needs, standing alone: it sees nothing of "
            "this conversation. The goal, the inputs, the constraints, the "
            "directory to work in, and what its report should look like."
        ),
    )
    agent: str | None = Field(
        default=None,
        description="A pod agent to hand it to. Omit for another instance of you.",
    )
    model: str | None = Field(
        default=None,
        description=(
            "Another model of your runtime to run this one on, by name -- a "
            "faster one for a simple lookup. Omit to use yours."
        ),
    )


class DelegateRequest(BaseModel):
    tasks: list[DelegateTask] = Field(
        min_length=1,
        max_length=MAX_TASKS_PER_CALL,
        description="Independent tasks, run in parallel.",
    )
    wait: Literal["all", "none"] = Field(
        default="all",
        description=(
            "'all': end your turn here and wake once, with every report. "
            "'none': carry on; the reports arrive together as a message later."
        ),
    )
    max_seconds: int | None = Field(
        default=None,
        description=(
            "With wait='all', wake after this long with whatever has finished. "
            f"Defaults to {DEFAULT_DELEGATION_SECONDS // 60} minutes."
        ),
    )


def _service() -> SubAgentService:
    return SubAgentService(SessionUnitOfWorkFactory(async_session_maker))


async def delegate(ctx: ToolContext, request: DelegateRequest) -> JsonObject:
    """Hand independent tasks to sub-agents, which run in parallel.

    Each child starts fresh with only its brief, in the same sandbox as you,
    and its last message is its report to you. With wait='all' (the default)
    your turn ends here and you wake once with every report, status and error.
    """
    from app.modules.agent.infrastructure.tools.waiting.tools import wait_on

    service = _service()
    handles: list[dict[str, object]] = []
    for index, task in enumerate(request.tasks):
        try:
            handle = await service.spawn(
                ctx,
                agent_name=task.agent,
                input_data=task.brief,
                # One key per task of this call: a resumed turn re-running it
                # gets the children it already started.
                tool_call_id=f"{ctx.tool_call_id}:{index}"
                if ctx.tool_call_id
                else None,
                model=task.model,
            )
        except SubAgentError as exc:
            handles.append({"task": index, "error": safe_error_text(exc)})
            continue
        handles.append(
            {
                "task": index,
                "conversation_id": str(handle.conversation_id),
                "run_id": str(handle.run_id) if handle.run_id else None,
            }
        )
    started = [str(item["run_id"]) for item in handles if item.get("run_id")]
    if request.wait == "none" or not started:
        return {
            "success": bool(started),
            "children": handles,
            "message": (
                "Started. Their reports arrive together as one message once "
                "they have all finished; do not poll."
                if started
                else "No sub-agent could be started."
            ),
        }
    waited = await wait_on(
        ctx,
        WaitForRequest(
            reason=f"waiting on {len(started)} sub-agent(s)",
            subagent_run_ids=started,
            max_seconds=min(
                request.max_seconds or DEFAULT_DELEGATION_SECONDS, MAX_WAIT_SECONDS
            ),
        ),
        tool_name=DELEGATE,
    )
    # Answered without suspending: every child had already ended, or this is
    # a remote harness, which ends its turn on its own.
    return {"children": handles, **waited.model_dump(mode="json", exclude_none=True)}


SPECS = (
    tool(
        delegate,
        ToolGroup.SUBAGENTS,
        # Visible: delegation is no search away. Its wait is a WAIT pause, and
        # its spawns are keyed per call and task, so a resumed turn re-running
        # it gets the same children.
        ToolPolicy(pauses=PauseKind.WAIT, replay=ReplayClass.KEYED),
    ),
)

__all__ = ["DELEGATE", "DelegateRequest", "DelegateTask", "SPECS", "delegate"]
