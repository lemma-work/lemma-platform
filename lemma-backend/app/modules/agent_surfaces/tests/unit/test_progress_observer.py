from __future__ import annotations

from app.modules.agent_surfaces.domain.models import (
    ProgressHandle,
    ProgressHandleKind,
)

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
from app.modules.agent_surfaces.services.pending_envelope import RunFiles
from app.modules.agent_surfaces.services.progress_observer import (
    SurfaceAgentRunProgressObserver,
)
from app.modules.agent_surfaces.domain.models import StreamAppendResult

pytestmark = pytest.mark.asyncio


def _run_ctx(conversation, **fields):
    """A run's context, naming the platform its conversation's surface records."""
    platform = (getattr(conversation, "metadata", None) or {}).get("surface_platform")
    return SimpleNamespace(surface_platform=platform, **fields)


class _UowFactory:
    def __call__(self):
        return self

    async def __aenter__(self):
        return SimpleNamespace(agent_run_id=None)

    async def __aexit__(self, exc_type, exc, tb):
        return None


class _SurfaceProgress:
    """The live-message half, recording onto the egress fake that owns it.

    A nested object rather than five more methods on one namespace, because
    that is the shape the observer now drives: `egress.progress.send_progress_update`
    edits a message on screen, `egress.send_agent_message_for_conversation`
    delivers a new one, and the two speak different platform APIs.
    """

    def __init__(self, egress: "_SurfaceService") -> None:
        self.egress = egress

    async def show_typing(self, **kwargs):
        self.egress.calls.append(kwargs)
        return self.egress.send_result

    async def send_progress_update(self, **kwargs):
        self.egress.updates.append(kwargs)
        return ProgressHandle(
            kind=ProgressHandleKind.MESSAGE, message_id=str(len(self.egress.updates))
        )

    async def clear_progress(self, **kwargs):
        self.egress.cleared.append(kwargs)
        return

    async def append_streamed_text(self, **kwargs):
        self.egress.streamed.append(kwargs)
        return StreamAppendResult(
            handle=ProgressHandle(kind=ProgressHandleKind.MESSAGE, message_id=str(1)),
            appended=True,
        )

    async def finish_with_answer(self, **kwargs):
        self.egress.finished.append(kwargs)
        if self.egress.finish_error is not None:
            raise self.egress.finish_error
        return self.egress.finish_result


class _SurfaceService:
    def __init__(self, *, send_result: bool = True, finish_result: bool = True):
        self.calls = []
        self.messages = []
        self.updates = []
        self.cleared = []
        self.finished = []
        self.streamed = []
        self.send_result = send_result
        self.finish_result = finish_result
        self.finish_error: BaseException | None = None
        self.send_error: BaseException | None = None
        self.text_fallback_result = True
        self.text_fallbacks: list[dict] = []
        self.progress = _SurfaceProgress(self)

    async def send_agent_message_for_conversation(self, **kwargs):
        self.messages.append(kwargs)
        if self.send_error is not None:
            raise self.send_error
        return self.send_result

    async def send_prompt_as_text_for_conversation(self, **kwargs):
        self.text_fallbacks.append(kwargs)
        return self.text_fallback_result

    async def send_display_resource_for_conversation(self, **kwargs):
        self.messages.append({"display_resource": kwargs})
        return self.send_result

    async def send_questions_for_conversation(self, **kwargs):
        self.messages.append({"questions": kwargs})
        return self.send_result

    async def send_approval_prompt_for_conversation(self, **kwargs):
        self.messages.append({"approval": kwargs})
        return self.send_result

    async def send_sign_in_prompt_for_conversation(self, **kwargs):
        self.messages.append({"sign_in": kwargs})
        return self.send_result


def _observer(service: _SurfaceService) -> SurfaceAgentRunProgressObserver:
    return SurfaceAgentRunProgressObserver(
        contact_gate=_no_contact_refuses,
        uow_factory=_UowFactory(),
        egress_factory=lambda _uow: service,
    )


