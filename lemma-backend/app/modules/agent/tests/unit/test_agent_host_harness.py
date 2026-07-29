from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from app.modules.agent.domain.value_objects import (
    AgentEventType,
    MessageDraft,
    MessageKind,
)
from app.modules.agent.infrastructure.harnesses.agent_host_events import (
    AgentHostEventNormalizer,
)
from app.modules.usage.contracts import AgentRunUsage


def _row(
    *,
    sequence: int,
    event_type: str,
    object_id: str | None,
    payload: dict,
) -> SimpleNamespace:
    return SimpleNamespace(
        sequence=sequence,
        type=event_type,
        object_id=object_id,
        payload=payload,
        integration_key="codex",
        adapter_version="1.2.3",
    )


def test_normalizer_streams_and_persists_one_final_message() -> None:
    run_id = uuid4()
    normalizer = AgentHostEventNormalizer(
        agent_run_id=run_id,
        model_name="gpt-test",
    )

    first = normalizer.normalize(
        _row(
            sequence=1,
            event_type="agent_message_chunk",
            object_id="message-1",
            payload={"text": "hello "},
        )
    )
    second = normalizer.normalize(
        _row(
            sequence=2,
            event_type="agent_message_upsert",
            object_id="message-1",
            payload={"text": "hello world"},
        )
    )
    terminal = normalizer.normalize(
        _row(
            sequence=3,
            event_type="terminal",
            object_id=None,
            payload={"state": "SUCCEEDED"},
        )
    )

    assert first[0].type is AgentEventType.TOKEN
    assert first[0].data == {"kind": "text", "data": "hello "}
    assert second[0].data == {"kind": "text", "data": "world"}
    message_events = [
        event for event in terminal if event.type is AgentEventType.MESSAGE
    ]
    assert len(message_events) == 1
    assert isinstance(message_events[0].data, MessageDraft)
    assert message_events[0].data.text == "hello world"
    assert terminal[-1].type is AgentEventType.COMPLETED


def test_normalizer_closes_unfinished_tool_call_before_failure() -> None:
    normalizer = AgentHostEventNormalizer(
        agent_run_id=uuid4(),
        model_name="gpt-test",
    )
    call = normalizer.normalize(
        _row(
            sequence=1,
            event_type="tool_call_upsert",
            object_id="call-1",
            payload={"name": "lemma_execute", "arguments": {"command": "pwd"}},
        )
    )
    terminal = normalizer.normalize(
        _row(
            sequence=2,
            event_type="terminal",
            object_id=None,
            payload={"state": "FAILED", "error": "adapter crashed"},
        )
    )

    assert call[0].data.kind is MessageKind.TOOL_CALL
    assert terminal[-1].type is AgentEventType.ERROR
    synthetic = terminal[-2].data
    assert synthetic.kind is MessageKind.TOOL_RETURN
    assert synthetic.tool_call_id == "call-1"
    assert synthetic.metadata["synthetic_tool_return"] is True


def test_normalizer_maps_usage_totals() -> None:
    normalizer = AgentHostEventNormalizer(
        agent_run_id=uuid4(),
        model_name="fallback-model",
    )
    events = normalizer.normalize(
        _row(
            sequence=1,
            event_type="usage_update",
            object_id=None,
            payload={
                "usage": {
                    "model_name": "provider-model",
                    "input_tokens": 120,
                    "output_tokens": 30,
                    "request_count": 2,
                }
            },
        )
    )

    assert events[0].type is AgentEventType.USAGE
    assert isinstance(events[0].data, AgentRunUsage)
    assert events[0].data.model_name == "provider-model"
    assert events[0].data.input_tokens == 120
    assert events[0].data.output_tokens == 30
    assert events[0].data.request_count == 2
