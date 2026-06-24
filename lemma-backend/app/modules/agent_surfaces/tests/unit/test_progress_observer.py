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
from app.modules.agent_surfaces.services import progress_observer
from app.modules.agent_surfaces.services.progress_observer import (
    SurfaceAgentRunProgressObserver,
)

pytestmark = pytest.mark.asyncio


class _UowFactory:
    def __call__(self):
        return self

    async def __aenter__(self):
        return SimpleNamespace()

    async def __aexit__(self, exc_type, exc, tb):
        return None


class _SurfaceService:
    def __init__(self, *, send_result: bool = True):
        self.calls = []
        self.messages = []
        self.progress = []
        self.cleared = []
        self.send_result = send_result

    async def send_processing_indicator_for_conversation(self, **kwargs):
        self.calls.append(kwargs)
        return self.send_result

    async def send_progress_update_for_conversation(self, **kwargs):
        self.progress.append(kwargs)
        return {"message_id": len(self.progress)}

    async def clear_progress_for_conversation(self, **kwargs):
        self.cleared.append(kwargs)
        return None

    async def send_agent_message_for_conversation(self, **kwargs):
        self.messages.append(kwargs)
        return self.send_result

    async def send_display_resource_for_conversation(self, **kwargs):
        self.messages.append({"display_resource": kwargs})
        return self.send_result


def _observer(service: _SurfaceService) -> SurfaceAgentRunProgressObserver:
    return SurfaceAgentRunProgressObserver(
        uow_factory=_UowFactory(),
        service_factory=lambda _uow: service,
    )


async def test_progress_observer_streams_slack_tool_comment_progress():
    service = _SurfaceService()
    observer = _observer(service)
    conversation = SimpleNamespace(
        id=uuid4(),
        metadata={"surface_platform": "SLACK"},
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

    # Slack now streams progress as an edited message (chat.update), not a status.
    assert service.progress == [
        {
            "conversation_id": conversation.id,
            "progress_text": "Checking the latest todo state",
            "progress_handle": None,
        }
    ]
    assert service.calls == []
    assert service.messages == []


def _assistant(draft: MessageDraft) -> AgentEvent:
    return AgentEvent(type=AgentEventType.MESSAGE, data=draft)


async def test_progress_observer_buffers_text_and_sends_final_answer_on_finish():
    service = _SurfaceService()
    observer = _observer(service)
    conversation = SimpleNamespace(id=uuid4(), metadata={"surface_platform": "TELEGRAM"})

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

    # Exactly one content message — the final answer. The pre-tool narration was
    # discarded and thinking/tool content was never sent as content.
    assert service.messages == [
        {"conversation_id": conversation.id, "message": "The final answer is 42."}
    ]
    # Thinking/tool activity surfaced as streamed Slack progress instead.
    assert any(p.get("progress_text") for p in service.progress)


async def test_progress_observer_ignores_chat_display_resource_now_sent_by_tool():
    # Chat-surface display_resource delivery happens inside the display_resource
    # tool now. The observer must NOT also deliver it (that would double-send).
    service = _SurfaceService()
    observer = _observer(service)
    conversation = SimpleNamespace(
        id=uuid4(),
        metadata={"surface_platform": "SLACK"},
    )
    tool_call = AgentEvent(
        type=AgentEventType.MESSAGE,
        data=MessageDraft.of_tool_call(
            tool_name="display_resource",
            tool_call_id="tool-display-1",
            tool_args={"type": "TABLE", "name": "deals"},
        ),
    )
    tool_return = AgentEvent(
        type=AgentEventType.MESSAGE,
        data=MessageDraft.of_tool_return(
            tool_name="display_resource",
            tool_call_id="tool-display-1",
            tool_result={"success": True},
        ),
    )

    await observer.on_event(tool_call, conversation, SimpleNamespace())
    await observer.on_event(tool_return, conversation, SimpleNamespace())

    # No display delivery from the observer for a chat surface.
    assert all("display_resource" not in m for m in service.messages)


async def test_progress_observer_email_suppresses_final_text_when_reply_tool_called():
    # Email surfaces reply via gmail_reply_email / outlook_reply_email. When the
    # agent used the reply tool, the observer must NOT also send the buffered
    # text as a separate message.
    service = _SurfaceService()
    observer = _observer(service)
    conversation = SimpleNamespace(id=uuid4(), metadata={"surface_platform": "GMAIL"})

    await observer.on_event(
        AgentEvent(
            type=AgentEventType.MESSAGE,
            data=MessageDraft.of_tool_call(
                tool_name="gmail_reply_email",
                tool_call_id="reply-1",
                tool_args={"content": "Here is the report.", "attachment_paths": ["/me/report.pdf"]},
            ),
        ),
        conversation,
        SimpleNamespace(),
    )
    await observer.on_event(
        _assistant(MessageDraft.of_text("I emailed the report.")),
        conversation,
        SimpleNamespace(),
    )

    await observer.on_run_finished(conversation, SimpleNamespace())

    # Reply tool handled delivery — observer sends nothing.
    assert service.messages == []


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

    assert service.calls
    assert service.calls[-1] == {
        "conversation_id": conversation.id,
        "metadata": None,
    }


async def test_progress_observer_strips_inline_thinking_tags_from_text():
    """Some models emit thinking inline in TextPart as think tags. The observer
    must strip them so they never get buffered or delivered to a surface."""
    service = _SurfaceService()
    observer = _observer(service)
    conversation = SimpleNamespace(id=uuid4(), metadata={"surface_platform": "TELEGRAM"})

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

    assert service.messages == [
        {"conversation_id": conversation.id, "message": "The answer is 42."}
    ]


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