async def test_progress_observer_streams_tool_comment_progress():
    """The step timeline lives on platforms that cannot stream text.

    Slack is deliberately excluded: it streams the answer itself, and a step
    appended into that stream lands mid-sentence.
    """
    service = _SurfaceService()
    observer = _observer(service)
    conversation = SimpleNamespace(
        contact_id=None,
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

    await observer.on_event(
        event, conversation, _run_ctx(conversation, agent_run_id=None)
    )

    assert service.updates == [
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
        contact_id=None,
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

    await observer.on_event(
        event, conversation, _run_ctx(conversation, agent_run_id=None)
    )

    assert service.updates == [
        {
            "conversation_id": conversation.id,
            "progress_text": "Reading the file",
            "progress_handle": None,
        }
    ]
    for entry in service.updates:
        assert "think" not in entry["progress_text"].lower()


async def test_progress_observer_skips_all_reasoning_tool_comment():
    """A comment that is entirely reasoning streams no progress at all."""
    service = _SurfaceService()
    observer = _observer(service)
    conversation = SimpleNamespace(
        contact_id=None,
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

    await observer.on_event(
        event, conversation, _run_ctx(conversation, agent_run_id=None)
    )

    assert service.updates == []


def _assistant(draft: MessageDraft) -> AgentEvent:
    return AgentEvent(type=AgentEventType.MESSAGE, data=draft)


async def test_progress_observer_ignores_email_display_resource():
    # display_resource is a no-op for email surfaces — the observer no longer
    # accumulates or sends anything for it.
    service = _SurfaceService()
    observer = _observer(service)
    conversation = SimpleNamespace(
        contact_id=None,
        id=uuid4(),
        pod_id=uuid4(),
        metadata={"surface_platform": "GMAIL"},
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
        _run_ctx(conversation, agent_run_id=None),
    )
    await observer.on_run_finished(
        conversation, _run_ctx(conversation, agent_run_id=None)
    )

    # No reply tool, no buffered text → nothing sent, and no display metadata.
    assert service.messages == []


async def test_progress_observer_refreshes_telegram_typing_in_process(monkeypatch):
    service = _SurfaceService()
    observer = _observer(service)
    conversation = SimpleNamespace(
        contact_id=None,
        id=uuid4(),
        metadata={"surface_platform": "TELEGRAM"},
    )
    monkeypatch.setitem(
        progress_observer._TYPING_REFRESH_INTERVAL_SECONDS,
        "TELEGRAM",
        0.01,
    )

    await observer.on_run_started(
        conversation, _run_ctx(conversation, agent_run_id=None)
    )
    await asyncio.sleep(0.03)
    await observer.on_run_finished(
        conversation, _run_ctx(conversation, agent_run_id=None)
    )

    # The opening acknowledgement, then keep-alive ticks flagged as refreshes so
    # an adapter can tell them apart from it.
    assert service.calls[0] == {"conversation_id": conversation.id, "metadata": None}
    assert len(service.calls) > 1
    assert service.calls[-1] == {
        "conversation_id": conversation.id,
        "metadata": {"is_refresh": True},
    }


async def test_progress_observer_stays_quiet_when_there_was_no_answer_at_all():
    """The counterpart: nothing written is not the same as an answer lost.

    A run that produced no assistant text has nothing to apologise for — tool
    output and reply tools deliver on their own paths — so the fallback above
    must not fire and add a spurious message.
    """
    service = _SurfaceService()
    observer = _observer(service)
    conversation = SimpleNamespace(
        id=uuid4(), contact_id=None, metadata={"surface_platform": "SLACK"}
    )

    await observer.on_run_finished(
        conversation, _run_ctx(conversation, agent_run_id=None)
    )

    assert service.messages == []


async def test_progress_observer_stops_when_indicator_cannot_be_sent(monkeypatch):
    service = _SurfaceService(send_result=False)
    observer = _observer(service)
    conversation = SimpleNamespace(
        contact_id=None,
        id=uuid4(),
        metadata={"surface_platform": "TELEGRAM"},
    )
    monkeypatch.setitem(
        progress_observer._TYPING_REFRESH_INTERVAL_SECONDS,
        "TELEGRAM",
        0.01,
    )

    await observer.on_run_started(
        conversation, _run_ctx(conversation, agent_run_id=None)
    )
    await asyncio.sleep(0.04)
    await observer.on_run_finished(
        conversation, _run_ctx(conversation, agent_run_id=None)
    )

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
        contact_id=None, id=uuid4(), metadata={"surface_platform": "TELEGRAM"}
    )
    waiting = AgentEvent(
        type=AgentEventType.WAITING,
        data={"kind": "ask_user", "tool_call_id": "ask-1"},
    )

    await observer.on_event(
        waiting, conversation, _run_ctx(conversation, agent_run_id=None)
    )
    await observer.on_event(
        waiting, conversation, _run_ctx(conversation, agent_run_id=None)
    )

    assert service.messages == [
        {
            "questions": {
                "conversation_id": conversation.id,
                "tool_call_id": "ask-1",
                # The lead-in travels with the question, not ahead of it.
                "narration": None,
                "attach_files_of": RunFiles(None),
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
            contact_id=None, id=uuid4(), metadata={"surface_platform": "TELEGRAM"}
        )

        await observer.on_event(
            self._permission_event(),
            conversation,
            _run_ctx(conversation, agent_run_id=None),
        )

        assert service.messages == [
            {
                "approval": {
                    "conversation_id": conversation.id,
                    "tool_call_id": "agent-host-permission:call-9",
                    "narration": None,
                    "attach_files_of": RunFiles(None),
                }
            }
        ]

    async def test_an_unrelated_status_event_renders_nothing(self):
        service = _SurfaceService()
        observer = _observer(service)
        conversation = SimpleNamespace(
            contact_id=None, id=uuid4(), metadata={"surface_platform": "TELEGRAM"}
        )

        await observer.on_event(
            AgentEvent(type=AgentEventType.STATUS, data={"status": "RUN_STATE"}),
            conversation,
            _run_ctx(conversation, agent_run_id=None),
        )

        assert service.messages == []


async def _run_with_progress_then_answer(service, platform: str):
    """Drive one run: something that opens live progress, then a final answer.

    On Slack that is a text token (which opens the stream); everywhere else it
    is a tool step.
    """
    observer = _observer(service)
    conversation = SimpleNamespace(
        id=uuid4(), contact_id=None, metadata={"surface_platform": platform}
    )
    if platform == "SLACK":
        await observer.on_event(
            AgentEvent(
                type=AgentEventType.TOKEN, data={"kind": "text", "data": "x" * 300}
            ),
            conversation,
            _run_ctx(conversation, agent_run_id=None),
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
            _run_ctx(conversation, agent_run_id=None),
        )
    await observer.on_event(
        _assistant(MessageDraft.of_text("The answer is 42.")),
        conversation,
        _run_ctx(conversation, agent_run_id=None),
    )
    await observer.on_run_finished(
        conversation, _run_ctx(conversation, agent_run_id=None)
    )
    return conversation


def _token(kind: str, data: str) -> AgentEvent:
    return AgentEvent(type=AgentEventType.TOKEN, data={"kind": kind, "data": data})


async def test_text_tokens_stream_to_slack_as_they_arrive():
    """The answer appears as it is written, not all at once when the run ends."""
    service = _SurfaceService()
    observer = _observer(service)
    conversation = SimpleNamespace(
        id=uuid4(), contact_id=None, metadata={"surface_platform": "SLACK"}
    )

    # One delta over the flush threshold goes out immediately.
    await observer.on_event(
        _token("text", "x" * 300),
        conversation,
        _run_ctx(conversation, agent_run_id=None),
    )

    assert [c["text"] for c in service.streamed] == ["x" * 300]


async def test_thinking_tokens_never_reach_the_surface():
    """Reasoning is not the answer — the same rule every other path enforces."""
    service = _SurfaceService()
    observer = _observer(service)
    conversation = SimpleNamespace(
        id=uuid4(), contact_id=None, metadata={"surface_platform": "SLACK"}
    )

    await observer.on_event(
        _token("thinking", "y" * 500),
        conversation,
        _run_ctx(conversation, agent_run_id=None),
    )

    assert service.streamed == []


async def test_first_delta_is_immediate_then_small_ones_batch():
    """Fast first paint, then batching — one call per token would burn the rate limit.

    The first delta flushes right away so text starts appearing the moment the
    model does; everything after it waits for the size or time threshold.
    """
    service = _SurfaceService()
    observer = _observer(service)
    conversation = SimpleNamespace(
        id=uuid4(), contact_id=None, metadata={"surface_platform": "SLACK"}
    )

    for _ in range(5):
        await observer.on_event(
            _token("text", "hi "),
            conversation,
            _run_ctx(conversation, agent_run_id=None),
        )

    assert [c["text"] for c in service.streamed] == ["hi "]


async def test_telegram_ignores_token_events():
    """Only platforms that can show a live stream consume tokens."""
    service = _SurfaceService()
    observer = _observer(service)
    conversation = SimpleNamespace(
        contact_id=None, id=uuid4(), metadata={"surface_platform": "TELEGRAM"}
    )

    await observer.on_event(
        _token("text", "z" * 500),
        conversation,
        _run_ctx(conversation, agent_run_id=None),
    )

    assert service.streamed == []


async def test_reasoning_split_across_deltas_never_reaches_slack():
    """Regression for reasoning leaking live into a Slack thread.

    Fireworks-class models emit ``<think>…</think>`` inline in the *text*
    stream. Per-delta stripping is not enough: the tag arrives in pieces, so
    ``<thi`` + ``nk>`` slipped through and the user watched the model reason.
    """
    service = _SurfaceService()
    observer = _observer(service)
    conversation = SimpleNamespace(
        id=uuid4(), contact_id=None, metadata={"surface_platform": "SLACK"}
    )

    for delta in [
        "Here is the plan. <thi",
        "nk>secret plotting</thi",
        "nk>",
        "x" * 300,
    ]:
        await observer.on_event(
            _token("text", delta),
            conversation,
            _run_ctx(conversation, agent_run_id=None),
        )

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
    conversation = SimpleNamespace(
        id=uuid4(), contact_id=None, metadata={"surface_platform": "SLACK"}
    )

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
        _run_ctx(conversation, agent_run_id=None),
    )

    assert service.updates == []


async def test_slack_opens_the_stream_at_run_start_so_channels_show_something():
    """A channel gets no setStatus — an open stream is its only live signal.

    setStatus works only inside an assistant DM thread, and waiting for the
    first token leaves a tool-heavy channel run looking dead for seconds.
    """
    service = _SurfaceService()
    observer = _observer(service)
    conversation = SimpleNamespace(
        id=uuid4(), contact_id=None, metadata={"surface_platform": "SLACK"}
    )

    await observer.on_run_started(
        conversation, _run_ctx(conversation, agent_run_id=None)
    )

    assert [c["text"] for c in service.streamed] == [""]


async def test_failed_token_append_stays_buffered_until_confirmed():
    service = _SurfaceService()
    observer = _observer(service)
    conversation = SimpleNamespace(
        id=uuid4(), contact_id=None, metadata={"surface_platform": "SLACK"}
    )
    attempts = 0

    async def append(**kwargs):
        nonlocal attempts
        attempts += 1
        service.streamed.append(kwargs)
        return StreamAppendResult(
            handle=ProgressHandle(kind=ProgressHandleKind.MESSAGE, message_id=str(1)),
            appended=attempts > 1,
        )

    service.progress.append_streamed_text = append
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


async def test_non_streaming_platforms_do_not_open_a_stream_at_run_start():
    service = _SurfaceService()
    observer = _observer(service)
    conversation = SimpleNamespace(
        contact_id=None, id=uuid4(), metadata={"surface_platform": "TELEGRAM"}
    )

    await observer.on_run_started(
        conversation, _run_ctx(conversation, agent_run_id=None)
    )

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
    return SimpleNamespace(
        id=uuid4(), contact_id=None, metadata={"surface_platform": platform}
    )


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
        _run_ctx(conversation, agent_run_id=None),
    )

    body = service.updates[-1]["progress_text"]
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
        _run_ctx(conversation, agent_run_id=None),
    )

    body = service.updates[-1]["progress_text"]
    assert body == "Working on it — 1 of 2 steps done · Render the video"
    assert "\n" not in body


async def test_a_plan_that_has_not_moved_does_not_spend_an_update():
    service = _SurfaceService()
    observer = _observer(service)
    conversation = _conversation("TELEGRAM")
    plan = _plan_return("- [ ] Only step")

    await observer.on_event(
        plan, conversation, _run_ctx(conversation, agent_run_id=None)
    )
    await observer.on_event(
        plan, conversation, _run_ctx(conversation, agent_run_id=None)
    )

    assert len(service.updates) == 1


async def test_whatsapp_posts_the_plan_it_previously_showed_nothing_for():
    """WhatsApp has no edit API, so a long run used to be pure silence."""
    service = _SurfaceService()
    observer = _observer(service)
    conversation = _conversation("WHATSAPP")

    await observer.on_event(
        _plan_return("- [ ] Reconcile the ledger", "- [ ] Write it up"),
        conversation,
        _run_ctx(conversation, agent_run_id=None),
    )

    assert len(service.updates) == 1
    assert "0 of 2 steps done" in service.updates[0]["progress_text"]


async def test_whatsapp_rations_updates_after_the_first_plan():
    """Every WhatsApp update is a message in someone's chat, so they are capped.

    The first plan goes straight through — it is what the person most wants at
    the start of a long run — and the next one waits out the interval.
    """
    service = _SurfaceService()
    observer = _observer(service)
    conversation = _conversation("WHATSAPP")

    await observer.on_event(
        _plan_return("- [ ] One", "- [ ] Two"),
        conversation,
        _run_ctx(conversation, agent_run_id=None),
    )
    await observer.on_event(
        _plan_return("- [x] One", "- [ ] Two"),
        conversation,
        _run_ctx(conversation, agent_run_id=None),
    )

    assert len(service.updates) == 1

    observer._last_post_at -= progress_display._POST_PROGRESS_MIN_INTERVAL_SECONDS + 1
    await observer.on_event(
        _plan_return("- [x] One", "- [x] Two"),
        conversation,
        _run_ctx(conversation, agent_run_id=None),
    )

    assert len(service.updates) == 2
    assert "All 2 steps done" in service.updates[1]["progress_text"]


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

    await observer.on_event(
        activity, conversation, _run_ctx(conversation, agent_run_id=None)
    )
    assert service.updates == []

    observer._run_started_at -= progress_display._POST_HEARTBEAT_DELAY_SECONDS + 1
    await observer.on_event(
        activity, conversation, _run_ctx(conversation, agent_run_id=None)
    )

    assert len(service.updates) == 1
    assert "Still working on this" in service.updates[0]["progress_text"]

    # One acknowledgement, not a drip feed.
    observer._run_started_at -= 600
    await observer.on_event(
        activity, conversation, _run_ctx(conversation, agent_run_id=None)
    )
    assert len(service.updates) == 1


async def test_whatsapp_keeps_its_typing_bubble_alive():
    """The bubble the inbound path lights up expires after ~25s.

    Nothing refreshed it, so on a long run it went dark early — dead in exactly
    the runs where it was the only sign of life.
    """
    service = _SurfaceService()
    observer = _observer(service)
    conversation = _conversation("WHATSAPP")

    await observer.on_run_started(
        conversation, _run_ctx(conversation, agent_run_id=None)
    )
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
        _plan_return("- [ ] Draft it"),
        conversation,
        _run_ctx(conversation, agent_run_id=None),
    )

    assert service.updates == []
    assert service.messages == []


