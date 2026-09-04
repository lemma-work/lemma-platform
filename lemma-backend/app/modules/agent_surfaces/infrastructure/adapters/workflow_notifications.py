"""Telling a person a workflow is waiting on them.

A ``FORM`` node assigned to a pod member was, until this existed, a pure pull
queue: the wait row existed, ``workflow.run.waiting_assigned_to_me`` listed it,
and the flows page rendered it — but nothing ever told the assignee. A workflow
could sit for three days on somebody who had no idea.

This is `agent_surfaces`' half of that bridge, and it lives here rather than in
`app/composition` because it is made entirely of this module's own service. It
is published as a factory from `contracts/workflow_notifications.py`, so
`workflow` names the port and never this class.

**On the degradation.** A workflow must not fail because a Slack token expired,
and it does not — but the catch that guarantees that is narrow, and it has to
be, because `NotificationService.deliver` already handles that exact case one
layer down. It catches `(AgentSurfaceError, HTTPError, OSError)` per channel,
marks the notification undeliverable, and returns; a platform outage never
reaches this file at all. What used to sit here was a bare ``except Exception``
on each of three methods, layered on top of that precise one -- so the only
failures it uniquely swallowed were the ones the service deliberately lets
through: our own bugs. An ``AttributeError`` here read as "the notification
didn't go", and the run carried on.

So each method catches what genuinely must not fail a run:

* ``AgentSurfaceError`` -- this module's own refusals, and the reason the
  broadest of them is wanted: ``notify`` fails closed when the assignee is no
  longer a member of the pod, and a form assigned to somebody who has left must
  leave the run standing (unnotified, in the queue, exactly as before this
  bridge existed) rather than break it. Rate limits arrive as this too.
* ``SQLAlchemyError`` / ``OSError`` / ``HTTPError`` -- infrastructure. A
  notification is an enhancement to a mechanism that already worked, and a blip
  reaching it must not become a new way for runs to die.

Everything else propagates, and every catch logs with ``exc_info`` so that what
was swallowed leaves a traceback rather than a one-line ``error=``.
"""

from __future__ import annotations

from uuid import UUID

from httpx import HTTPError
from sqlalchemy.exc import SQLAlchemyError

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent_surfaces.api.dependencies import get_notification_service
from app.modules.agent_surfaces.domain.errors import AgentSurfaceError
from app.modules.agent_surfaces.domain.notification import NotificationOriginKind

logger = get_logger(__name__)

#: What a notification must survive rather than fail a workflow run over. See
#: the module docstring: not `Exception`, because the platform failures that
#: reason names are already handled inside `NotificationService.deliver`.
_SURVIVABLE = (AgentSurfaceError, SQLAlchemyError, OSError, HTTPError)


def describe_fields(schema: dict[str, object] | None) -> str:
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


def build_form_instruction(fields: str) -> str:
    """What the recipient's agent is told to do with their reply.

    It points at ``submit_workflow_form`` rather than inviting a free-text
    answer, because a form is validated against its node's schema and a prose
    reply cannot be. The notification refuses ``respond`` for the same reason.

    The tool, not the CLI: this instruction is read by whichever agent happens
    to be handling the reply, and on an email or chat surface that agent may
    have no shell and no authenticated CLI. ``submit_workflow_form`` is attached
    automatically whenever a form is open, so it is always reachable.

    It used to take ``run_id`` and ``node_id`` and immediately ``del`` them:
    both are read from the notification at submit time rather than retyped by
    the model, which is what stops a recipient's agent naming a run it guessed.
    """
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

    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self._uow = uow

    def _service(self):
        # A plain factory despite its FastAPI ``Depends`` annotation; calling it
        # directly is how the other contracts here build the same service
        # outside a request. It no longer takes a `ConversationService` -- see
        # `agent/contracts/conversations_for_surfaces.py`.
        return get_notification_service(self._uow)

    async def notify_form_assignee(
        self,
        *,
        pod_id: UUID,
        run_id: UUID,
        flow_id: UUID,
        node_id: str,
        assigned_pod_member_id: UUID,
        flow_name: str | None,
        schema: dict[str, object] | None,
        actor_user_id: UUID | None,
    ) -> None:
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

            fields = describe_fields(schema)
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
                background_instruction=build_form_instruction(fields),
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
        except _SURVIVABLE as exc:
            logger.warning(
                "workflow.notifications.form_notify_failed.degraded",
                run_id=str(run_id),
                node_id=node_id,
                error=str(exc),
                exc_info=True,
            )

    async def close_form_notification(
        self,
        *,
        pod_id: UUID,
        run_id: UUID,
        node_id: str,
        summary: str,
        data: dict[str, object] | None = None,
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
        except _SURVIVABLE as exc:
            logger.warning(
                "workflow.notifications.form_close_failed.degraded",
                run_id=str(run_id),
                node_id=node_id,
                error=str(exc),
                exc_info=True,
            )

    async def cancel_for_run(self, *, run_id: UUID) -> None:
        """A cancelled run leaves nothing outstanding in anyone's inbox."""
        try:
            await self._service().cancel_for_origin(
                origin_kind=NotificationOriginKind.WORKFLOW_FORM,
                origin_id=run_id,
            )
        except _SURVIVABLE as exc:
            logger.warning(
                "workflow.notifications.cancel_failed.degraded",
                run_id=str(run_id),
                error=str(exc),
                exc_info=True,
            )


__all__ = ["WorkflowNotificationAdapter", "build_form_instruction", "describe_fields"]
