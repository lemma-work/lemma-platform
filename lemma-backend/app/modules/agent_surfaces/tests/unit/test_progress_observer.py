from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    MessageDraft,
)
from app.modules.agent_surfaces.services import progress_display, progress_observer
from app.modules.agent_surfaces.services.progress_observer import (
    SurfaceAgentRunProgressObserver,
)
from app.modules.agent_surfaces.domain.models import StreamAppendResult

pytestmark = pytest.mark.asyncio


class _UowFactory:
    def __call__(self):
        return self

    async def __aenter__(self):
        return SimpleNamespace()

    async def __aexit__(self, exc_type, exc, tb):
        return None


class _SurfaceService:
    def __init__(self, *, send_result: bool = True, finish_result: bool = True):
        self.calls = []
        self.messages = []
        self.progress = []
        self.cleared = []
        self.finished = []
        self.streamed = []
        self.send_result = send_result
        self.finish_result = finish_result

    async def send_processing_indicator_for_conversation(self, **kwargs):
        self.calls.append(kwargs)
        return self.send_result

    async def send_progress_update_for_conversation(self, **kwargs):
        self.progress.append(kwargs)
        return {"message_id": len(self.progress)}

    async def clear_progress_for_conversation(self, **kwargs):
        self.cleared.append(kwargs)
        return

    async def append_stream_text_for_conversation(self, **kwargs):
        self.streamed.append(kwargs)
        return StreamAppendResult(handle={"message_id": 1}, appended=True)

    async def finish_progress_for_conversation(self, **kwargs):
        self.finished.append(kwargs)
        return self.finish_result

    async def send_agent_message_for_conversation(self, **kwargs):
        self.messages.append(kwargs)
        return self.send_result

    async def send_display_resource_for_conversation(self, **kwargs):
        self.messages.append({"display_resource": kwargs})
        return self.send_result

    async def send_questions_for_conversation(self, **kwargs):
        self.messages.append({"questions": kwargs})
        return self.send_result

    async def send_approval_prompt_for_conversation(self, **kwargs):
        self.messages.append({"approval": kwargs})
        return self.send_result


def _observer(service: _SurfaceService) -> SurfaceAgentRunProgressObserver:
    return SurfaceAgentRunProgressObserver(
        uow_factory=_UowFactory(),
        service_factory=lambda _uow: service,
    )


async def test_progress_observer_streams_tool_comment_progress():
    """The step timeline lives on platforms that cannot stream text.

    Slack is deliberately excluded: it streams the answer itself, and a step
    appended into that stream lands mid-sentence.
    """
    service = _SurfaceService()
    observer = _observer(service)
    conversation = SimpleNamespace(
        id=uuid4(),
        metadata={"surface_platform": "TELEGRAM"},
    )
    event = AgentEvent(
        type=AgentEventType.MESSAGE,
        data=MessageDraft.of_tool_call(
            tool_name="workspace_todo_update",
            tool_call_id="tool-1",
            tool_args={"request": {"comment": "Checking the latest todo state"}},
        ),
    )

    await observer.on_event(event, conversation, SimpleNamespace())

    assert service.progress == [
        {
            "conversation_id": conversation.id,
            "progress_text": "Checking the latest todo state",
            "progress_handle": None,
        }
    ]
    assert service.calls == []
    assert service.messages == []


async def test_progress_observer_strips_thinking_from_tool_comment():
    """Regression: a tool-call comment containing model reasoning must never be
    streamed to a surface as a live progress update."""
    service = _SurfaceService()
    observer = _observer(service)
    conversation = SimpleNamespace(
        id=uuid4(),
        metadata={"surface_platform": "TELEGRAM"},
    )
    event = AgentEvent(
        type=AgentEventType.MESSAGE,
        data=MessageDraft.of_tool_call(
            tool_name="web_search",
            tool_call_id="tool-1",
            tool_args={"comment": "<think>secret reasoning</think>Reading the file"},
        ),
    )

    await observer.on_event(event, conversation, SimpleNamespace())

    assert service.progress == [
        {
            "conversation_id": conversation.id,
            "progress_text": "Reading the file",
            "progress_handle": None,
        }
    ]
    for entry in service.progress:
        assert "think" not in entry["progress_text"].lower()