async def test_slack_is_acknowledged_by_its_open_stream_and_nothing_else():
    """The open stream is Slack's indicator — the observer adds nothing to it."""
    service = _SurfaceService()
    observer = _observer(service)
    conversation = _conversation("SLACK")

    await observer.on_run_started(
        conversation, _run_ctx(conversation, agent_run_id=None)
    )

    assert service.streamed == [
        {"conversation_id": conversation.id, "progress_handle": None, "text": ""}
    ]
    assert service.calls == []


async def test_email_gets_no_indicator_from_the_observer():
    service = _SurfaceService()
    observer = _observer(service)

    await observer.on_run_started(
        _conversation("GMAIL"), _run_ctx(_conversation("GMAIL"), agent_run_id=None)
    )

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

    await observer.on_event(
        event, conversation, _run_ctx(conversation, agent_run_id=None)
    )

    assert service.updates[-1]["progress_text"] == "Rendering the scene at 1080p"


async def test_a_paused_sign_in_reaches_the_surface_as_a_link():
    """A run stuck at a login wall has to reach the person wherever they are.

    It did not. `_handle_waiting_event` recognised `ask_user` and
    `request_approval` and returned early on anything else, so a paused
    `browser_sign_in` was delivered nowhere -- the run waited for ever and the
    only way to find out was to already be looking at the conversation.

    Sent as a link rather than through the approval path on purpose: Approve and
    Deny are not answers to "please sign in to this site".
    """
    service = _SurfaceService()
    observer = _observer(service)

    await observer._handle_waiting_event(
        AgentEvent(
            type=AgentEventType.WAITING,
            data={
                "kind": "browser_sign_in",
                "tool_call_id": "call_signin_1",
                "conversation_id": str(uuid4()),
            },
        ),
        _conversation("SLACK"),
    )

    assert [key for message in service.messages for key in message] == ["sign_in"]
    assert service.messages[0]["sign_in"]["tool_call_id"] == "call_signin_1"


