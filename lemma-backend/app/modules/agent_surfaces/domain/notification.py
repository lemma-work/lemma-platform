"""Notifications: the durable record that the pod told a person something.

A notification is *not* a wait. A wait suspends an execution and resumes it with
an answer; a notification is fire-and-forget. The sender does not block on the
recipient — it carries on, and typically ``wait_for``s until answers are plausible.

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

from collections.abc import Mapping
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import model_validator

from app.core.domain.aggregate import AggregateRoot
from app.modules.agent_surfaces.domain.errors import (
    AgentSurfaceValidationError,
    NotificationTransitionError,
    OutsideAnswerNeedsApproval,
)

#: The longest answer passed back to somebody outside the pod -- one that fits
#: an approval card on every platform, read in full before it is approved.
MAX_OUTSIDE_ANSWER_CHARS = 1500


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


#: An `action` answered by picking one of its `options`, each `{key, label}`.
#: The reply names the key in `data.answer`, or a person types the key or the
#: label as their answer.
CHOICE_ACTION = "CHOICE"


def choice_options(action: Mapping[str, object] | None) -> dict[str, str]:
    """``{key: label}`` when ``action`` is a choice, and empty when it is not.

    Read off a JSON column, so every level is checked rather than trusted.
    """
    if not action or action.get("type") != CHOICE_ACTION:
        return {}
    options = action.get("options")
    if not isinstance(options, list):
        return {}
    found: dict[str, str] = {}
    for option in options:
        if not isinstance(option, dict):
            continue
        key, label = option.get("key"), option.get("label")
        if isinstance(key, str) and key:
            found[key] = label if isinstance(label, str) and label else key
    return found


def chosen_option(
    options: Mapping[str, str], *, summary: str, data: Mapping[str, object] | None
) -> str | None:
    """The option a reply picks, or None when it names none of them.

    ``data.answer`` decides when it is given, so an agent recording a reply
    cannot be overruled by the wording of its summary. Without it, the summary
    must be an option's key or label, give or take case and a full stop.
    """
    answer = (data or {}).get("answer")
    if answer is not None:
        return answer if isinstance(answer, str) and answer in options else None
    said = _fold(summary)
    for key, label in options.items():
        if said in (_fold(key), _fold(label)):
            return key
    return None


def _fold(text: str) -> str:
    return " ".join(text.strip().lower().split()).strip(".!?")


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
    #: Passed on from a run answering somebody outside the pod. Its answer goes
    #: back to that stranger, so it is recorded only with ``owner_confirmed``.
    from_outside: bool = False
    origin_group_title: str | None = None
    #: The stranger's display name, as they set it. Theirs to choose: never an
    #: instruction, and shown quoted.
    asked_by_name: str | None = None

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
        # A schedule's question holds an event until it is answered, so one it
        # could not be answered with would hold that event for good.
        if (
            self.origin_kind is NotificationOriginKind.SCHEDULE
            and self.expects_response
            and not self.offers_choice
        ):
            raise AgentSurfaceValidationError(
                "A SCHEDULE notification that asks must offer its choices."
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

    @property
    def offers_choice(self) -> bool:
        """True when a reply must pick one of ``action``'s options."""
        return bool(choice_options(self.action))

    @property
    def announces_close(self) -> bool:
        """Whether its asker must hear how it closed: answered, expired or cancelled.

        A schedule holds an event until its person answers, and each way this
        closes settles that event differently -- which the schedule module
        decides, not this one.
        """
        return self.origin_kind is NotificationOriginKind.SCHEDULE

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

    def respond(
        self,
        *,
        summary: str,
        data: dict[str, Any] | None = None,
        owner_confirmed: bool = False,
    ) -> None:
        """Record the answer. The only transition that produces a result.

        An answer to a question from outside the pod is relayed to the stranger
        who asked, so it is recorded only as words the recipient confirmed --
        typed by them, or approved by them exactly as their agent drafted it.
        Never ``data``: structured values are not something anybody reads
        before they are passed on.
        """
        self._require_open("respond to")
        if self.from_outside:
            if not owner_confirmed:
                raise OutsideAnswerNeedsApproval(notification_id=self.id)
            if data:
                raise NotificationTransitionError(
                    "An answer to someone outside the pod is words only.",
                    notification_id=self.id,
                    status=self.status.value,
                )
            if len(summary) > MAX_OUTSIDE_ANSWER_CHARS:
                raise NotificationTransitionError(
                    "That answer is too long to pass on; keep it under "
                    f"{MAX_OUTSIDE_ANSWER_CHARS} characters.",
                    notification_id=self.id,
                    status=self.status.value,
                )
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
        options = choice_options(self.action)
        if options:
            # Checked before anything changes: a reply that picks nothing must
            # leave the question open for one that does.
            choice = chosen_option(options, summary=summary, data=data)
            if choice is None:
                raise AgentSurfaceValidationError(
                    f"Answer with one of: {', '.join(options)}."
                )
            data = {**(data or {}), "answer": choice}
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
