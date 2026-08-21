"""Telling a person a workflow is waiting on them.

A ``FORM`` node assigned to a pod member was, until now, a pure pull queue: the
wait row existed, ``workflow.run.waiting_assigned_to_me`` listed it, and the
flows page rendered it — but nothing ever told the assignee. A workflow could
sit for three days on somebody who had no idea.

This is the bridge that fixes it, and it lives in ``composition`` because the
workflow module must not import ``agent_surfaces`` (the dependency runs the
other way, and surfaces already reach into agents). Same shape as
``workflow_agent.py`` and ``workflow_scheduler.py``.

Every failure here is swallowed. A workflow must not fail because a Slack token
expired: the wait is still recorded, the queue still lists it, and the person can
still find it in the app. Notification is an enhancement to a mechanism that
already worked, and it must not become a new way for runs to die.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.log.log import get_logger

logger = get_logger(__name__)


async def expire_past_due_notifications(uow) -> int:
    """Close out notifications nobody answered before their deadline.

    Lives here rather than in the workflow cron that calls it because the cron
    is workflow-module code and must not import ``agent_surfaces`` — the
    architecture ratchet enforces exactly that, and it is right to: the
    dependency runs the other way.
    """
    from app.modules.agent_surfaces.api.dependencies import get_notification_service
    from app.modules.agent.api.dependencies import get_conversation_service

    service = get_notification_service(uow, get_conversation_service(uow))
    return await service.expire_past_due()


def _describe_fields(schema: dict[str, Any] | None) -> str:
    """A one-line summary of what the form wants, for the message body.

    Reads the resolved schema the form executor already stored on the wait, so
    the recipient is told what is being asked rather than just that something is.
    """
    if not isinstance(schema, dict):
        return ""
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        return ""
    labels = []
    for name, spec in properties.items():
        title = None
        if isinstance(spec, dict):
            title = spec.get("title") or spec.get("description")
        labels.append(str(title or name))
    return ", ".join(labels[:8])


def build_form_instruction(*, run_id: UUID, node_id: str, fields: str) -> str:
    """What the recipient's agent is told to do with their reply.

    It points at ``submit_workflow_form`` rather than inviting a free-text
    answer, because a form is validated against its node's schema and a prose
    reply cannot be. The notification refuses ``respond`` for the same reason.

    The tool, not the CLI: this instruction is read by whichever agent happens
    to be handling the reply, and on an email or chat surface that agent may
    have no shell and no authenticated CLI. ``submit_workflow_form`` is attached
    automatically whenever a form is open, so it is always reachable.
    """
    del run_id, node_id  # read from the notification at submit time, not retyped
    asked = f" It asks for: {fields}." if fields else ""
    return (
        "This person is being asked to complete a workflow form."
        f"{asked}\n\n"
        "Collect the values conversationally, then submit them with "
        "`submit_workflow_form`, using the field names listed with this "
        "request. Do not invent values they did not give you — submission is "
        "validated against the form's schema and a guess will be rejected. If "
        "they decline or go quiet, leave the form unsubmitted; it stays in "
        "their queue in the app."
    )


class WorkflowNotificationAdapter:
    """Creates and closes the notification that mirrors a human wait."""

    def __init__(self, uow):
        self._uow = uow

    def _service(self):
        from app.modules.agent_surfaces.api.dependencies import (
            get_notification_service,
        )
        from app.composition.surface_agent import get_conversation_service

        # Both are plain factories despite their FastAPI ``Depends`` annotations;
        # calling them directly is how the other composition adapters build the
        # same services outside a request.
        return get_notification_service(self._uow, get_conversation_service(self._uow))

    async def notify_form_assignee(
        self,
        *,
        pod_id: UUID,
        run_id: UUID,
        flow_id: UUID,
        node_id: str,
        assigned_pod_member_id: UUID,
        flow_name: str | None,
        schema: dict[str, Any] | None,
        actor_user_id: UUID | None,
    ) -> None:
        from app.modules.agent_surfaces.domain.notification import (
            NotificationOriginKind,
        )

        try:
            service = self._service()
            recipient_user_id = await service.membership.resolve_pod_recipient(
                pod_id=pod_id, reference=str(assigned_pod_member_id)
            )
            if recipient_user_id is None:
                logger.warning(
                    "workflow.notifications.assignee_unresolved.degraded",
                    run_id=str(run_id),
                    node_id=node_id,
                )
                return

            fields = _describe_fields(schema)
            title = f"{flow_name or 'A workflow'} needs your input"
            body = f"{title}.\n\nStep: {node_id}." + (
                f"\nIt asks for: {fields}." if fields else ""
            )
            await service.notify(
                pod_id=pod_id,
                recipient_user_id=recipient_user_id,
                title=title,
                body=body,
                origin_kind=NotificationOriginKind.WORKFLOW_FORM,
                origin_id=run_id,
                actor_user_id=actor_user_id,
                background_instruction=build_form_instruction(
                    run_id=run_id, node_id=node_id, fields=fields
                ),
                expects_response=True,
                action={
                    "type": "WORKFLOW_FORM",
                    "run_id": str(run_id),
                    "flow_id": str(flow_id),
                    "node_id": node_id,
                    # The concrete, template-resolved schema the executor already
                    # put on the wait — so the inbox can render the real form
                    # instead of deep-linking away to find it.
                    "schema": schema,
                },
                # One per (run, node). A retried worker job, or a run that
                # re-enters the same node, must not stack duplicates in
                # somebody's inbox.
                idempotency_key=f"wf:{run_id}:{node_id}",
            )
        except Exception as exc:  # noqa: BLE001 - see module docstring
            logger.warning(
                "workflow.notifications.form_notify_failed.degraded",
                run_id=str(run_id),
                node_id=node_id,
                error=str(exc),
            )

    async def close_form_notification(
        self,
        *,
        pod_id: UUID,
        run_id: UUID,
        node_id: str,
        summary: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Close the notification when the form it pointed at is submitted.

        Goes through ``resolve_through_action``, the only path allowed to close
        an action-backed notification — by this point the submission has been
        through the node's schema validation and the assignee check, which is
        exactly what a free-text ``respond`` would have bypassed.
        """
        try:
            service = self._service()
            existing = await service.notifications.get_by_idempotency_key(
                pod_id=pod_id, idempotency_key=f"wf:{run_id}:{node_id}"
            )
            if existing is None:
                return
            await service.resolve_through_action(
                notification_id=existing.id, summary=summary, data=data
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "workflow.notifications.form_close_failed.degraded",
                run_id=str(run_id),
                node_id=node_id,
                error=str(exc),
            )

    async def cancel_for_run(self, *, run_id: UUID) -> None:
        """A cancelled run leaves nothing outstanding in anyone's inbox."""
        from app.modules.agent_surfaces.domain.notification import (
            NotificationOriginKind,
        )

        try:
            await self._service().cancel_for_origin(
                origin_kind=NotificationOriginKind.WORKFLOW_FORM,
                origin_id=run_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "workflow.notifications.cancel_failed.degraded",
                run_id=str(run_id),
                error=str(exc),
            )