async def test_progress_observer_skips_all_reasoning_tool_comment():
    """A comment that is entirely reasoning streams no progress at all."""
    service = _SurfaceService()
    observer = _observer(service)
    conversation = SimpleNamespace(
        id=uuid4(),
        metadata={"surface_platform": "SLACK"},
    )
    event = AgentEvent(
        type=AgentEventType.MESSAGE,
        data=MessageDraft.of_tool_call(
            tool_name="web_search",
            tool_call_id="tool-1",
            tool_args={"comment": "<think>all of it</think>"},
        ),
    )

    await observer.on_event(event, conversation, SimpleNamespace())

    assert service.progress == []


def _assistant(draft: MessageDraft) -> AgentEvent:
    return AgentEvent(type=AgentEventType.MESSAGE, data=draft)


async def test_progress_observer_buffers_text_and_sends_final_answer_on_finish():
    service = _SurfaceService()
    observer = _observer(service)
    conversation = SimpleNamespace(
        id=uuid4(), metadata={"surface_platform": "TELEGRAM"}
    )

    await observer.on_event(
        _assistant(MessageDraft.of_text("Final answer.")),
        conversation,
        SimpleNamespace(),
    )
    # Buffered, not sent mid-run.
    assert service.messages == []

    await observer.on_run_finished(conversation, SimpleNamespace())
    assert service.messages == [
        {"conversation_id": conversation.id, "message": "Final answer."}
    ]


async def test_progress_observer_sends_only_final_answer_not_thinking_or_tools():
    service = _SurfaceService()
    observer = _observer(service)
    conversation = SimpleNamespace(id=uuid4(), metadata={"surface_platform": "SLACK"})

    # Thinking, intermediate narration, a tool call/return, then the answer.
    await observer.on_event(
        _assistant(MessageDraft.of_thinking("Let me think about this.")),
        conversation,
        SimpleNamespace(),
    )
    await observer.on_event(
        _assistant(MessageDraft.of_text("Let me look that up.")),
        conversation,
        SimpleNamespace(),
    )
    await observer.on_event(
        _assistant(
            MessageDraft.of_tool_call(
                tool_name="web_search", tool_call_id="t1", tool_args={}
            )
        ),
        conversation,
        SimpleNamespace(),
    )
    await observer.on_event(
        AgentEvent(
            type=AgentEventType.MESSAGE,
            data=MessageDraft.of_tool_return(
                tool_name="web_search", tool_call_id="t1", tool_result="result"
            ),
        ),
        conversation,
        SimpleNamespace(),
    )
    await observer.on_event(
        _assistant(MessageDraft.of_text("The final answer is 42.")),
        conversation,
        SimpleNamespace(),
    )

    # Nothing delivered as content mid-run.
    assert service.messages == []

    await observer.on_run_finished(conversation, SimpleNamespace())

    # Exactly one delivered answer. The pre-tool narration was discarded and
    # thinking/tool content was never sent as content. No text token streamed
    # in this run, so there is no live stream to close and the answer arrives
    # as an ordinary message — see test_slack_final_answer_closes_the_live_stream
    # for the streamed case.
    assert [m["message"] for m in service.messages] == ["The final answer is 42."]
    assert service.finished == []


async def test_progress_observer_email_sends_buffered_text_when_no_reply_tool():
    # Fallback: if the agent never called the reply tool, the observer emails the
    # buffered final text so the user still gets a response.
    service = _SurfaceService()
    observer = _observer(service)
    conversation = SimpleNamespace(id=uuid4(), metadata={"surface_platform": "GMAIL"})

    await observer.on_event(
        _assistant(MessageDraft.of_text("Quick answer, no attachments.")),
        conversation,
        SimpleNamespace(),
    )
    await observer.on_run_finished(conversation, SimpleNamespace())

    assert service.messages == [
        {"conversation_id": conversation.id, "message": "Quick answer, no attachments."}
    ]


async def test_progress_observer_ignores_email_display_resource():
    # display_resource is a no-op for email surfaces — the observer no longer
    # accumulates or sends anything for it.
    service = _SurfaceService()
    observer = _observer(service)
    conversation = SimpleNamespace(
        id=uuid4(), pod_id=uuid4(), metadata={"surface_platform": "GMAIL"}
    )

    await observer.on_event(
        AgentEvent(
            type=AgentEventType.MESSAGE,
            data=MessageDraft.of_tool_return(
                tool_name="display_resource",
                tool_call_id="tool-display-2",
                tool_result={"success": True},
            ),
        ),
        conversation,
        SimpleNamespace(),
    )
    await observer.on_run_finished(conversation, SimpleNamespace())

    # No reply tool, no buffered text → nothing sent, and no display metadata.
    assert service.messages == []


