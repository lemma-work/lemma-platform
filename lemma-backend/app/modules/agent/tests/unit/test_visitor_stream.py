"""What a visitor's page is sent of a run, frame by frame.

The member's stream carries the model's thinking, tool calls and private notes.
A visitor sees the answer being written, "typing" while the run does anything
else, the finished message and "done" -- and from a private run, nothing.
"""

from __future__ import annotations

import json

import pytest

from app.modules.agent.contracts.visitor_stream import _translate, visitor_frame

pytestmark = pytest.mark.unit

RUN = "01a10c3e-0000-7000-8000-000000000001"


def _token(text: str, kind: str = "text") -> dict:
    return {"type": "token", "kind": kind, "agent_run_id": RUN, "data": text}


def _message(**fields) -> dict:
    return {
        "type": "message",
        "agent_run_id": RUN,
        "data": {"role": "assistant", "kind": "TEXT", "sequence": 4, **fields},
    }


def test_the_answer_is_streamed_as_it_is_written():
    assert visitor_frame(_token("Hel")) == {"type": "delta", "text": "Hel"}


def test_thinking_and_tool_traffic_only_say_typing():
    assert visitor_frame(_token("I should check the table", "thinking")) == {
        "type": "typing"
    }
    assert visitor_frame(_token("{}", "tool")) == {"type": "typing"}
    tool_call = _message(kind="TOOL_CALL", tool_name="pod_tables", text=None)
    assert visitor_frame(tool_call) == {"type": "typing"}


def test_a_tool_result_and_a_thinking_message_send_nothing():
    assert visitor_frame(_message(kind="THINKING", text="private")) is None
    result = {
        "type": "message",
        "data": {"role": "tool", "kind": "TOOL_RETURN", "text": "rows"},
    }
    assert visitor_frame(result) is None


def test_the_finished_answer_carries_its_text_and_sequence():
    frame = visitor_frame(_message(text="Here is our price list."))
    assert frame == {
        "type": "message",
        "text": "Here is our price list.",
        "sequence": 4,
    }


def test_a_private_note_never_reaches_the_page():
    note = _message(text="Ask Ana about the refund", metadata={"private_note": True})
    assert visitor_frame(note) is None


def test_the_visitors_own_message_is_not_echoed():
    mine = {"type": "message", "data": {"role": "user", "kind": "TEXT", "text": "hi"}}
    assert visitor_frame(mine) is None


def test_a_retry_resets_and_the_end_says_done():
    assert visitor_frame(_token("", "stream_reset")) == {"type": "reset"}
    for terminal in ("completed", "error", "stopped"):
        assert visitor_frame({"type": terminal, "data": {"secret": 1}}) == {
            "type": "done"
        }


def test_frames_nobody_outside_needs_send_nothing():
    assert visitor_frame({"type": "title_updated", "data": "Refund for Ana"}) is None
    assert visitor_frame(_token("", "text")) is None


async def test_every_frame_of_a_private_run_is_dropped():
    async def private(run_id: str) -> bool:
        return run_id == RUN

    assert await _translate(json.dumps(_token("secret")), private) is None
    assert (
        await _translate(json.dumps({"type": "status", "agent_run_id": RUN}), private)
        is None
    )


async def test_a_public_run_and_a_garbled_frame():
    async def public(run_id: str) -> bool:
        return False

    assert await _translate(json.dumps(_token("ok")), public) == {
        "type": "delta",
        "text": "ok",
    }
    assert await _translate("not json", public) is None


def _fill_return(result: object) -> dict:
    return {
        "type": "message",
        "agent_run_id": RUN,
        "data": {
            "role": "tool",
            "kind": "TOOL_RETURN",
            "tool_name": "fill_form",
            "tool_result": result,
        },
    }


def test_a_filled_form_reaches_the_page_from_the_checked_result():
    frame = visitor_frame(
        _fill_return(
            {"success": True, "table": "signups", "filled": {"full_name": "Priya"}}
        )
    )
    assert frame == {
        "type": "fill",
        "table": "signups",
        "values": {"full_name": "Priya"},
    }


def test_a_failed_fill_or_another_tools_result_sends_nothing():
    assert visitor_frame(_fill_return({"success": False, "forms": []})) is None
    other = _fill_return({"success": True, "table": "t", "filled": {"a": 1}})
    other["data"]["tool_name"] = "pod_query"
    assert visitor_frame(other) is None
