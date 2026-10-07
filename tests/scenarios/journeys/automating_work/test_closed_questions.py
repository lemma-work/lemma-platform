"""Automating work → judging one piece of evidence with closed questions.

Nothing here needs a sandbox or a real model: the deployment's stand-in model
answers with an allowed value, which is exactly what is promised. What a real
model concludes about a real email is the provider's business; that every
answer is one the question allows, and that a question which is not closed is
refused, is the product's.
"""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario

pytestmark = [
    journey("Automating work"),
    capability("Judge something with a closed question"),
]

QUESTIONS = {
    "type": "object",
    "properties": {
        "category": {
            "type": "string",
            "oneOf": [
                {"const": "billing", "description": "Charges, invoices, refunds"},
                {"const": "bug", "description": "Something in the product is broken"},
                {"const": "other", "description": "Anything else"},
            ],
            "description": "What is this support email about?",
        },
        "labels": {
            "type": "array",
            "items": {"type": "string", "enum": ["refund", "angry", "vip"]},
            "uniqueItems": True,
            "description": "Which of these apply?",
        },
        "urgent": {"type": "boolean", "description": "Does it need a reply today?"},
        "severity": {
            "type": "integer",
            "minimum": 1,
            "maximum": 3,
            "description": "How bad is it, 1 cosmetic to 3 blocking?",
        },
    },
}
EMAIL = {
    "from": "finance@customer.example.com",
    "subject": "Charged twice this month",
    "body": "We were billed twice for the same plan. Please refund one.",
}


@pytest.fixture
async def team(world, run):
    alice = await world.person("priya")
    pod = await alice.creates_a_pod(named=run.name("judging"))
    outsider = await world.person("hannah")
    try:
        yield alice, outsider, pod
    finally:
        await alice.deletes_pod(pod)


def _ask(evidence: object, schema: object) -> dict[str, object]:
    return {
        "instruction": "Triage incoming support email for the billing team.",
        "evidence": evidence,
        "schema": schema,
    }


@scenario("A member asks closed questions and gets one allowed answer each")
@proves("PS-FUNC-020")
@covers("decision.make")
async def test_every_question_gets_an_allowed_answer(team):
    alice, _outsider, pod = team

    decision = await alice.api.post(
        f"/pods/{pod['id']}/decisions",
        status=200,
        what=f"{alice.label} judging a support email",
        json=_ask(EMAIL, QUESTIONS),
    )

    answers = decision["answers"]
    assert set(answers) == {"category", "labels", "urgent", "severity"}, answers
    allowed = {
        "category": {"billing", "bug", "other"},
        "urgent": {True, False},
        "severity": {1, 2, 3},
    }
    for key, values in allowed.items():
        value = answers[key]["value"]
        assert value is None or value in values, f"{key} answered {value!r}"
    labels = answers["labels"]["value"]
    assert labels is None or set(labels) <= {"refund", "angry", "vip"}, labels


@scenario("A question that is not closed is refused, naming the question")
@proves("PS-FUNC-020")
@covers("decision.make")
async def test_an_open_question_is_refused(team):
    alice, _outsider, pod = team
    open_ended = {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "Summarise the email"},
            "refund_amount": {"type": "number", "description": "How much?"},
        },
    }

    response = await alice.api.call(
        "POST", f"/pods/{pod['id']}/decisions", json=_ask(EMAIL, open_ended)
    )

    assert response.status_code == 422, response.text
    named = {problem["path"] for problem in response.json()["details"]}
    assert {
        "schema.properties.summary",
        "schema.properties.refund_amount.type",
    } <= named, named


@scenario("Someone outside the pod cannot ask it to judge anything")
@proves("PS-FUNC-020")
@covers("decision.make")
async def test_an_outsider_is_refused(team):
    _alice, outsider, pod = team

    response = await outsider.api.call(
        "POST", f"/pods/{pod['id']}/decisions", json=_ask(EMAIL, QUESTIONS)
    )

    assert response.status_code == 403, response.text
