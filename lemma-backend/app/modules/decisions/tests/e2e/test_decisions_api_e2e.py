"""Decisions over HTTP, end to end: define, ask, ask again, correct, version, test.

No Typesafe key is configured in e2e and every model is the deterministic mock,
so each answer here has one knowable source: a rule, or the model rung's
smallest valid answer (the first option). The trace says which, and so do these
assertions.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.e2e

TRIAGE = {
    "description": "What Kit does with each new email.",
    "guidance": "Kit files invoices. Anything legal goes to a person.",
    "input": {"fields": ["from", "subject", "labels"], "max_chars": 2000},
    "questions": {
        "action": {
            "type": "choice",
            "prompt": "What should Kit do with this email?",
            "options": {
                "act": "A customer is waiting, or an invoice needs filing.",
                "ask": "Needs a person.",
                "ignore": "Newsletters and promotions.",
            },
            "fallback": "ask",
        }
    },
    "rules": [
        {"when": "contains(labels, 'PROMOTIONS')", "answer": {"action": "ignore"}}
    ],
}
PROMOTION = {
    "from": "deals@shop.example",
    "subject": "50% off",
    "labels": ["PROMOTIONS"],
    "body": "never sent anywhere",
}
INVOICE = {"from": "billing@acme.example", "subject": "Invoice 42", "labels": ["INBOX"]}


async def _define(client, pod_id: str, name: str = "email-triage") -> dict:
    response = await client.post(
        f"/pods/{pod_id}/deciders", json={"name": name, "definition": TRIAGE}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_a_rule_answers_and_the_same_subject_is_asked_once(
    authenticated_client, test_pod
) -> None:
    pod_id = test_pod["id"]
    decider = await _define(authenticated_client, pod_id)
    assert decider["version"] == 1

    subject = f"gmail:{uuid4().hex}"
    first = await authenticated_client.post(
        f"/pods/{pod_id}/decisions",
        json={"decider": "email-triage", "state": PROMOTION, "subject": subject},
    )
    assert first.status_code == 200, first.text
    decision = first.json()
    assert decision["answers"]["action"]["value"] == "ignore"
    assert decision["answers"]["action"]["by"] == "rules"
    assert decision["open"] == []
    assert decision["decider_version"] == 1
    # Evidence is whatever was decided about, so a decision is the asker's own
    # unless they share it with the pod.
    assert decision["visibility"] == "PERSONAL"
    assert "never sent anywhere" not in (decision["evidence"] or "")

    again = await authenticated_client.post(
        f"/pods/{pod_id}/decisions",
        json={
            "decider": "email-triage",
            "state": {**PROMOTION, "labels": ["INBOX"]},
            "subject": subject,
        },
    )
    assert again.json()["id"] == decision["id"]
    assert again.json()["answers"]["action"]["value"] == "ignore"


async def test_without_system_one_the_model_rung_answers(
    authenticated_client, test_pod
) -> None:
    pod_id = test_pod["id"]
    await _define(authenticated_client, pod_id)
    response = await authenticated_client.post(
        f"/pods/{pod_id}/decisions",
        json={"decider": "email-triage", "state": INVOICE},
    )
    assert response.status_code == 200, response.text
    decision = response.json()
    rungs = {step["rung"]: step["outcome"] for step in decision["trace"]}
    assert rungs["system_one"] == "not_configured"
    assert decision["answers"]["action"]["by"] == "model"
    assert decision["answers"]["action"].get("confidence") is None


async def test_a_persons_correction_is_recorded_and_teaches(
    authenticated_client, test_pod, db_session
) -> None:
    pod_id = test_pod["id"]
    await _define(authenticated_client, pod_id)
    decision = (
        await authenticated_client.post(
            f"/pods/{pod_id}/decisions",
            json={"decider": "email-triage", "state": INVOICE},
        )
    ).json()
    corrected = await authenticated_client.post(
        f"/pods/{pod_id}/decisions/{decision['id']}/answer",
        json={"answers": {"action": "ask"}},
    )
    assert corrected.status_code == 200, corrected.text
    body = corrected.json()
    assert body["answers"]["action"]["value"] == "ask"
    assert body["answers"]["action"]["by"] == "person"
    expected_status = (
        "corrected"
        if decision["answers"]["action"]["value"] != "ask"
        else decision["status"]
    )
    assert body["status"] == expected_status

    examples = await db_session.execute(
        text(
            "SELECT question_key, value, source FROM decision_examples "
            "WHERE decision_id = :decision_id"
        ),
        {"decision_id": decision["id"]},
    )
    assert [tuple(row) for row in examples.all()] == [("action", "ask", "person")]

    invalid = await authenticated_client.post(
        f"/pods/{pod_id}/decisions/{decision['id']}/answer",
        json={"answers": {"action": "escalate"}},
    )
    assert invalid.status_code == 422


async def test_a_new_version_keeps_the_old_one(authenticated_client, test_pod) -> None:
    pod_id = test_pod["id"]
    await _define(authenticated_client, pod_id)
    updated = await authenticated_client.put(
        f"/pods/{pod_id}/deciders/email-triage",
        json={
            "definition": {**TRIAGE, "guidance": "Kit also answers order questions."}
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == 2
    versions = await authenticated_client.get(
        f"/pods/{pod_id}/deciders/email-triage/versions"
    )
    assert [item["version"] for item in versions.json()["items"]] == [2, 1]
    duplicate = await authenticated_client.post(
        f"/pods/{pod_id}/deciders", json={"name": "email-triage", "definition": TRIAGE}
    )
    assert duplicate.status_code == 409


async def test_inline_questions_rows_and_a_test_run(
    authenticated_client, test_pod
) -> None:
    pod_id = test_pod["id"]
    inline = {
        "description": "Is this about money?",
        "questions": {"money": {"type": "yes_no", "prompt": "Is it about money?"}},
        "rules": [
            {
                "phrases": ["invoice", "refund"],
                "field": "subject",
                "answer": {"money": True},
            }
        ],
    }
    unrecorded = await authenticated_client.post(
        f"/pods/{pod_id}/decisions",
        json={"definition": inline, "state": {"subject": "Invoice"}, "record": False},
    )
    assert unrecorded.status_code == 200, unrecorded.text
    assert unrecorded.json()["answers"]["money"]["value"] is True
    missing = await authenticated_client.get(
        f"/pods/{pod_id}/decisions/{unrecorded.json()['id']}"
    )
    assert missing.status_code == 404

    rows = await authenticated_client.post(
        f"/pods/{pod_id}/decisions/rows",
        json={
            "definition": inline,
            "rows": [
                {"id": "a", "subject": "Refund"},
                {"id": "b", "subject": "invoice"},
                {"id": "c", "subject": "Lunch"},
            ],
            "id_field": "id",
        },
    )
    assert rows.status_code == 200, rows.text
    body = rows.json()
    assert [row["row_id"] for row in body["rows"]] == ["a", "b", "c"]
    assert body["counts"]["money"]["true"] == 2

    await _define(authenticated_client, pod_id)
    trial = await authenticated_client.post(
        f"/pods/{pod_id}/deciders/test",
        json={
            "decider": "email-triage",
            "rows": [
                {"state": PROMOTION, "expected": {"action": "ignore"}},
                {"state": PROMOTION, "expected": {"action": "act"}},
            ],
        },
    )
    assert trial.status_code == 200, trial.text
    assert trial.json()["agreement"]["action"] == {"agreed": 1, "total": 2}
    assert len(trial.json()["disagreements"]) == 1


async def test_a_stranger_can_neither_ask_nor_read(
    authenticated_client, async_client_for_stranger, test_pod
) -> None:
    pod_id = test_pod["id"]
    await _define(authenticated_client, pod_id)
    decision = (
        await authenticated_client.post(
            f"/pods/{pod_id}/decisions",
            json={"decider": "email-triage", "state": PROMOTION},
        )
    ).json()
    asked = await async_client_for_stranger.post(
        f"/pods/{pod_id}/decisions",
        json={"decider": "email-triage", "state": PROMOTION},
    )
    assert asked.status_code in (403, 404)
    read = await async_client_for_stranger.get(
        f"/pods/{pod_id}/decisions/{decision['id']}"
    )
    assert read.status_code in (403, 404)


async def test_deleting_a_decider_keeps_its_decisions(
    authenticated_client, test_pod
) -> None:
    pod_id = test_pod["id"]
    await _define(authenticated_client, pod_id)
    decision = (
        await authenticated_client.post(
            f"/pods/{pod_id}/decisions",
            json={"decider": "email-triage", "state": PROMOTION},
        )
    ).json()
    deleted = await authenticated_client.delete(f"/pods/{pod_id}/deciders/email-triage")
    assert deleted.status_code == 204
    gone = await authenticated_client.post(
        f"/pods/{pod_id}/decisions",
        json={"decider": "email-triage", "state": PROMOTION},
    )
    assert gone.status_code == 404
    kept = await authenticated_client.get(f"/pods/{pod_id}/decisions/{decision['id']}")
    assert kept.status_code == 200
