"""The question a held event puts to its person: the decider's own, and answerable."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from pydantic import JsonValue

from app.modules.schedule.domain.ports import TriageQuestion
from app.modules.schedule.domain.triage import TriageAsk, TriageRoute
from app.modules.schedule.infrastructure.adapters.triage_questions import (
    NotificationTriageQuestions,
    QuestionWording,
)

pytestmark = pytest.mark.unit


class _Sent:
    """`ask_about_schedule_event`, keeping what it was asked to send."""

    def __init__(self, notification_id: UUID | None) -> None:
        self.notification_id = notification_id
        self.calls: list[dict[str, object]] = []

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
    ) -> UUID | None:
        self.calls.append(
            {
                "recipient_user_id": recipient_user_id,
                "schedule_id": schedule_id,
                "title": title,
                "body": body,
                "action": action,
                "background_instruction": background_instruction,
                "idempotency_key": idempotency_key,
            }
        )
        return self.notification_id


class _Wording:
    def __init__(self, wording: QuestionWording | None) -> None:
        self.wording = wording

    async def __call__(
        self, *, pod_id: UUID, decider: str, question: str
    ) -> QuestionWording | None:
        return self.wording


def _question() -> TriageQuestion:
    return TriageQuestion(
        pod_id=uuid4(),
        recipient_user_id=uuid4(),
        schedule_id=uuid4(),
        schedule_name="support-inbox",
        run_id=uuid4(),
        decision_id=uuid4(),
        decider="inbox-triage",
        question="action",
        routes={
            "urgent": TriageRoute.ACT,
            "fyi": TriageRoute.DIGEST,
            "unsure": TriageRoute.ASK,
            "spam": TriageRoute.IGNORE,
        },
        evidence='{"subject":"Refund still missing"}',
    )


async def test_the_person_is_asked_the_deciders_question_with_what_each_answer_does():
    sent = _Sent(uuid4())
    questions = NotificationTriageQuestions(
        send=sent,
        wording_of=_Wording(
            QuestionWording(
                prompt="What should Kit do with this email?",
                labels={
                    "urgent": "A customer is waiting.",
                    "fyi": "Worth knowing.",
                    "unsure": "Needs a person.",
                    "spam": "Promotions.",
                },
            )
        ),
    )
    question = _question()

    notification_id = await questions.ask(question)

    assert notification_id == sent.notification_id
    [call] = sent.calls
    assert call["recipient_user_id"] == question.recipient_user_id
    assert call["title"] == "support-inbox: What should Kit do with this email?"
    body = str(call["body"])
    assert "Refund still missing" in body
    assert "- urgent: A customer is waiting. (handled now)" in body
    assert "- fyi: Worth knowing. (saved for the next digest)" in body
    assert "- spam: Promotions. (skipped)" in body
    # Choosing an option routed to ask would only ask again, so it is not offered.
    assert "unsure" not in body
    assert call["idempotency_key"] == f"schedule-ask:{question.run_id}"
    action = call["action"]
    assert isinstance(action, dict) and action["type"] == "CHOICE"
    asked = TriageAsk.model_validate(action)
    assert asked.run_id == question.run_id
    assert asked.decision_id == question.decision_id
    assert [option.key for option in asked.options] == ["urgent", "fyi", "spam"]
    assert asked.route_for("fyi") is TriageRoute.DIGEST
    assert "data.answer" in str(call["background_instruction"])


async def test_a_decider_gone_since_still_gets_an_answerable_question():
    sent = _Sent(None)
    questions = NotificationTriageQuestions(send=sent, wording_of=_Wording(None))

    assert await questions.ask(_question()) is None

    [call] = sent.calls
    assert call["title"] == "support-inbox: What should happen with this event?"
    asked = TriageAsk.model_validate(call["action"])
    assert [option.label for option in asked.options] == ["urgent", "fyi", "spam"]