async def test_a_pause_this_surface_does_not_render_is_left_alone():
    """Something new that pauses a run must not be delivered as a sign-in."""
    service = _SurfaceService()
    observer = _observer(service)

    await observer._handle_waiting_event(
        AgentEvent(
            type=AgentEventType.WAITING,
            data={"kind": "something_else", "tool_call_id": "x"},
        ),
        _conversation("SLACK"),
    )

    assert service.messages == []


# --- an answer that cannot be sent is not an answer that was sent ------------


def _events_named(caplog, name: str) -> list:
    return [record for record in caplog.records if name in str(record.getMessage())]


# --- a prompt that did not arrive is asked again, in words --------------------


def _waiting(kind: str = "ask_user", tool_call_id: str = "ask-1") -> AgentEvent:
    return AgentEvent(
        type=AgentEventType.WAITING,
        data={"kind": kind, "tool_call_id": tool_call_id},
    )


async def test_a_prompt_that_did_not_arrive_is_asked_again_in_plain_words(caplog):
    service = _SurfaceService(send_result=False)
    observer = _observer(service)
    conversation = SimpleNamespace(
        contact_id=None, id=uuid4(), metadata={"surface_platform": "WHATSAPP"}
    )

    await observer.on_event(
        _waiting(), conversation, _run_ctx(conversation, agent_run_id=None)
    )

    assert service.text_fallbacks == [
        {
            "conversation_id": conversation.id,
            "kind": "ask_user",
            "tool_call_id": "ask-1",
        }
    ]
    assert _events_named(caplog, "waiting_prompt_not_delivered")[0].levelname == (
        "WARNING"
    )
    # Asked in words, so a repeat of the same WAITING event is not asked again.
    await observer.on_event(
        _waiting(), conversation, _run_ctx(conversation, agent_run_id=None)
    )
    assert len(service.text_fallbacks) == 1


