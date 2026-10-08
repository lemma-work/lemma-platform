from __future__ import annotations

from typing import Any
from uuid import uuid4

from lemma_sdk.openapi_client.api.decisions import decision_make
from lemma_sdk.resources.decisions import PodDecisions

POD = uuid4()
SCHEMA = {
    "type": "object",
    "properties": {
        "category": {
            "type": "string",
            "enum": ["billing", "bug"],
            "description": "What is it about?",
        },
        "urgent": {"type": "boolean", "description": "Reply today?"},
    },
}


class RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, tuple[Any, ...], dict[str, Any]]] = []

    def call(self, endpoint: Any, *path_args: Any, **kwargs: Any) -> Any:
        self.calls.append((endpoint, path_args, kwargs))
        return None


def _sent(transport: RecordingTransport) -> dict[str, Any]:
    """The body as it would go over the wire, through the generated model."""
    _, _, kwargs = transport.calls[0]
    return kwargs["body_model"].from_dict(kwargs["body"]).to_dict()


def test_a_decision_is_sent_to_its_pod_with_the_schema_intact() -> None:
    transport = RecordingTransport()

    PodDecisions(transport, pod_id=POD).make(
        instruction="Triage this email.",
        evidence={"subject": "Refund"},
        schema=SCHEMA,
    )

    endpoint, path_args, _ = transport.calls[0]
    assert endpoint is decision_make
    assert path_args == (POD,)
    assert _sent(transport) == {
        "instruction": "Triage this email.",
        "evidence": {"subject": "Refund"},
        "schema": SCHEMA,
        "priority": "background",
    }


def test_examples_and_priority_are_passed_through() -> None:
    transport = RecordingTransport()

    PodDecisions(transport, pod_id=POD).make(
        instruction="Route this call.",
        evidence="caller asks about the invoice",
        schema=SCHEMA,
        examples=[{"evidence": "refund please", "answers": {"category": "billing"}}],
        priority="interactive",
    )

    sent = _sent(transport)
    assert sent["priority"] == "interactive"
    assert sent["examples"] == [
        {"evidence": "refund please", "answers": {"category": "billing"}}
    ]
