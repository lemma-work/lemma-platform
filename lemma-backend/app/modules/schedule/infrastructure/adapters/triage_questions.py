"""`TriageQuestions`, put to a person as a notification they answer by choosing.

The question is the decider's own -- its prompt, and what each option means --
so the person is asked what the decider was asked. They are offered only the
options that settle the event, each saying what it does, and what was offered
is written into the notification's action: the answer is routed by what the
person saw.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from pydantic import JsonValue

from app.core.log.log import get_logger
from app.modules.agent_surfaces.contracts import CHOICE_ACTION
from app.modules.decisions.contracts.shapes import ChoiceQuestion
from app.modules.schedule.domain.ports import TriageQuestion
from app.modules.schedule.domain.triage import (
    OfferedOption,
    TriageAsk,
    TriageRoute,
    offered_options,
)

logger = get_logger(__name__)

#: What each settling route does, as the person is told it.
_EFFECT = {
    TriageRoute.ACT: "handled now",
    TriageRoute.DIGEST: "saved for the next digest",
    TriageRoute.IGNORE: "skipped",
}
_DEFAULT_PROMPT = "What should happen with this event?"
#: How much of the judged event the question quotes. The rest is on the run.
_EVIDENCE_CHARS = 1_500
_TITLE_CHARS = 255


@dataclass(frozen=True, slots=True)
class QuestionWording:
    """How the decider puts a question: its prompt, and what each option means."""

    prompt: str
    labels: dict[str, str]


class QuestionWordingLookup(Protocol):
    async def __call__(
        self, *, pod_id: UUID, decider: str, question: str
    ) -> QuestionWording | None: ...


class QuestionSender(Protocol):
    """`agent_surfaces`' `ask_about_schedule_event`, as much of it as this uses."""

    async def __call__(
        self,
        *,
        pod_id: UUID,
        recipient_user_id: UUID,
        schedule_id: UUID,
        title: str,
        body: str,
        action: dict[str, JsonValue],
        background_instruction: str,
        idempotency_key: str,
    ) -> UUID | None: ...


async def decider_wording(
    *, pod_id: UUID, decider: str, question: str
) -> QuestionWording | None:
    """The question as the pod decider words it, or None if it is gone."""
    from app.modules.decisions.contracts.deciders import get_decider

    found = await get_decider(pod_id=pod_id, name=decider)
    asked = found.definition.questions.get(question) if found is not None else None
    if not isinstance(asked, ChoiceQuestion):
        return None
    labels = {key: option.description for key, option in (asked.options or {}).items()}
    return QuestionWording(prompt=asked.prompt, labels=labels)


async def _send(
    *,
    pod_id: UUID,
    recipient_user_id: UUID,
    schedule_id: UUID,
    title: str,
    body: str,
    action: dict[str, JsonValue],
    background_instruction: str,
    idempotency_key: str,
) -> UUID | None:
    from app.modules.agent_surfaces.contracts.notifications import (
        ask_about_schedule_event,
    )

    return await ask_about_schedule_event(
        pod_id=pod_id,
        recipient_user_id=recipient_user_id,
        schedule_id=schedule_id,
        title=title,
        body=body,
        action=action,
        background_instruction=background_instruction,
        idempotency_key=idempotency_key,
    )


class NotificationTriageQuestions:
    """Ask a held event's person through their notifications."""

    def __init__(
        self,
        *,
        send: QuestionSender | None = None,
        wording_of: QuestionWordingLookup | None = None,
    ) -> None:
        self._send: QuestionSender = send or _send
        self._wording_of: QuestionWordingLookup = wording_of or decider_wording

    async def ask(self, question: TriageQuestion) -> UUID | None:
        wording = await self._wording_of(
            pod_id=question.pod_id,
            decider=question.decider,
            question=question.question,
        )
        prompt = wording.prompt if wording is not None else _DEFAULT_PROMPT
        options = offered_options(
            question.routes, wording.labels if wording is not None else {}
        )
        ask = TriageAsk(
            schedule_id=question.schedule_id,
            run_id=question.run_id,
            decision_id=question.decision_id,
            question=question.question,
            options=options,
        )
        notification_id = await self._send(
            pod_id=question.pod_id,
            recipient_user_id=question.recipient_user_id,
            schedule_id=question.schedule_id,
            title=_title(question.schedule_name, prompt),
            body=_body(prompt, options, question.evidence),
            action={"type": CHOICE_ACTION, **ask.model_dump(mode="json")},
            background_instruction=_instruction(question, options),
            # One question per held event, however often the event arrives.
            idempotency_key=f"schedule-ask:{question.run_id}",
        )
        if notification_id is None:
            # Nobody to ask: the event stays held, and the run says so.
            logger.warning(
                "schedule.triage_questions.nobody_to_ask.degraded",
                schedule_id=str(question.schedule_id),
                run_id=str(question.run_id),
            )
        return notification_id


def _title(schedule_name: str | None, prompt: str) -> str:
    title = f"{schedule_name}: {prompt}" if schedule_name else prompt
    if len(title) <= _TITLE_CHARS:
        return title
    return title[: _TITLE_CHARS - 1].rstrip() + "…"


def _body(prompt: str, options: list[OfferedOption], evidence: str | None) -> str:
    lines = [prompt]
    if evidence:
        quoted = evidence[:_EVIDENCE_CHARS]
        if len(evidence) > _EVIDENCE_CHARS:
            quoted += " …"
        lines.append(quoted)
    choices = "\n".join(
        f"- {option.key}: {option.label} ({_EFFECT[option.route]})"
        for option in options
    )
    lines.append(f"Answer with one of:\n{choices}")
    return "\n\n".join(lines)


def _instruction(question: TriageQuestion, options: list[OfferedOption]) -> str:
    keys = ", ".join(option.key for option in options)
    name = f" {question.schedule_name!r}" if question.schedule_name else ""
    return (
        f"The schedule{name} held an event for this person to sort. When they "
        "choose, record it with respond_to_notification, putting the option's "
        f"key in data.answer -- one of: {keys}. Record only what they chose; if "
        "they do not choose, leave it open. Their answer decides what happens to "
        f"the event, and teaches the decider {question.decider!r}."
    )