async def test_a_prompt_that_cannot_be_asked_even_in_words_can_be_tried_again():
    service = _SurfaceService(send_result=False)
    service.text_fallback_result = False
    observer = _observer(service)
    conversation = SimpleNamespace(
        contact_id=None, id=uuid4(), metadata={"surface_platform": "WHATSAPP"}
    )

    await observer.on_event(
        _waiting(), conversation, _run_ctx(conversation, agent_run_id=None)
    )
    await observer.on_event(
        _waiting(), conversation, _run_ctx(conversation, agent_run_id=None)
    )

    assert len(service.text_fallbacks) == 2, "the key was given back"


async def test_a_prompt_that_raised_is_logged_and_asked_in_words(caplog):
    service = _SurfaceService()

    async def broken(**kwargs):
        raise TimeoutError("whatsapp is down")

    service.send_approval_prompt_for_conversation = broken
    observer = _observer(service)
    conversation = SimpleNamespace(
        contact_id=None, id=uuid4(), metadata={"surface_platform": "WHATSAPP"}
    )

    await observer.on_event(
        _waiting("request_approval", "call-1"),
        conversation,
        _run_ctx(conversation, agent_run_id=None),
    )

    failed = _events_named(caplog, "waiting_prompt_failed")
    assert failed and failed[0].levelname == "WARNING"
    assert service.text_fallbacks[0]["kind"] == "request_approval"


