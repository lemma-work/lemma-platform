"""The SSE stream must deliver the answer *while* it is being produced.

Every other agent E2E asserts on the events a run produced, in order, once it
finished. None of them asserted that anything arrived *before* the end — so a
harness that buffered the whole response and flushed it at completion passed the
entire suite, while the UI showed a spinner until the user reloaded the page.

These tests pin the property the UI actually depends on: tokens reach the client
incrementally, they reconstruct the assistant's text exactly, and they arrive
before the durable `message` frame that supersedes them.
"""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest
from fastapi import status

from app.modules.agent.tests.e2e.test_agent_hermetic_journeys_e2e import (
    _create_mock_agent,
    _create_pod,
    _create_runtime_profile,
)
from app.modules.test_support.e2e.scripted_model import script_text, script_tool_call

pytestmark = pytest.mark.e2e

# Long enough that a working stream must split it into several frames, so
# "streamed" and "sent once at the end" cannot both satisfy the assertions.
_ANSWER = (
    "Ashwin retired holding 537 Test wickets from 106 matches, the second "
    "highest for India. He reached 500 in 98 Tests, the second fastest to that "
    "mark, and took 37 five-wicket hauls along the way."
)


class _Frame:
    """One SSE frame plus when it arrived, relative to the first one."""

    __slots__ = ("type", "kind", "data", "at")

    def __init__(self, payload: dict, at: float) -> None:
        self.type = str(payload.get("type", ""))
        self.kind = payload.get("kind")
        self.data = payload.get("data")
        self.at = at

    def __repr__(self) -> str:  # pragma: no cover - failure output only
        body = str(self.data)
        preview = body if len(body) <= 40 else f"{body[:40]}..."
        return f"<{self.type}/{self.kind or '-'} @{self.at:.2f}s {preview!r}>"


async def _stream_frames(
    authenticated_client,
    pod_id: str,
    conversation_id: str,
    content: str,
) -> list[_Frame]:
    """Send a message and record every SSE frame with its arrival time."""
    frames: list[_Frame] = []
    url = f"/pods/{pod_id}/conversations/{conversation_id}/messages"
    async with authenticated_client.stream(
        "POST",
        url,
        json={"content": content, "metadata": {"client": "sse-streaming-e2e"}},
        timeout=60,
    ) as response:
        assert response.status_code == status.HTTP_200_OK, await response.aread()
        started = asyncio.get_running_loop().time()
        async with asyncio.timeout(45):
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = json.loads(line.removeprefix("data: "))
                at = asyncio.get_running_loop().time() - started
                frames.append(_Frame(payload, at))
                if payload["type"] in {"completed", "stopped", "error"}:
                    break
    return frames


def _text_tokens(frames: list[_Frame]) -> list[_Frame]:
    return [f for f in frames if f.type == "token" and (f.kind or "text") == "text"]


def _messages_of_kind(frames: list[_Frame], kind: str, role: str) -> list[_Frame]:
    """Durable message frames of one kind.

    `kind` arrives as the enum value (`TEXT`, `TOOL_RETURN`) while `role` is
    lowercase — an inconsistency in the wire format that clients have to know
    about, so it is matched case-insensitively here and asserted on explicitly
    in `test_the_wire_format_is_what_clients_parse`.
    """
    matched: list[_Frame] = []
    for frame in frames:
        if frame.type != "message" or not isinstance(frame.data, dict):
            continue
        if str(frame.data.get("kind", "")).lower() != kind:
            continue
        if str(frame.data.get("role", "")).lower() != role:
            continue
        matched.append(frame)
    return matched