async def test_progress_observer_refreshes_telegram_typing_in_process(monkeypatch):
    service = _SurfaceService()
    observer = _observer(service)
    conversation = SimpleNamespace(
        id=uuid4(),
        metadata={"surface_platform": "TELEGRAM"},
    )
    monkeypatch.setitem(
        progress_observer._TYPING_REFRESH_INTERVAL_SECONDS,
        "TELEGRAM",
        0.01,
    )

    await observer.on_run_started(conversation, SimpleNamespace())
    await asyncio.sleep(0.03)
    await observer.on_run_finished(conversation, SimpleNamespace())

    # The opening acknowledgement, then keep-alive ticks flagged as refreshes so
    # an adapter can tell them apart from it.
    assert service.calls[0] == {"conversation_id": conversation.id, "metadata": None}
    assert len(service.calls) > 1
    assert service.calls[-1] == {
        "conversation_id": conversation.id,
        "metadata": {"is_refresh": True},
    }


async def test_progress_observer_delivers_retryable_telegram_error():
    service = _SurfaceService()
    observer = _observer(service)
    conversation = SimpleNamespace(
        id=uuid4(),
        metadata={"surface_platform": "TELEGRAM"},
    )

    await observer.on_event(
        AgentEvent(type=AgentEventType.ERROR, data={"error": "provider failed"}),
        conversation,
        SimpleNamespace(),
    )
    await observer.on_run_finished(conversation, SimpleNamespace())

    assert service.messages == [
        {
            "conversation_id": conversation.id,
            "message": (
                "I couldn’t finish that request. "
                "Try it again without resending your message."
            ),
            "metadata": {"retry_action": True},
        }
    ]


async def test_progress_observer_delivers_preflight_telegram_error():
    service = _SurfaceService()
    observer = _observer(service)
    conversation = SimpleNamespace(
        id=uuid4(),
        metadata={"surface_platform": "TELEGRAM"},
    )

    await observer.on_run_failed(conversation, RuntimeError("runtime missing"))

    assert service.messages == [
        {
            "conversation_id": conversation.id,
            "message": (
                "I couldn’t finish that request. "
                "Try it again without resending your message."
            ),
            "metadata": {"retry_action": True},
        }
    ]


async def test_progress_observer_strips_inline_thinking_tags_from_text():
    """Some models emit thinking inline in TextPart as think tags. The observer
    must strip them so they never get buffered or delivered to a surface."""
    service = _SurfaceService()
    observer = _observer(service)
    conversation = SimpleNamespace(
        id=uuid4(), metadata={"surface_platform": "TELEGRAM"}
    )

    # Build the message with literal thinking tags (constructed programmatically
    # so the tags survive in source without being stripped as markup).
    open_tag = chr(60) + "think" + chr(62)
    close_tag = chr(60) + "/think" + chr(62)
    raw_text = f"Let me check that. {open_tag}internal reasoning{close_tag} Here is your answer."

    # The model emitted thinking tags inside a TEXT part (not a ThinkingPart).
    await observer.on_event(
        _assistant(MessageDraft.of_text(raw_text)),
        conversation,
        SimpleNamespace(),
    )

    await observer.on_run_finished(conversation, SimpleNamespace())

    assert len(service.messages) == 1
    delivered = service.messages[0]["message"]
    assert "<think" not in delivered.lower()
    assert "internal reasoning" not in delivered
    assert "Here is your answer." in delivered
    assert "Let me check that." in delivered


async def test_progress_observer_speaks_when_the_answer_was_only_reasoning():
    """A model that writes `<think>` and stops must not end the turn in silence.

    Seen live on Slack: the model emitted an unclosed thinking block and no
    answer, stripping correctly reduced it to "", and the run completed having
    sent nothing — the placeholder was even deleted. The person cannot tell
    that from being ignored, so they ask again into the same silence.
    """
    service = _SurfaceService()
    observer = _observer(service)
    conversation = SimpleNamespace(id=uuid4(), metadata={"surface_platform": "SLACK"})

    # Unclosed on purpose: this is what the model actually sent.
    open_tag = chr(60) + "think" + chr(62)
    raw_text = f"{open_tag}\nUser asks what I can do. Keep it short."

    await observer.on_event(
        _assistant(MessageDraft.of_text(raw_text)),
        conversation,
        SimpleNamespace(),
    )
    await observer.on_run_finished(conversation, SimpleNamespace())

    assert len(service.messages) == 1, "the turn must not end in silence"
    delivered = service.messages[0]["message"]
    assert "think" not in delivered.lower()
    assert "User asks what I can do" not in delivered