async def test_a_web_conversation_that_pauses_is_not_asked_about_anywhere():
    """The observer is attached to every run, and most are not on a surface."""
    service = _SurfaceService(send_result=False)
    observer = _observer(service)
    conversation = SimpleNamespace(id=uuid4(), contact_id=None, metadata={})

    await observer.on_event(
        _waiting(), conversation, _run_ctx(conversation, agent_run_id=None)
    )

    assert service.text_fallbacks == []


async def test_the_observer_never_sends_the_answer_it_only_takes_display_down():
    """The answer is the run's delivery row (``surface.reply_for_run``).

    A run that streamed onto Slack and wrote its answer: the observer closes
    and removes the live stream, and sends nothing -- so a worker that dies
    here costs a progress bubble, never the answer.
    """
    service = _SurfaceService()
    observer = _observer(service)
    conversation = _conversation("SLACK")
    ctx = _run_ctx(conversation, agent_run_id=uuid4())
    observer._progress_handle = ProgressHandle(
        kind=ProgressHandleKind.MESSAGE, message_id="1.2"
    )

    await observer.on_run_finished(conversation, ctx)

    assert service.messages == []
    assert service.finished == []
    assert len(service.cleared) == 1


async def _no_contact_refuses(_conversation_id) -> bool:
    """No contact here refuses the answer (``contact_stream_gate``)."""
    return False
