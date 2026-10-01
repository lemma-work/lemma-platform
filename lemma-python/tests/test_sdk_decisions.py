"""The decisions resources send what the API takes, and nothing it does not."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from lemma_sdk.openapi_client.api.decisions import (
    decider_test,
    decision_answer,
    decision_create,
    decision_rows,
)
from lemma_sdk.resources.decisions import PodDeciders, PodDecisions

POD = uuid4()


class RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, tuple[Any, ...], dict[str, Any]]] = []

    def call(self, endpoint: Any, *path_args: Any, **kwargs: Any) -> Any:
        self.calls.append((endpoint, path_args, kwargs))
        return None


def test_a_decision_is_personal_unless_shared() -> None:
    transport = RecordingTransport()
    PodDecisions(transport, pod_id=POD).decide(
        {"subject": "Invoice"}, decider="email-triage", subject="gmail:1"
    )
    endpoint, path_args, kwargs = transport.calls[0]
    assert endpoint is decision_create
    assert path_args == (POD,)
    assert kwargs["body"] == {
        "decider": "email-triage",
        "options": {},
        "record": True,
        "visibility": "PERSONAL",
        "state": {"subject": "Invoice"},
        "subject": "gmail:1",
    }


def test_a_decision_names_exactly_one_judgement() -> None:
    decisions = PodDecisions(RecordingTransport(), pod_id=POD)
    with pytest.raises(ValueError):
        decisions.decide({"a": 1})
    with pytest.raises(ValueError):
        decisions.decide({"a": 1}, decider="x", definition={"description": "d"})


def test_rows_carry_their_identity_and_answers_are_a_persons() -> None:
    transport = RecordingTransport()
    decisions = PodDecisions(transport, pod_id=POD)
    decisions.decide_rows(
        [{"id": "a"}], decider="triage", id_field="id", subject_prefix="leads"
    )
    decisions.answer(uuid4(), {"action": "act"})
    (rows_endpoint, _, rows_kwargs), (answer_endpoint, _, answer_kwargs) = (
        transport.calls
    )
    assert rows_endpoint is decision_rows
    assert rows_kwargs["body"]["id_field"] == "id"
    assert rows_kwargs["body"]["subject_prefix"] == "leads"
    assert answer_endpoint is decision_answer
    assert answer_kwargs["body"] == {"answers": {"action": "act"}, "by": "person"}


def test_a_trial_records_nothing() -> None:
    transport = RecordingTransport()
    PodDeciders(transport, pod_id=POD).test(
        [{"state": {"subject": "Hi"}, "expected": {"action": "ignore"}}],
        decider="triage",
    )
    endpoint, _, kwargs = transport.calls[0]
    assert endpoint is decider_test
    assert "record" not in kwargs["body"]
    assert kwargs["body"]["rows"][0]["expected"] == {"action": "ignore"}