async def test_progress_observer_stays_quiet_when_there_was_no_answer_at_all():
    """The counterpart: nothing written is not the same as an answer lost.

    A run that produced no assistant text has nothing to apologise for — tool
    output and reply tools deliver on their own paths — so the fallback above
    must not fire and add a spurious message.
    """
    service = _SurfaceService()
    observer = _observer(service)
    conversation = SimpleNamespace(id=uuid4(), metadata={"surface_platform": "SLACK"})

    await observer.on_run_finished(conversation, SimpleNamespace())

    assert service.messages == []


async def test_progress_observer_strips_thinking_tags_and_resets_on_tool():
    """Inline thinking tags in intermediate narration are stripped, and the
    pre-tool narration is still discarded when a tool runs."""
    service = _SurfaceService()
    observer = _observer(service)
    conversation = SimpleNamespace(id=uuid4(), metadata={"surface_platform": "SLACK"})

    open_tag = chr(60) + "think" + chr(62)
    close_tag = chr(60) + "/think" + chr(62)

    # Pre-tool narration with inline thinking tags.
    await observer.on_event(
        _assistant(
            MessageDraft.of_text(
                f"Let me look that up. {open_tag}reasoning{close_tag} Searching now."
            )
        ),
        conversation,
        SimpleNamespace(),
    )
    # Tool call resets the buffer.
    await observer.on_event(
        _assistant(
            MessageDraft.of_tool_call(
                tool_name="web_search", tool_call_id="t1", tool_args={}
            )
        ),
        conversation,
        SimpleNamespace(),
    )
    # Final answer (no thinking tags).
    await observer.on_event(
        _assistant(MessageDraft.of_text("The answer is 42.")),
        conversation,
        SimpleNamespace(),
    )

    await observer.on_run_finished(conversation, SimpleNamespace())

    # The point of this test is the stripping: the delivered text carries no
    # thinking tags. Nothing streamed here, so it arrives as a plain message.
    assert [m["message"] for m in service.messages] == ["The answer is 42."]


async def test_progress_observer_stops_when_indicator_cannot_be_sent(monkeypatch):
    service = _SurfaceService(send_result=False)
    observer = _observer(service)
    conversation = SimpleNamespace(
        id=uuid4(),
        metadata={"surface_platform": "TELEGRAM"},
    )
    monkeypatch.setitem(
        progress_observer._TYPING_REFRESH_INTERVAL_SECONDS,
        "TELEGRAM",
        0.01,
    )

    await observer.on_run_started(conversation, SimpleNamespace())
    await asyncio.sleep(0.04)
    await observer.on_run_finished(conversation, SimpleNamespace())

    assert service.calls == [
        {
            "conversation_id": conversation.id,
            "metadata": None,
        }
    ]


async def test_progress_observer_renders_waiting_tool_call_once():
    """A repeated WAITING event for the same ask_user tool call must not send
    the same native surface prompt several times."""
    service = _SurfaceService()
    observer = _observer(service)
    conversation = SimpleNamespace(
        id=uuid4(), metadata={"surface_platform": "TELEGRAM"}
    )
    waiting = AgentEvent(
        type=AgentEventType.WAITING,
        data={"kind": "ask_user", "tool_call_id": "ask-1"},
    )

    await observer.on_event(waiting, conversation, SimpleNamespace())
    await observer.on_event(waiting, conversation, SimpleNamespace())

    assert service.messages == [
        {
            "questions": {
                "conversation_id": conversation.id,
                "tool_call_id": "ask-1",
                # The lead-in travels with the question, not ahead of it.
                "narration": None,
            }
        }
    ]


