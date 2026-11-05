"""A tool result tells the model when it came back.

A long conversation keeps every result it ever read, and nothing in an old one
said it was old: the agent picked from a list of a table's rows read days
before, and never saw the rows added since. Each result the model is sent now
opens with the time it came back -- from the result's stored time, so a replay
sends the bytes the run sent, and in the request only, so the history keeps what
the tool returned.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic_ai import Agent
from pydantic_ai.capabilities import ProcessHistory
from pydantic_ai.messages import (
    BinaryContent,
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    ToolSearchReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.modules.agent.domain.entities import Message
from app.modules.agent.domain.value_objects import MessageKind, MessageRole
from app.modules.agent.infrastructure.harness.lemma.history_replay import (
    history_and_prompt,
)
from app.modules.agent.infrastructure.harness.lemma.mock_model import (
    _resolve_references,
)
from app.modules.agent.infrastructure.harness.lemma.run_log import decode, encode
from app.modules.agent.infrastructure.harness.lemma.stale_tool_output import (
    BOUNDARY_BLOCK,
    RECENT_MESSAGES,
    clip_stale_tool_returns,
)
from app.modules.agent.infrastructure.harness.lemma.tool_return_times import (
    ToolReturnTimes,
    return_time_note,
    with_return_times,
    without_return_time,
)

pytestmark = pytest.mark.unit

_MONDAY = datetime(2026, 11, 3, 14, 2, 5, 123456, tzinfo=timezone.utc)
_NOTE = "<notes>Returned 2026-11-03T14:02:05Z (UTC).</notes>"
_ROWS = {"rows": [{"industry": "Fintech"}, {"industry": "Retail"}]}


def _read(call_id: str, content: object, **fields: object) -> list[ModelMessage]:
    return [
        ModelResponse(
            parts=[ToolCallPart(tool_name="query_table", args={}, tool_call_id=call_id)]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="query_table",
                    content=content,
                    tool_call_id=call_id,
                    timestamp=_MONDAY,
                    **fields,  # type: ignore[arg-type]
                )
            ]
        ),
    ]


def _returned(messages: list[ModelMessage]) -> list[ToolReturnPart]:
    return [
        part
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]


def test_a_result_opens_with_its_stored_time_then_what_the_tool_returned() -> None:
    history = _read("c1", _ROWS)
    original = _returned(history)[0]

    (part,) = _returned(with_return_times(history))

    assert part.content == f"{_NOTE}\n\n{original.model_response_str()}"
    # The history passed in keeps what the tool returned, and its type.
    assert _returned(history)[0].content == _ROWS


def test_the_note_is_absolute_and_utc() -> None:
    india = timezone(timedelta(hours=5, minutes=30))

    assert return_time_note(datetime(2026, 11, 3, 14, 2, 5)) == _NOTE
    assert return_time_note(datetime(2026, 11, 3, 19, 32, 5, tzinfo=india)) == _NOTE


def test_a_replay_from_the_log_sends_the_same_bytes() -> None:
    history = [ModelRequest(parts=[UserPromptPart(content="go")]), *_read("c1", _ROWS)]
    wire = ModelMessagesTypeAdapter.dump_json
    sent = wire(with_return_times(history))

    replayed = decode([encode(history)])
    replayed_again = decode([encode(replayed)])

    assert wire(with_return_times(replayed)) == sent
    assert wire(with_return_times(replayed_again)) == sent


def test_noting_twice_is_noting_once() -> None:
    once = with_return_times(_read("c1", "two rows"))

    assert with_return_times(once) == once
    assert without_return_time(_returned(once)[0].content) == "two rows"


def test_a_clipped_result_keeps_its_note() -> None:
    big = "Fintech\n" * 2_000
    history: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(content="go")]),
        *_read("c0", big),
    ]
    for step in range(1, RECENT_MESSAGES + BOUNDARY_BLOCK):
        history.extend(_read(f"c{step}", "small"))

    clipped = clip_stale_tool_returns(history)
    first = _returned(with_return_times(clipped))[0]

    assert first.content.startswith(f"{_NOTE}\n\nFintech\n")
    assert "characters of this earlier query_table result are not shown" in str(
        first.content
    )


def test_a_failed_call_or_a_file_is_left_as_it_was() -> None:
    failed = _read("c1", "no such table", outcome="failed")
    image = _read(
        "c2", [BinaryContent(data=b"\x89PNG", media_type="image/png"), "a chart"]
    )
    history = [*failed, *image]

    assert with_return_times(history) == history


def test_a_tool_searchs_matches_stay_the_structure_pydantic_ai_reads() -> None:
    """Noted, its matches became a string, nothing found was revealed, and the
    agent's call to the tool it had just found never ran."""
    matches = {"discovered_tools": [{"name": "ask_teammate"}]}
    history: list[ModelMessage] = [
        ModelRequest(
            parts=[
                ToolSearchReturnPart(
                    tool_name="search_tools",
                    content=matches,  # type: ignore[arg-type]
                    tool_call_id="s1",
                    timestamp=_MONDAY,
                )
            ]
        )
    ]

    (part,) = _returned(with_return_times(history))

    assert isinstance(part, ToolSearchReturnPart)
    assert part.content == matches