async def _conversation(authenticated_client, fixed_test_org, e2e_settings, script):
    runtime = await _create_runtime_profile(
        authenticated_client, fixed_test_org, e2e_settings
    )
    pod = await _create_pod(authenticated_client, fixed_test_org)
    agent = await _create_mock_agent(
        authenticated_client,
        pod_id=pod["id"],
        runtime_profile_id=runtime["id"],
        name_prefix="sse_streaming",
    )
    response = await authenticated_client.post(
        f"/pods/{pod['id']}/conversations",
        json={
            "agent_name": agent["name"],
            "title": f"SSE streaming {uuid4().hex[:6]}",
            "metadata": {"mock_llm_script": script},
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return pod["id"], response.json()["id"]


@pytest.mark.asyncio
async def test_the_answer_streams_as_tokens_before_it_is_persisted(
    authenticated_client,
    fixed_test_org,
    e2e_settings,
    worker,
):
    """Tokens arrive incrementally and rebuild the answer exactly.

    Three separate failures are covered here, and they look identical from the
    UI: no tokens at all, tokens whose concatenation does not match what was
    saved, and a durable message that lands before the tokens it supersedes.
    """
    del worker
    pod_id, conversation_id = await _conversation(
        authenticated_client, fixed_test_org, e2e_settings, [script_text(_ANSWER)]
    )

    frames = await _stream_frames(
        authenticated_client, pod_id, conversation_id, "How did Ashwin's career end?"
    )

    tokens = _text_tokens(frames)
    assert tokens, f"no token frames reached the client: {frames}"
    assert len(tokens) > 1, (
        "the whole answer arrived in one frame -- it was buffered, not streamed: "
        f"{frames}"
    )
    assert "".join(str(f.data) for f in tokens) == _ANSWER

    # The durable message supersedes the streamed bubble, so it must come after
    # it. Arriving first would make the UI render the answer twice.
    assistant = _messages_of_kind(frames, "text", "assistant")
    assert [str(f.data["text"]) for f in assistant] == [_ANSWER], frames
    assert frames.index(tokens[-1]) < frames.index(assistant[0]), frames

    assert frames[-1].type == "completed", frames


@pytest.mark.asyncio
async def test_the_wire_format_is_what_clients_parse(
    authenticated_client,
    fixed_test_org,
    e2e_settings,
    worker,
):
    """Pin the frame shape the SDK's `parseAssistantStreamEvent` depends on.

    Nothing else asserts it, so a serialization change — `kind` losing its enum
    casing, a token carrying a dict instead of a string — would ship green and
    show up as a chat pane that silently renders nothing.
    """
    del worker
    pod_id, conversation_id = await _conversation(
        authenticated_client, fixed_test_org, e2e_settings, [script_text(_ANSWER)]
    )

    frames = await _stream_frames(
        authenticated_client, pod_id, conversation_id, "Tell me about Ashwin."
    )

    for token in _text_tokens(frames):
        assert isinstance(token.data, str), token
        assert token.kind == "text", token

    for frame in frames:
        if frame.type != "message":
            continue
        # The SDK drops any message missing one of these three, silently.
        assert isinstance(frame.data, dict)
        for field in ("id", "role", "kind"):
            assert isinstance(frame.data.get(field), str), (field, frame.data)
        assert frame.data["kind"].isupper(), frame.data["kind"]
        assert frame.data["role"].islower(), frame.data["role"]

    completed = frames[-1]
    assert completed.type == "completed"
    assert isinstance(completed.data, dict)
    assert completed.data["status"] == "COMPLETED", completed.data


@pytest.mark.asyncio
async def test_tool_calls_stream_too_and_do_not_stall_the_text_after_them(
    authenticated_client,
    fixed_test_org,
    e2e_settings,
    worker,
):
    """A turn that calls a tool still streams, on both sides of the call.

    The tool-call path is where buffering bugs hide: the harness holds durable
    writes until a model response completes, and a tool call is what splits one
    turn into two responses.
    """
    del worker
    preamble = "Let me look that up in the workspace before answering properly."
    pod_id, conversation_id = await _conversation(
        authenticated_client,
        fixed_test_org,
        e2e_settings,
        [
            script_tool_call("todo_write", {"items": []}, text=preamble),
            script_text(_ANSWER),
        ],
    )

    frames = await _stream_frames(
        authenticated_client, pod_id, conversation_id, "Check and then tell me."
    )

    tokens = _text_tokens(frames)
    assert "".join(str(f.data) for f in tokens) == preamble + _ANSWER

    # The discriminating assertion: text the model produced *before* the tool
    # ran reached the client before the tool's own result did. A harness that
    # held its output until the run ended would put every token after this
    # frame, while still satisfying "tokens arrived in order".
    tool_frames = _messages_of_kind(frames, "tool_return", "tool")
    assert tool_frames, frames
    first_tool = frames.index(tool_frames[0])
    streamed_before_tool = "".join(
        str(f.data) for f in tokens if frames.index(f) < first_tool
    )
    assert streamed_before_tool == preamble, frames

    assert frames[-1].type == "completed", frames
