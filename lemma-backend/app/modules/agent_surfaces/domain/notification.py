"""Notifications: the durable record that the pod told a person something.

A notification is *not* a wait. A wait suspends an execution and resumes it with
an answer; a notification is fire-and-forget. The sender does not block on the
recipient — it carries on, and typically ``snooze``s until answers are plausible.

The one thread back is deliberately thin: when the last ask an asking
conversation made is answered, that conversation gets a fresh turn. Nothing
about this row becomes a wait — no execution hangs off it, no value crosses, and
the agent still has to go and read the answers itself. Being *told* there is
something to read is the whole of what it gets.

That asymmetry is the whole design. Resolution is done by the *recipient's own*
agent, in the recipient's own thread, under the recipient's own authority: the
``background_instruction`` tells that agent what to do with the reply, and
``respond_to_notification`` is where it records the outcome. Nothing ever moves
a value from one person's run context into another's, so the permission boundary
holds by construction rather than by a re-check somebody has to remember.

Both the agent tool and the workflow FORM node write rows here, because what
they genuinely share is "a person must be told, durably, and the UI must be able
to list it" — not a wait mechanism. The waits themselves stay where they are.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import model_validator

from app.core.domain.aggregate import AggregateRoot
from app.modules.agent_surfaces.domain.errors import (
    AgentSurfaceValidationError,
    NotificationTransitionError,
)


class NotificationOriginKind(StrEnum):
    """What produced the notification. ``origin_id`` is read against this."""

    AGENT_RUN = "AGENT_RUN"
    WORKFLOW_FORM = "WORKFLOW_FORM"
    SCHEDULE = "SCHEDULE"
    API = "API"


class NotificationStatus(StrEnum):
    """Where the *person* is: has the thing we needed from them happened?"""

    OPEN = "OPEN"
    RESPONDED = "RESPONDED"
    # Seen and explicitly dismissed, or delivered with expects_response=False.
    ACKNOWLEDGED = "ACKNOWLEDGED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"

    @property
    def is_terminal(self) -> bool:
        return self is not NotificationStatus.OPEN


class NotificationDeliveryStatus(StrEnum):
    """Where the *channel* is: did the message physically get to them?

    Deliberately a second column rather than more members on
    :class:`NotificationStatus`. The two axes are independent — a notification
    can be DELIVERED and still OPEN (they haven't answered), or UNDELIVERABLE
    and still RESPONDED (they saw it in the app and replied there). Smearing
    them into one enum is how you end up unable to answer "who did we fail to
    reach?", which is the only question this column exists for.
    """

    PENDING = "PENDING"
    DELIVERED = "DELIVERED"
    # No channel could carry it. Not an error: the in-app inbox still has it.
    UNDELIVERABLE = "UNDELIVERABLE"
    # A channel was chosen and the send raised.
    FAILED = "FAILED"


class NotificationEntity(AggregateRoot):
    """One thing the pod needs a person to see, and what to do with the reply."""

    pod_id: UUID
    recipient_user_id: UUID
    recipient_pod_member_id: UUID
    # Whose authority the sending run carried — the human behind the agent. Every
    # delivered message names them, because the recipient sees the pod's bot and
    # extends it the trust they extend to Lemma.
    actor_user_id: UUID | None = None
    actor_agent_id: UUID | None = None

    origin_kind: NotificationOriginKind
    origin_id: UUID | None = None
    origin_conversation_id: UUID | None = None

    title: str
    body: str
    # Never rendered to the recipient. It is addressed to the agent that handles
    # their reply, and leaking it would show them the asker's private framing.
    background_instruction: str | None = None
    expects_response: bool = True
    action: dict[str, Any] | None = None

    status: NotificationStatus = NotificationStatus.OPEN
    delivery_status: NotificationDeliveryStatus = NotificationDeliveryStatus.PENDING
    delivery_surface_id: UUID | None = None
    delivery_conversation_id: UUID | None = None
    delivery_platform: str | None = None
    delivery_error: str | None = None

    response_summary: str | None = None
    response_data: dict[str, Any] | None = None

    # Unique per pod. `wf:{run_id}:{node_id}` for form waits, `run:{agent_run_id}:
    # {tool_call_id}` for the tool. There is no outbound dedup store — the inbound
    # one claims inbound only — so without this a worker retry double-posts to a
    # chat platform.
    idempotency_key: str | None = None

    expires_at: datetime | None = None
    delivered_at: datetime | None = None
    read_at: datetime | None = None
    responded_at: datetime | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> "NotificationEntity":
        if not self.title.strip():
            raise AgentSurfaceValidationError("Notification title cannot be empty.")
        if not self.body.strip():
            raise AgentSurfaceValidationError("Notification body cannot be empty.")
        # A WORKFLOW_FORM notification's whole purpose is to point at the form.
        # Without the action there is nothing for the UI to open and nothing for
        # the recipient's agent to submit — it degrades into an unanswerable
        # message that can never leave OPEN, because ``respond`` refuses it.
        if self.origin_kind is NotificationOriginKind.WORKFLOW_FORM and not self.action:
            raise AgentSurfaceValidationError(
                "A WORKFLOW_FORM notification must carry its action."
            )
        return self

    def mark_delivered(
        self,
        *,
        surface_id: UUID | None,
        conversation_id: UUID | None,
        platform: str | None,
    ) -> None:
        self.delivery_status = NotificationDeliveryStatus.DELIVERED
        self.delivery_surface_id = surface_id
        self.delivery_conversation_id = conversation_id
        self.delivery_platform = platform
        self.delivery_error = None
        self.delivered_at = datetime.now(timezone.utc)

    def mark_undeliverable(self, reason: str) -> None:
        """No channel could carry it. The row and the inbox entry still stand."""
        self.delivery_status = NotificationDeliveryStatus.UNDELIVERABLE
        self.delivery_error = reason

    def mark_delivery_failed(
        self, reason: str, *, surface_id: UUID | None = None
    ) -> None:
        self.delivery_status = NotificationDeliveryStatus.FAILED
        self.delivery_surface_id = surface_id or self.delivery_surface_id
        self.delivery_error = reason

    def mark_read(self) -> None:
        """A timestamp, not a status — reading it does not answer it."""
        if self.read_at is None:
            self.read_at = datetime.now(timezone.utc)

    @property
    def responds_through_action(self) -> bool:
        """True when answering means completing ``action``, not writing prose.

        A WORKFLOW_FORM notification is answered by submitting the form, which
        validates against the node's JSON schema and resumes the run. Accepting a
        free-text ``respond`` on one of those would give a form two answer paths,
        exactly one of which validates — so the API refuses it and points the
        caller at the action instead. The UI reads this to decide whether the
        button opens a form or a text box.
        """
        return self.origin_kind is NotificationOriginKind.WORKFLOW_FORM

    @property
    def awaiting_response(self) -> bool:
        """What the UI renders a Respond button for."""
        return self.expects_response and self.status is NotificationStatus.OPEN

    def _require_open(self, verb: str) -> None:
        """The single gate every resolving transition passes through.

        A notification owns its ask until it resolves, and it resolves exactly
        once. Two people answering the same question from two devices, an agent
        answering one the asker already cancelled, a sweep expiring one that was
        answered a second earlier — all of them arrive here and all of them are
        refused, rather than silently overwriting an answer somebody already
        acted on.
        """
        if self.status is not NotificationStatus.OPEN:
            raise NotificationTransitionError(
                f"Cannot {verb} a notification that is already {self.status.value}.",
                notification_id=self.id,
                status=self.status.value,
            )

    def respond(self, *, summary: str, data: dict[str, Any] | None = None) -> None:
        """Record the answer. The only transition that produces a result."""
        self._require_open("respond to")
        if self.responds_through_action:
            raise NotificationTransitionError(
                "This notification is answered by completing its action, not by "
                "a free-text response.",
                notification_id=self.id,
                status=self.status.value,
            )
        if not self.expects_response:
            raise NotificationTransitionError(
                "This notification did not ask for a response; acknowledge it instead.",
                notification_id=self.id,
                status=self.status.value,
            )
        self.status = NotificationStatus.RESPONDED
        self.response_summary = summary
        self.response_data = data
        self.responded_at = datetime.now(timezone.utc)
        self.mark_read()

    def resolve_through_action(
        self, *, summary: str, data: dict[str, Any] | None = None
    ) -> None:
        """Close an action-backed ask from the system that owns the action.

        The workflow engine calls this when the form it pointed at is submitted.
        It is the one path allowed to resolve a ``responds_through_action`` row,
        because by then the answer has been through the node's schema validation
        — which is exactly what ``respond`` refuses to bypass.
        """
        self._require_open("resolve")
        self.status = NotificationStatus.RESPONDED
        self.response_summary = summary
        self.response_data = data
        self.responded_at = datetime.now(timezone.utc)
        self.mark_read()

    def acknowledge(self) -> None:
        """Seen, and nothing more is owed. Reading is not acknowledging."""
        self._require_open("acknowledge")
        if self.expects_response:
            raise NotificationTransitionError(
                "This notification is waiting for a response; respond to it "
                "instead of acknowledging it.",
                notification_id=self.id,
                status=self.status.value,
            )
        self.status = NotificationStatus.ACKNOWLEDGED
        self.mark_read()

    def expire(self) -> None:
        """Nobody answered in time. Not a failure — people are busy."""
        self._require_open("expire")
        self.status = NotificationStatus.EXPIRED

    def cancel(self) -> None:
        """The asker no longer needs it: run cancelled, workflow torn down.

        Deliberately not guarded on ``expects_response`` — an informational
        notification whose originating run was cancelled is just as stale as a
        question, and leaving it OPEN forever is the outcome to avoid.
        """
        self._require_open("cancel")
        self.status = NotificationStatus.CANCELLED

    def is_past_due(self, *, now: datetime | None = None) -> bool:
        if self.expires_at is None or self.status is not NotificationStatus.OPEN:
            return False
        return (now or datetime.now(timezone.utc)) >= self.expires_at
