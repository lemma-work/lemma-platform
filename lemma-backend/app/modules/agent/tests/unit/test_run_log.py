"""The run log's rules, one at a time: what is open, what is pending, what is
written as a step and what as a checkpoint."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from app.modules.agent.domain.entities import Message
from app.modules.agent.domain.message_state import MessageState
from app.modules.agent.domain.run_log import (
    RunStepKind,
    open_tool_call_ids,
    pending_rows,
)
from app.modules.agent.domain.value_objects import MessageKind, MessageRole
from app.modules.agent.infrastructure.harness.lemma.run_log import (
    INTERRUPTED,
    NOT_ANSWERED,
    STILL_OPEN,
    RunLogRecorder,
    encode,
    resume_request,
)

pytestmark = pytest.mark.unit

_CONVERSATION = uuid4()


def _row(sequence: int, **fields: object) -> Message:
    return Message.create(
        conversation_id=_CONVERSATION,
        sequence=sequence,
        agent_run_id=None,
        role=MessageRole.USER,
        **fields,  # type: ignore[arg-type]
    )


def _call(name: str, call_id: str) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(name, {}, tool_call_id=call_id)])


def _answer(name: str, call_id: str) -> ModelRequest:
    return ModelRequest(parts=[ToolReturnPart(name, "ok", tool_call_id=call_id)])


def test_a_call_is_open_until_a_request_answers_it() -> None:
    steps = [
        encode([_call("lookup", "a"), _answer("lookup", "a")]),
        encode([_call("ask_user", "b")]),
    ]

    assert open_tool_call_ids(steps) == ["b"]


def test_an_id_reused_by_a_later_call_is_open_again() -> None:
    steps = [encode([_call("lookup", "a"), _answer("lookup", "a"), _call("x", "a")])]

    assert open_tool_call_ids(steps) == ["a"]


def test_pending_is_what_no_step_carries_and_lemma_did_not_make_up() -> None:
    carried = _row(1, text="old").model_copy(update={"run_step_seq": 3})
    held = _row(2, text="held", state=MessageState(held_for_a_person=True))
    synthetic = _row(3, kind=MessageKind.NOTIFICATION, text="[x]").model_copy(
        update={"metadata": {"synthetic": True}}
    )
    fresh = _row(4, text="new")

    assert pending_rows([carried, held, synthetic, fresh], fallback=False) == [
        held,
        fresh,
    ]
    # A conversation from before the log takes all of it in, once.
    assert len(pending_rows([carried, held, synthetic, fresh], fallback=True)) == 4


def test_an_open_call_is_answered_by_its_row_or_told_what_happened() -> None:
    returned = _row(5, kind=MessageKind.TOOL_RETURN, text=None).model_copy(
        update={"role": MessageRole.TOOL, "tool_call_id": "a", "tool_result": "3rd"}
    )
    calls = [
        ToolCallPart("ask_user", {}, tool_call_id="a"),
        ToolCallPart("ask_user", {}, tool_call_id="b"),
        ToolCallPart("send", {}, tool_call_id="c"),
    ]

    def contents(moved_on: bool) -> list[object]:
        request = resume_request(
            calls,
            returns={"a": returned},
            pausing=frozenset({"ask_user"}),
            moved_on=moved_on,
        )
        assert request is not None
        return [part.content for part in request.parts]  # type: ignore[union-attr]

    assert contents(moved_on=True) == [
        "3rd",
        {"success": False, "error": NOT_ANSWERED},
        {"success": False, "error": INTERRUPTED},
    ]
    assert contents(moved_on=False)[1] == {"success": False, "error": STILL_OPEN}


def test_what_extends_the_log_is_a_step_and_a_rewrite_is_a_checkpoint() -> None:
    first = ModelRequest(parts=[UserPromptPart("hi")])
    held = ModelRequest(parts=[UserPromptPart("held")])
    recorder = RunLogRecorder(
        logged=[first],
        labels={id(held): RunStepKind.HELD},
        covers=[uuid4()],
    )
    context = ModelRequest(parts=[UserPromptPart("<run_context>now</run_context>")])
    answer = ModelResponse(parts=[TextPart("hello")])

    drafts = recorder.take([first, held, context, answer])

    assert [draft.kind for draft in drafts] == [RunStepKind.HELD, RunStepKind.STEP]
    assert drafts[0].covers and not drafts[1].covers
    # The run's context is logged where it was sent: it is history.
    assert "run_context" in drafts[1].messages
    assert recorder.take([first, held, context, answer]) == []

    rewritten = ModelRequest(parts=[UserPromptPart("[summary]")])
    (checkpoint,) = recorder.take([rewritten, answer])
    assert checkpoint.kind is RunStepKind.COMPACTION
    assert "[summary]" in checkpoint.messages and "hello" in checkpoint.messages
