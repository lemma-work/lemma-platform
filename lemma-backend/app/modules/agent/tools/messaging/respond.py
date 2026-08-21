"""The other half of ``message_user``: recording what the person said back.

This runs in the *recipient's* conversation, as the recipient. That is what
makes the whole design safe without a new permission check: their agent writes
under their authority, and the asker reads the result under its own. No value
ever crosses from one person's run context into another's.

It is also why the notification row is the default place an answer goes. A pod
table works too — that is what a ``background_instruction`` naming one is for —
but for the common case (four people, four status updates) nothing needs to be
created in advance.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field
from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.composition.agent_notifications import (
    notification_form_action,
    record_notification_response,
)
from app.composition.agent_workflow_forms import submit_workflow_form as submit_form
from app.core.log.log import get_logger
from app.modules.agent.tools.context import BaseAgentContext, BaseToolResponse

logger = get_logger(__name__)


class RespondToNotificationRequest(BaseModel):
    notification_id: UUID = Field(description="Id of the open request.")
    summary: str = Field(
        min_length=1,
        description=(
            "What they actually said, in their terms — this is all the asker "
            "sees. Keep numbers, dates and reasons; don't compress to 'done'."
        ),
    )
    data: dict | None = Field(
        default=None,
        description="Structured values, when specific fields were asked for.",
    )


class RespondToNotificationResponse(BaseToolResponse):
    pass


async def respond_to_notification(
    ctx: RunContext[BaseAgentContext], request: RespondToNotificationRequest
) -> RespondToNotificationResponse:
    """Record this person's answer to something the pod asked them.

    Call it once you actually have the answer — not when they say they will get
    to it. Record only what they told you: an invented answer is worse than a
    missing one, because the asker acts on it. If they decline, leave the request
    open and say so.
    """
    deps = ctx.deps
    if deps.pod_id is None:
        return RespondToNotificationResponse(
            success=False, error="This tool is only available inside a pod."
        )

    # No try/except: GracefulToolset turns a raising tool body into an error
    # result the model can act on. Catching here too would also swallow the
    # 409 that says somebody already answered — which the model needs to see.
    await record_notification_response(
        pod_id=deps.pod_id,
        notification_id=request.notification_id,
        # The conversation owner. An agent cannot answer on behalf of anyone
        # else: the service checks the responder owns the row.
        responder_user_id=deps.user_id,
        summary=request.summary,
        data=request.data,
    )

    return RespondToNotificationResponse(
        success=True,
        message=(
            "Recorded. The person who asked will see it the next time they check."
        ),
    )


class SubmitWorkflowFormRequest(BaseModel):
    notification_id: UUID = Field(description="Id of the open request.")
    inputs: dict = Field(
        description=(
            "The form's fields, using the exact names from its schema. Only "
            "what they actually told you — omitted fields fall back to schema "
            "defaults, invented ones are rejected."
        )
    )


class SubmitWorkflowFormResponse(BaseToolResponse):
    pass


async def submit_workflow_form(
    ctx: RunContext[BaseAgentContext], request: SubmitWorkflowFormRequest
) -> SubmitWorkflowFormResponse:
    """Complete a workflow form this person was asked to fill in.

    Only for requests shown as answered by a form — those name their fields
    above. Collect the values conversationally, then submit them here; a
    free-text `respond_to_notification` will not advance the workflow.

    Values are validated against the form's schema, so a guess is refused
    rather than written into the run. If it comes back invalid, ask them for the
    field it names — never substitute your own value.
    """
    deps = ctx.deps
    if deps.pod_id is None:
        return SubmitWorkflowFormResponse(
            success=False, error="This tool is only available inside a pod."
        )

    action = await notification_form_action(
        pod_id=deps.pod_id, notification_id=request.notification_id
    )
    if action is None:
        return SubmitWorkflowFormResponse(
            success=False,
            error=(
                "That request is not answered by a workflow form. Use "
                "respond_to_notification instead."
            ),
        )

    submitted, message = await submit_form(
        run_id=action["run_id"],
        node_id=action["node_id"],
        inputs=request.inputs,
        # The conversation owner. The engine re-checks that the form is theirs.
        requester_user_id=deps.user_id,
    )
    return SubmitWorkflowFormResponse(
        success=submitted,
        message=message if submitted else None,
        error=None if submitted else message,
    )


respond_toolset = FunctionToolset[BaseAgentContext](
    tools=[respond_to_notification, submit_workflow_form]
)
