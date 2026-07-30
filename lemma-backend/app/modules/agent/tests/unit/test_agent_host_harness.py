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
        harness_key="codex",
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

    assert first == []
    assert second == []
    assert terminal[0].type is AgentEventType.TOKEN
    assert terminal[0].data == {"kind": "text", "data": "hello world"}
    message_events = [
        event for event in terminal if event.type is AgentEventType.MESSAGE
    ]
    assert len(message_events) == 1
    assert isinstance(message_events[0].data, MessageDraft)
    assert message_events[0].data.text == "hello world"
    assert terminal[-1].type is AgentEventType.COMPLETED


def test_normalizer_does_not_stringify_non_text_acp_content() -> None:
    normalizer = AgentHostEventNormalizer(
        agent_run_id=uuid4(),
        model_name="gpt-test",
    )

    events = normalizer.normalize(
        _row(
            sequence=1,
            event_type="agent_message_chunk",
            object_id="image-1",
            payload={
                "content": {
                    "type": "image",
                    "data": "base64-is-handled-before-normalization",
                    "mimeType": "image/png",
                }
            },
        )
    )

    assert events == []

    rendered = "![Generated image](/me/c/test/agent-output/image.png)"
    streamed = normalizer.normalize(
        _row(
            sequence=2,
            event_type="agent_message_chunk",
            object_id="image-1",
            payload={"content": {"type": "image"}},
        ),
        payload_override={"text": rendered},
    )
    terminal = normalizer.normalize(
        _row(
            sequence=3,
            event_type="terminal",
            object_id=None,
            payload={"state": "SUCCEEDED"},
        )
    )

    assert streamed == []
    assert terminal[0].data == {"kind": "text", "data": rendered}
    final_message = next(
        event.data for event in terminal if event.type is AgentEventType.MESSAGE
    )
    assert final_message.text == rendered


def test_normalizer_batches_word_sized_acp_deltas_for_the_ui() -> None:
    normalizer = AgentHostEventNormalizer(
        agent_run_id=uuid4(),
        model_name="gpt-test",
    )

    events = []
    for sequence, text in enumerate(
        ("I ", "am ", "streaming ", "one ", "readable ", "chunk."),
        start=1,
    ):
        events.extend(
            normalizer.normalize(
                _row(
                    sequence=sequence,
                    event_type="agent_message_chunk",
                    object_id=f"codex-content-{sequence}",
                    payload={"text": text},
                )
            )
        )

    assert len(events) == 1
    assert events[0].type is AgentEventType.TOKEN
    assert events[0].data == {
        "kind": "text",
        "data": "I am streaming one readable chunk.",
    }
    terminal = normalizer.normalize(
        _row(
            sequence=7,
            event_type="terminal",
            object_id=None,
            payload={"state": "SUCCEEDED"},
        )
    )
    final_messages = [
        event.data
        for event in terminal
        if event.type is AgentEventType.MESSAGE
        and getattr(event.data, "text", None)
    ]
    assert len(final_messages) == 1
    assert final_messages[0].text == "I am streaming one readable chunk."


def test_normalizer_preserves_order_when_acp_switches_stream_objects() -> None:
    normalizer = AgentHostEventNormalizer(
        agent_run_id=uuid4(),
        model_name="gpt-test",
    )

    first = normalizer.normalize(
        _row(
            sequence=1,
            event_type="agent_message_chunk",
            object_id="message-1",
            payload={"text": "first fragment"},
        )
    )
    switched = normalizer.normalize(
        _row(
            sequence=2,
            event_type="agent_thought_chunk",
            object_id="thought-1",
            payload={"text": "then thinking"},
        )
    )

    assert first == []
    assert [event.data for event in switched] == [
        {"kind": "text", "data": "first fragment"}
    ]


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


def test_normalizer_preserves_acp_tool_title_input_and_bounded_output() -> None:
    normalizer = AgentHostEventNormalizer(
        agent_run_id=uuid4(),
        model_name="gpt-test",
    )

    call = normalizer.normalize(
        _row(
            sequence=1,
            event_type="tool_call_upsert",
            object_id="image-call",
            payload={
                "title": "Image generation",
                "status": "in_progress",
                "rawInput": {"prompt": "A useful poster"},
            },
        )
    )
    returned = normalizer.normalize(
        _row(
            sequence=2,
            event_type="tool_call_update",
            object_id="image-call",
            payload={
                "status": "completed",
                "rawOutput": {"result": "x" * 10_000},
            },
        )
    )

    invocation = call[0].data
    assert invocation.tool_name == "Image generation"
    assert invocation.tool_args == {"prompt": "A useful poster"}
    assert invocation.metadata["tool_title"] == "Image generation"
    result = returned[0].data
    assert result.tool_name == "Image generation"
    assert result.tool_result == {
        "result": {
            "omitted": "large tool payload",
            "character_count": 10_000,
        }
    }


def test_normalizer_maps_acp_execute_to_terminal_command() -> None:
    normalizer = AgentHostEventNormalizer(
        agent_run_id=uuid4(),
        model_name="gpt-test",
    )

    call = normalizer.normalize(
        _row(
            sequence=1,
            event_type="tool_call_upsert",
            object_id="command-call",
            payload={
                "kind": "execute",
                "title": "python -c 'print(42)'",
                "status": "in_progress",
                "rawInput": {"command": "python -c 'print(42)'"},
            },
        )
    )
    returned = normalizer.normalize(
        _row(
            sequence=2,
            event_type="tool_call_update",
            object_id="command-call",
            payload={
                "status": "completed",
                "rawOutput": {"exit_code": 0, "formatted_output": "42\n"},
            },
        )
    )

    invocation = call[0].data
    assert invocation.tool_name == "exec_command"
    assert invocation.tool_args == {"cmd": "python -c 'print(42)'"}
    assert invocation.metadata["tool_title"] == "python -c 'print(42)'"
    assert invocation.metadata["tool_kind"] == "execute"
    assert returned[0].data.tool_result == {
        "exit_code": 0,
        "formatted_output": "42\n",
    }


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