class TestAgentHostPermissionPrompt:
    """An Agent Host pauses for permission without ending its run.

    Every other pause arrives as a terminal WAITING event; this one arrives as a
    STATUS event mid-run, because the host holds the request open inside a run
    that keeps going. The observer has to render it all the same, or the prompt
    reaches nobody on Slack/Teams/Telegram and the agent waits out its 30-minute
    timeout in silence.
    """

    @staticmethod
    def _permission_event() -> AgentEvent:
        return AgentEvent(
            type=AgentEventType.STATUS,
            data={
                "status": "permission_request",
                "kind": "request_approval",
                "tool_call_id": "agent-host-permission:call-9",
            },
        )

    async def test_the_approval_prompt_is_rendered(self):
        service = _SurfaceService()
        observer = _observer(service)
        conversation = SimpleNamespace(
            id=uuid4(), metadata={"surface_platform": "TELEGRAM"}
        )

        await observer.on_event(
            self._permission_event(), conversation, SimpleNamespace()
        )

        assert service.messages == [
            {
                "approval": {
                    "conversation_id": conversation.id,
                    "tool_call_id": "agent-host-permission:call-9",
                    "narration": None,
                }
            }
        ]

    async def test_the_final_answer_still_arrives_after_the_decision(self):
        """A normal pause marks the final answer delivered, since the run is
        over. Doing that here would swallow everything the agent says once it
        has permission — the whole rest of the turn."""
        service = _SurfaceService()
        observer = _observer(service)
        conversation = SimpleNamespace(
            id=uuid4(), metadata={"surface_platform": "TELEGRAM"}
        )

        await observer.on_event(
            self._permission_event(), conversation, SimpleNamespace()
        )
        await observer.on_event(
            AgentEvent(
                type=AgentEventType.MESSAGE,
                data=MessageDraft.of_text("Removed the build directory."),
            ),
            conversation,
            SimpleNamespace(),
        )
        await observer.on_run_finished(conversation, SimpleNamespace())

        assert service.messages[-1] == {
            "conversation_id": conversation.id,
            "message": "Removed the build directory.",
        }

    async def test_an_unrelated_status_event_renders_nothing(self):
        service = _SurfaceService()
        observer = _observer(service)
        conversation = SimpleNamespace(
            id=uuid4(), metadata={"surface_platform": "TELEGRAM"}
        )

        await observer.on_event(
            AgentEvent(type=AgentEventType.STATUS, data={"status": "RUN_STATE"}),
            conversation,
            SimpleNamespace(),
        )

        assert service.messages == []


async def _run_with_progress_then_answer(service, platform: str):
    """Drive one run: something that opens live progress, then a final answer.

    On Slack that is a text token (which opens the stream); everywhere else it
    is a tool step.
    """
    observer = _observer(service)
    conversation = SimpleNamespace(id=uuid4(), metadata={"surface_platform": platform})
    if platform == "SLACK":
        await observer.on_event(
            AgentEvent(
                type=AgentEventType.TOKEN, data={"kind": "text", "data": "x" * 300}
            ),
            conversation,
            SimpleNamespace(),
        )
    else:
        await observer.on_event(
            AgentEvent(
                type=AgentEventType.MESSAGE,
                data=MessageDraft.of_tool_call(
                    tool_name="web_search",
                    tool_call_id="t1",
                    tool_args={"request": {"comment": "Searching the web"}},
                ),
            ),
            conversation,
            SimpleNamespace(),
        )
    await observer.on_event(
        _assistant(MessageDraft.of_text("The answer is 42.")),
        conversation,
        SimpleNamespace(),
    )
    await observer.on_run_finished(conversation, SimpleNamespace())
    return conversation


async def test_slack_final_answer_closes_the_live_stream():
    """On Slack the answer closes the stream: one message, not three acts.

    The old shape posted a placeholder, deleted it, then posted the answer
    beside it. Closing the stream keeps the agent's steps and its answer
    together, so nothing is cleared and nothing is sent separately.
    """
    service = _SurfaceService()
    await _run_with_progress_then_answer(service, "SLACK")

    assert len(service.finished) == 1
    assert service.finished[0]["already_streamed"] is True
    assert service.cleared == []
    assert service.messages == []


async def test_slack_falls_back_to_a_plain_message_when_the_stream_will_not_close():
    """A refused stop must not swallow the answer."""
    service = _SurfaceService(finish_result=False)
    await _run_with_progress_then_answer(service, "SLACK")

    assert service.finished  # attempted
    assert service.cleared  # progress cleaned up the old way
    assert [m["message"] for m in service.messages] == ["The answer is 42."]


async def test_telegram_still_clears_progress_and_sends_the_answer():
    """Platforms without a streaming API keep the existing two-step delivery."""
    service = _SurfaceService()
    await _run_with_progress_then_answer(service, "TELEGRAM")

    assert service.finished == []
    assert service.cleared
    assert [m["message"] for m in service.messages] == ["The answer is 42."]


def _token(kind: str, data: str) -> AgentEvent:
    return AgentEvent(type=AgentEventType.TOKEN, data={"kind": kind, "data": data})


async def test_text_tokens_stream_to_slack_as_they_arrive():
    """The answer appears as it is written, not all at once when the run ends."""
    service = _SurfaceService()
    observer = _observer(service)
    conversation = SimpleNamespace(id=uuid4(), metadata={"surface_platform": "SLACK"})

    # One delta over the flush threshold goes out immediately.
    await observer.on_event(_token("text", "x" * 300), conversation, SimpleNamespace())

    assert [c["text"] for c in service.streamed] == ["x" * 300]