def test_a_rebuilt_history_takes_each_result_time_from_its_row() -> None:
    """The reconstruction (no run log) rebuilds returns on every run: the
    clock would give a new time, and a new note, each time."""
    conversation_id, run_id = uuid4(), uuid4()

    def rows() -> list[Message]:
        return [
            Message(
                conversation_id=conversation_id,
                sequence=1,
                agent_run_id=run_id,
                role=MessageRole.USER,
                kind=MessageKind.TEXT,
                text="Which industries do we have?",
                created_at=_MONDAY,
            ),
            Message(
                conversation_id=conversation_id,
                sequence=2,
                agent_run_id=run_id,
                role=MessageRole.ASSISTANT,
                kind=MessageKind.TOOL_CALL,
                tool_name="query_table",
                tool_call_id="c1",
                tool_args={},
                created_at=_MONDAY,
            ),
            Message(
                conversation_id=conversation_id,
                sequence=3,
                agent_run_id=run_id,
                role=MessageRole.TOOL,
                kind=MessageKind.TOOL_RETURN,
                tool_name="query_table",
                tool_call_id="c1",
                tool_result=_ROWS,
                created_at=_MONDAY,
            ),
            Message(
                conversation_id=conversation_id,
                sequence=4,
                agent_run_id=run_id,
                role=MessageRole.ASSISTANT,
                kind=MessageKind.TEXT,
                text="Fintech and Retail.",
                created_at=_MONDAY,
            ),
        ]

    first, _ = history_and_prompt(rows())
    second, _ = history_and_prompt(rows())

    (part,) = _returned(with_return_times(first))
    (again,) = _returned(with_return_times(second))
    assert part.content.startswith(_NOTE)
    assert again.content == part.content


async def test_the_model_is_sent_the_note_and_the_history_keeps_the_result() -> None:
    """Through pydantic-ai itself, behind a processor that writes the history
    back: the note reaches the request and nothing else."""
    seen: list[list[ModelMessage]] = []

    def answer(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(list(messages))
        if len(seen) == 1:
            return ModelResponse(
                parts=[ToolCallPart(tool_name="industries", args={}, tool_call_id="c1")]
            )
        return ModelResponse(parts=[TextPart(content="Fintech and Retail.")])

    async def unchanged(messages: list[ModelMessage]) -> list[ModelMessage]:
        return list(messages)

    agent = Agent(
        FunctionModel(answer),
        capabilities=[ProcessHistory(unchanged), ToolReturnTimes()],
    )

    @agent.tool_plain
    def industries() -> dict[str, object]:
        return _ROWS

    result = await agent.run("Which industries do we have?")

    (sent,) = _returned(seen[1])
    (kept,) = _returned(result.all_messages())
    assert kept.content == _ROWS
    assert isinstance(sent.content, str)
    assert sent.content.startswith(f"{return_time_note(kept.timestamp)}\n\n")
    assert json.loads(without_return_time(sent.content)) == _ROWS


def test_a_scripted_reference_still_reads_a_noted_result() -> None:
    """The e2e mock model reads ``${call.path}`` out of what it is sent."""
    sent = with_return_times(_read("c1", _ROWS))

    assert _resolve_references("${c1.rows}", sent) == _ROWS["rows"]
