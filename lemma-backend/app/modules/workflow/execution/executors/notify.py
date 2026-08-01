"""Notify node executor: tell a pod member something, then carry on."""

from uuid import UUID

from app.modules.workflow.domain.nodes import NotifyNode
from app.modules.workflow.execution.outcome import Advance, NodeOutcome
from app.modules.workflow.execution.step_context import StepContext


class NotifyExecutor:
    async def execute(self, node: NotifyNode, step: StepContext) -> NodeOutcome:
        from app.modules.agent_surfaces.domain.entities import NotificationOrigin
        from app.modules.agent_surfaces.services.surface_display_delivery import (
            notify_member,
        )

        recipient = _resolve_recipient(node, step)
        if recipient is None:
            # A notify nobody can receive is a configuration error, but it is not
            # a reason to fail a run that may have already done real work. Report
            # it in the step output where it is visible on the run timeline.
            return Advance(
                output={"delivered": False, "reason": "No recipient resolved."}
            )

        outcome = await notify_member(
            pod_id=step.pod_id,
            recipient_user_id=recipient,
            body=str(step.context.resolve(node.config.message) or node.config.message),
            title=node.config.title,
            origin_type=NotificationOrigin.WORKFLOW_RUN,
            origin_id=step.run_id,
        )
        if outcome is None:
            return Advance(
                output={
                    "delivered": False,
                    "reason": "Recipient is not a member of this pod.",
                }
            )
        return Advance(
            output={
                "delivered": True,
                "notification_id": str(outcome.notification_id),
                "delivered_via": (
                    outcome.delivered_via.value if outcome.delivered_via else None
                ),
                "conversation_id": (
                    str(outcome.conversation_id) if outcome.conversation_id else None
                ),
            }
        )


def _resolve_recipient(node: NotifyNode, step: StepContext) -> UUID | None:
    """Expression wins over the literal, matching the FORM node's assignee rule."""
    expression = node.config.recipient_user_id_expression
    if expression:
        resolved = step.context.resolve(expression)
        if resolved is None:
            return None
        try:
            return UUID(str(resolved))
        except (ValueError, AttributeError):
            return None
    return node.config.recipient_user_id