async def test_thinking_tokens_never_reach_the_surface():
    """Reasoning is not the answer — the same rule every other path enforces."""
    service = _SurfaceService()
    observer = _observer(service)
    conversation = SimpleNamespace(id=uuid4(), metadata={"surface_platform": "SLACK"})

    await observer.on_event(
        _token("thinking", "y" * 500), conversation, SimpleNamespace()
    )

    assert service.streamed == []


async def test_first_delta_is_immediate_then_small_ones_batch():
    """Fast first paint, then batching — one call per token would burn the rate limit.

    The first delta flushes right away so text starts appearing the moment the
    model does; everything after it waits for the size or time threshold.
    """
    service = _SurfaceService()
    observer = _observer(service)
    conversation = SimpleNamespace(id=uuid4(), metadata={"surface_platform": "SLACK"})

    for _ in range(5):
        await observer.on_event(_token("text", "hi "), conversation, SimpleNamespace())

    assert [c["text"] for c in service.streamed] == ["hi "]


async def test_a_streamed_answer_is_not_delivered_twice():
    """What already streamed is on screen; only the remainder may be sent."""
    service = _SurfaceService()
    observer = _observer(service)
    conversation = SimpleNamespace(id=uuid4(), metadata={"surface_platform": "SLACK"})

    await observer.on_event(
        _token("text", "The answer "), conversation, SimpleNamespace()
    )
    await observer.on_event(
        _assistant(MessageDraft.of_text("The answer is 42.")),
        conversation,
        SimpleNamespace(),
    )
    await observer.on_run_finished(conversation, SimpleNamespace())

    # The streamed prefix is not repeated — only what was left.
    assert service.finished[0]["message"] == "is 42."
    assert service.finished[0]["already_streamed"] is True
    assert service.messages == []


async def test_telegram_ignores_token_events():
    """Only platforms that can show a live stream consume tokens."""
    service = _SurfaceService()
    observer = _observer(service)
    conversation = SimpleNamespace(
        id=uuid4(), metadata={"surface_platform": "TELEGRAM"}
    )

    await observer.on_event(_token("text", "z" * 500), conversation, SimpleNamespace())

    assert service.streamed == []


async def test_reasoning_split_across_deltas_never_reaches_slack():
    """Regression for reasoning leaking live into a Slack thread.

    Fireworks-class models emit ``<think>…</think>`` inline in the *text*
    stream. Per-delta stripping is not enough: the tag arrives in pieces, so
    ``<thi`` + ``nk>`` slipped through and the user watched the model reason.
    """
    service = _SurfaceService()
    observer = _observer(service)
    conversation = SimpleNamespace(id=uuid4(), metadata={"surface_platform": "SLACK"})

    for delta in [
        "Here is the plan. <thi",
        "nk>secret plotting</thi",
        "nk>",
        "x" * 300,
    ]:
        await observer.on_event(_token("text", delta), conversation, SimpleNamespace())

    streamed = "".join(c["text"] for c in service.streamed)
    assert "think" not in streamed.lower()
    assert "secret plotting" not in streamed
    assert "Here is the plan." in streamed


async def test_slack_gets_no_step_timeline_while_text_streams():
    """A step chunk appended into a live text stream lands mid-sentence.

    That is what split a word in half in the real thread: "Step 2: Bu" …step
    box… "ild something". Slack's progress *is* the streamed text.
    """
    service = _SurfaceService()
    observer = _observer(service)
    conversation = SimpleNamespace(id=uuid4(), metadata={"surface_platform": "SLACK"})

    await observer.on_event(
        AgentEvent(
            type=AgentEventType.MESSAGE,
            data=MessageDraft.of_tool_call(
                tool_name="web_search",
                tool_call_id="t1",
                tool_args={"request": {"comment": "Searching the web"}},
            ),
        ),
        conversation,
        SimpleNamespace(),
    )

    assert service.progress == []


async def test_slack_opens_the_stream_at_run_start_so_channels_show_something():
    """A channel gets no setStatus — an open stream is its only live signal.

    setStatus works only inside an assistant DM thread, and waiting for the
    first token leaves a tool-heavy channel run looking dead for seconds.
    """
    service = _SurfaceService()
    observer = _observer(service)
    conversation = SimpleNamespace(id=uuid4(), metadata={"surface_platform": "SLACK"})

    await observer.on_run_started(conversation, SimpleNamespace())

    assert [c["text"] for c in service.streamed] == [""]


