"""Asking another pod: the tool half of ``services/pod_ask_service.py``.

Part of messaging because it is the same move as ``channel_post`` -- reaching
someone outside this conversation, who answers in their own thread with their
own tools -- and it lands the same way: the answer arrives here as a new turn,
so the asking agent ends its turn instead of waiting.

The first ask to each pod in a conversation needs the person's OK. The
assistant acts as them, and the answer comes back with their access in the
other pod, so content that reached this conversation from elsewhere -- an
email, a web page -- must not be able to send it there unseen. Approving it
for the conversation covers that pod from then on (``pod.ask:<pod id>``).
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field
from pydantic_ai.tools import RunContext

from app.core.authorization.session_approvals import has_session_approval
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.domain.pod_asks import AskMode, AskRefused
from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.services.pod_ask_service import (
    PodAskService,
    Teammate,
    resolve_teammate,
)
from app.modules.agent.infrastructure.tools.approval.denials import remember_denial
from app.modules.agent.infrastructure.tools.authority import workload_actor_id
from app.modules.agent.infrastructure.tools.responses import BaseToolResponse
from app.modules.agent.domain.context import ToolContext

ASK_TEAMMATE_TOOL_NAME = "ask_teammate"
ASK_NEEDS_APPROVAL = "POD_ASK_NEEDS_APPROVAL"
#: How long an ask may hold this turn open waiting for a quick answer. Past it
#: the answer arrives as a new turn, which costs nothing while it waits.
MAX_INLINE_WAIT_SECONDS = 45.0


def ask_permission_id(pod_id: UUID) -> str:
    """The session-approval key for asking one pod from one conversation."""
    return f"pod.ask:{pod_id}"


class TeammateSummary(BaseModel):
    teammate: str = Field(
        description="Pass this verbatim as ask_teammate's `teammate`."
    )
    name: str
    job: str | None = Field(default=None, description="What this pod is for.")
    reach: list[str] = Field(
        default_factory=list,
        description=(
            "'through the person': asked as them, with their access there. "
            "'connected': asked as this pod, over a link that pod's people made "
            "-- also when nobody is here."
        ),
    )


class ListTeammatesResponse(BaseToolResponse):
    teammates: list[TeammateSummary] = Field(default_factory=list)


class AskTeammateRequest(BaseModel):
    teammate: str = Field(
        description="The pod's exact name, or its `teammate` id from list_teammates."
    )
    request: str = Field(
        min_length=1,
        max_length=8000,
        description=(
            "Everything the other pod needs, in one self-contained message. It "
            "sees nothing of this conversation: name the record, the date, the "
            "customer, and say what answer you want back."
        ),
    )
    wait_seconds: float = Field(
        default=20.0,
        ge=0.0,
        le=MAX_INLINE_WAIT_SECONDS,
        description=(
            "How long to wait here for a quick answer. If it takes longer, end "
            "your turn: the answer arrives in this conversation by itself."
        ),
    )


class AskTeammateResponse(BaseToolResponse):
    teammate: str | None = None
    teammate_name: str | None = None
    asked_as: str | None = Field(
        default=None,
        description=(
            "AS_PERSON: it worked with the person's own access there. LINK: "
            "with what that pod shared with this one."
        ),
    )
    conversation_id: UUID | None = Field(
        default=None,
        description="The other pod's conversation for this request.",
    )
    status: str | None = Field(
        default=None,
        description=(
            "ANSWERED (read `answer`) / WORKING (end your turn; the answer comes "
            "here) / WAITING (it needs someone there first) / UNFINISHED / "
            "DISCONNECTED (the connection was removed)."
        ),
    )
    answer: str | None = None
    needs_approval: bool = False
    approval: JsonObject | None = None


def _service() -> PodAskService:
    return PodAskService(SessionUnitOfWorkFactory(async_session_maker))


async def _teammates(deps: ToolContext) -> list[Teammate]:
    return await _service().reachable(deps)


async def list_teammates(ctx: RunContext[ToolContext]) -> ListTeammatesResponse:
    """List the other pods this one can ask: ones the person is also in, and
    ones connected to this pod.

    Each is a teammate with its own job, tools and data; `ask_teammate` asks one.
    """
    teammates = await _teammates(ctx.deps)
    if not teammates:
        reason = await _service().unreachable_reason(ctx.deps)
        return ListTeammatesResponse(
            success=True,
            message=reason
            or (
                "There is no other pod this conversation can ask: the person "
                "isn't in one, none is connected to this pod, or other people "
                "read this thread."
            ),
        )
    return ListTeammatesResponse(
        success=True,
        teammates=[
            TeammateSummary(
                teammate=str(teammate.pod_id),
                name=teammate.name,
                job=teammate.description,
                reach=teammate.reach,
            )
            for teammate in teammates
        ],
    )


async def ask_teammate(
    ctx: RunContext[ToolContext], request: AskTeammateRequest
) -> AskTeammateResponse:
    """Ask another pod to find something out or look something up.

    It works on it in its own conversation -- with the person's access there
    when they are in both pods, or with what that pod shared over a link -- and
    its final message is the answer. A quick answer comes back in this call;
    otherwise end your turn: the answer arrives here as a new message, and you
    continue from there. Don't wait or poll for it.
    """
    deps = ctx.deps
    service = _service()
    try:
        teammates = await _teammates(deps)
        if not teammates and (reason := await service.unreachable_reason(deps)):
            raise AskRefused(reason)
        target = resolve_teammate(teammates, request.teammate)
        mode = await service.ensure_may_ask(deps, teammate=target)
        # Only asking as the person needs their OK: a link was approved by the
        # other pod's people, once, for asks nobody stands behind.
        if mode is AskMode.AS_PERSON and not await _approved(deps, target):
            needed = _approval_needed(target, request)
            # The server's record of what this ask needs, which is what an
            # approval of it will unlock -- not the model's copy of it.
            await remember_denial(deps, needed)
            return needed
        outcome = await service.ask(
            deps,
            teammate=target,
            request=request.request,
            wait_seconds=request.wait_seconds,
        )
    except AskRefused as exc:
        return AskTeammateResponse(success=False, error=exc.message)
    return AskTeammateResponse(
        success=True,
        teammate=str(outcome.pod_id),
        teammate_name=outcome.pod_name,
        conversation_id=outcome.conversation_id,
        status=outcome.status,
        asked_as=outcome.mode.value if outcome.mode else None,
        answer=outcome.answer,
        message=outcome.detail,
    )


async def _approved(deps: ToolContext, target: Teammate) -> bool:
    approved = deps.approved_execution
    if approved is not None and approved.tool_name == ASK_TEAMMATE_TOOL_NAME:
        return True
    return await has_session_approval(
        session_id=str(deps.conversation_id),
        person_user_id=deps.user_id,
        workload_actor_id=workload_actor_id(deps),
        permission_id=ask_permission_id(target.pod_id),
    )


def _approval_needed(
    target: Teammate, request: AskTeammateRequest
) -> AskTeammateResponse:
    return AskTeammateResponse(
        success=False,
        teammate=str(target.pod_id),
        teammate_name=target.name,
        error=(
            f"Asking {target.name} needs the person's OK first: it will work "
            "with their access there. Call request_approval with this call's "
            "tool_name and args, and copy approval.permission_ids verbatim."
        ),
        needs_approval=True,
        approval={
            "tool_name": ASK_TEAMMATE_TOOL_NAME,
            "args": request.model_dump(mode="json"),
            "reason_code": ASK_NEEDS_APPROVAL,
            "permission_ids": [ask_permission_id(target.pod_id)],
        },
    )