async def test_failed_token_append_stays_buffered_until_confirmed():
    service = _SurfaceService()
    observer = _observer(service)
    conversation = SimpleNamespace(id=uuid4(), metadata={"surface_platform": "SLACK"})
    attempts = 0

    async def append(**kwargs):
        nonlocal attempts
        attempts += 1
        service.streamed.append(kwargs)
        return StreamAppendResult(handle={"message_id": 1}, appended=attempts > 1)

    service.append_stream_text_for_conversation = append
    observer._token_buffer = "must survive"

    await observer._flush_tokens(conversation)
    assert observer._token_buffer == "must survive"
    assert observer._streamed_text == ""

    await observer._flush_tokens(conversation)
    assert observer._token_buffer == ""
    assert observer._streamed_text == "must survive"
    assert [call["text"] for call in service.streamed] == [
        "must survive",
        "must survive",
    ]


async def test_final_answer_sends_unsent_text_after_append_failure():
    service = _SurfaceService()
    observer = _observer(service)
    conversation = SimpleNamespace(id=uuid4(), metadata={"surface_platform": "SLACK"})

    async def reject_append(**kwargs):
        service.streamed.append(kwargs)
        return StreamAppendResult(handle={"message_id": 1}, appended=False)

    service.append_stream_text_for_conversation = reject_append
    observer._progress_handle = {"message_id": 1}
    observer._token_buffer = "complete answer"
    observer._final_answer_text = "complete answer"

    assert await observer._finish_stream_with_answer(conversation) is True
    assert service.finished[-1]["message"] == "complete answer"
    assert service.finished[-1]["already_streamed"] is False


async def test_non_streaming_platforms_do_not_open_a_stream_at_run_start():
    service = _SurfaceService()
    observer = _observer(service)
    conversation = SimpleNamespace(
        id=uuid4(), metadata={"surface_platform": "TELEGRAM"}
    )

    await observer.on_run_started(conversation, SimpleNamespace())

    assert service.streamed == []


def _plan_return(*lines: str) -> AgentEvent:
    """A ``write_todos`` tool return carrying the whole checklist."""
    return AgentEvent(
        type=AgentEventType.MESSAGE,
        data=MessageDraft.of_tool_return(
            tool_name="write_todos",
            tool_call_id="todo-1",
            tool_result={"success": True, "todos": list(lines)},
        ),
    )


def _conversation(platform: str) -> SimpleNamespace:
    return SimpleNamespace(id=uuid4(), metadata={"surface_platform": platform})


async def test_the_plan_is_drawn_as_a_checklist_not_as_using_write_todos():
    """The most informative tool call used to render as the least informative line.

    Every tool call collapses to one status string, so a five-step plan reached
    the person as ``Using write_todos``.
    """
    service = _SurfaceService()
    observer = _observer(service)
    conversation = _conversation("TEAMS")

    await observer.on_event(
        _plan_return("- [x] Pull the Q3 numbers", "- [ ] Draft the summary"),
        conversation,
        SimpleNamespace(),
    )

    body = service.progress[-1]["progress_text"]
    assert "write_todos" not in body
    assert "Working on it — 1 of 2 steps done." in body
    assert "✅ Pull the Q3 numbers" in body
    assert "⏳ Draft the summary" in body


async def test_telegram_gets_the_plan_as_one_line_because_its_chip_holds_one():
    """A checklist in a ``tg-thinking`` chip is a run-on sentence.

    The chip collapses newlines, so five lines arrived as one paragraph with the
    marks stranded between the words — and the tool name trailing it said the
    same thing as the step, worse.
    """
    service = _SurfaceService()
    observer = _observer(service)
    conversation = _conversation("TELEGRAM")

    await observer.on_event(
        _plan_return("- [x] Write the scene", "- [ ] Render the video"),
        conversation,
        SimpleNamespace(),
    )

    body = service.progress[-1]["progress_text"]
    assert body == "Working on it — 1 of 2 steps done · Render the video"
    assert "\n" not in body


async def test_a_plan_that_has_not_moved_does_not_spend_an_update():
    service = _SurfaceService()
    observer = _observer(service)
    conversation = _conversation("TELEGRAM")
    plan = _plan_return("- [ ] Only step")

    await observer.on_event(plan, conversation, SimpleNamespace())
    await observer.on_event(plan, conversation, SimpleNamespace())

    assert len(service.progress) == 1


async def test_whatsapp_posts_the_plan_it_previously_showed_nothing_for():
    """WhatsApp has no edit API, so a long run used to be pure silence."""
    service = _SurfaceService()
    observer = _observer(service)
    conversation = _conversation("WHATSAPP")

    await observer.on_event(
        _plan_return("- [ ] Reconcile the ledger", "- [ ] Write it up"),
        conversation,
        SimpleNamespace(),
    )

    assert len(service.progress) == 1
    assert "0 of 2 steps done" in service.progress[0]["progress_text"]


async def test_whatsapp_rations_updates_after_the_first_plan():
    """Every WhatsApp update is a message in someone's chat, so they are capped.

    The first plan goes straight through — it is what the person most wants at
    the start of a long run — and the next one waits out the interval.
    """
    service = _SurfaceService()
    observer = _observer(service)
    conversation = _conversation("WHATSAPP")

    await observer.on_event(
        _plan_return("- [ ] One", "- [ ] Two"), conversation, SimpleNamespace()
    )
    await observer.on_event(
        _plan_return("- [x] One", "- [ ] Two"), conversation, SimpleNamespace()
    )

    assert len(service.progress) == 1

    observer._last_post_at -= progress_display._POST_PROGRESS_MIN_INTERVAL_SECONDS + 1
    await observer.on_event(
        _plan_return("- [x] One", "- [x] Two"), conversation, SimpleNamespace()
    )

    assert len(service.progress) == 2
    assert "All 2 steps done" in service.progress[1]["progress_text"]


async def test_whatsapp_says_something_on_a_long_run_with_no_plan():
    service = _SurfaceService()
    observer = _observer(service)
    conversation = _conversation("WHATSAPP")
    activity = AgentEvent(
        type=AgentEventType.MESSAGE,
        data=MessageDraft.of_tool_call(
            tool_name="run_query",
            tool_call_id="tool-9",
            tool_args={"request": {"comment": "Scanning the ledger"}},
        ),
    )

    await observer.on_event(activity, conversation, SimpleNamespace())
    assert service.progress == []

    observer._run_started_at -= progress_display._POST_HEARTBEAT_DELAY_SECONDS + 1
    await observer.on_event(activity, conversation, SimpleNamespace())

    assert len(service.progress) == 1
    assert "Still working on this" in service.progress[0]["progress_text"]

    # One acknowledgement, not a drip feed.
    observer._run_started_at -= 600
    await observer.on_event(activity, conversation, SimpleNamespace())
    assert len(service.progress) == 1


async def test_whatsapp_keeps_its_typing_bubble_alive():
    """The bubble the inbound path lights up expires after ~25s.

    Nothing refreshed it, so on a long run it went dark early — dead in exactly
    the runs where it was the only sign of life.
    """
    service = _SurfaceService()
    observer = _observer(service)
    conversation = _conversation("WHATSAPP")

    await observer.on_run_started(conversation, SimpleNamespace())
    task = observer._typing_task
    if task is not None:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert service.calls == [{"conversation_id": conversation.id, "metadata": None}]


async def test_email_still_shows_nothing_before_the_reply():
    service = _SurfaceService()
    observer = _observer(service)
    conversation = _conversation("RESEND")

    await observer.on_event(
        _plan_return("- [ ] Draft it"), conversation, SimpleNamespace()
    )

    assert service.progress == []
    assert service.messages == []


async def test_slack_is_acknowledged_by_its_open_stream_and_nothing_else():
    """The open stream is Slack's indicator — the observer adds nothing to it."""
    service = _SurfaceService()
    observer = _observer(service)
    conversation = _conversation("SLACK")

    await observer.on_run_started(conversation, SimpleNamespace())

    assert service.streamed == [
        {"conversation_id": conversation.id, "progress_handle": None, "text": ""}
    ]
    assert service.calls == []


async def test_email_gets_no_indicator_from_the_observer():
    service = _SurfaceService()
    observer = _observer(service)

    await observer.on_run_started(_conversation("GMAIL"), SimpleNamespace())

    assert service.calls == []
    assert service.streamed == []


async def test_a_one_line_surface_folds_a_multi_line_tool_comment():
    """A tool's comment is free text, and Telegram's chip eats the newlines.

    Two lines run together into one word without them, so the fold happens here
    rather than in the surface that cannot show it.
    """
    service = _SurfaceService()
    observer = _observer(service)
    conversation = _conversation("TELEGRAM")
    event = AgentEvent(
        type=AgentEventType.MESSAGE,
        data=MessageDraft.of_tool_call(
            tool_name="execute_python",
            tool_call_id="tool-2",
            tool_args={"request": {"comment": "Rendering the scene\nat 1080p"}},
        ),
    )

    await observer.on_event(event, conversation, SimpleNamespace())

    assert service.progress[-1]["progress_text"] == "Rendering the scene at 1080p"
