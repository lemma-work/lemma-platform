"""What a run says on a surface, per verb.

Split from ``test_ingress_service`` with the object it tests. These call the
real ``SurfaceEgress`` over doubled collaborators -- the version of them that
came before built an eight-mixin ingress service and then reassigned
``service._resolve_credentials``, which is patching the subject to test it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent.contracts import (
    DisplayResourceRequest,
    DisplayResourceType,
)
from app.modules.agent.contracts import (
    conversations_for_surfaces as agent_conversations,
)
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceConversationLink,
    ConversationType,
    ParsedInboundSurfaceEvent,
)
from app.modules.agent_surfaces.domain.envelope import (
    DeliveryReceipt,
    EnvelopeFile,
    PartDelivery,
)
from app.modules.agent_surfaces.domain.models import (
    SurfaceDisplayRenderPlan,
    SurfaceQuestionRenderPlan,
)
from app.modules.agent_surfaces.services import egress_service
from app.modules.agent_surfaces.services.display_resource_content import (
    PodFileDelivery,
    PodFileParts,
)
from app.modules.agent_surfaces.tests.unit.surface_doubles import (
    _ASK_USER_TOOL_ARGS,
    _ASK_USER_TOOL_ARGS_FLAT,
    _REQUEST_APPROVAL_TOOL_ARGS,
    _ask_user_link,
    _delivering_adapter,
    _pending,
    _resend_surface,
    _slack_event,
    _slack_surface,
    _teams_surface,
    build_egress,
    conversation_operations,  # noqa: F401  (autouse fixture)
)

pytestmark = pytest.mark.asyncio


async def test_send_processing_indicator_for_conversation_uses_last_surface_event():
    surface = _teams_surface()
    conversation_id = uuid4()
    link = AgentSurfaceConversationLink(
        surface_id=surface.id,
        conversation_id=conversation_id,
        platform="TEAMS",
        external_channel_id="19:channel",
        external_thread_id="17001",
        external_user_id="8:orgid:user-1",
        last_event=ParsedInboundSurfaceEvent(
            platform="TEAMS",
            conversation_type=ConversationType.EXTERNAL_GROUP,
            tenant_id="tenant-123",
            external_channel_id="19:channel",
            external_thread_id="17001",
            external_message_id="17002",
            sender_external_user_id="8:orgid:user-1",
            sender_display_name="Asha",
            message_text="hello",
            mentioned_agent=True,
            reply_target={"conversation_id": "conversation-1"},
        ).model_dump(mode="json"),
    )
    adapter = AsyncMock()
    egress = build_egress(
        adapter=adapter,
        surfaces=[surface],
        existing_link=link,
    )
    egress.delivery.conversation_link_repository.get_by_conversation_id.return_value = (
        link
    )

    sent = await egress.progress.show_typing(
        conversation_id=conversation_id,
        metadata={"progress_text": "Checking the calendar"},
    )

    assert sent is True
    adapter.add_processing_indicator.assert_awaited_once()
    assert (
        adapter.add_processing_indicator.await_args.kwargs["metadata"]["progress_text"]
        == "Checking the calendar"
    )


async def test_send_agent_message_for_conversation_sends_surface_message():
    surface = _slack_surface()
    conversation_id = uuid4()
    parsed_event = _slack_event()
    link = AgentSurfaceConversationLink(
        surface_id=surface.id,
        conversation_id=conversation_id,
        platform="SLACK",
        external_channel_id=parsed_event.external_channel_id,
        external_thread_id=parsed_event.external_thread_id,
        external_user_id=parsed_event.sender_external_user_id,
        last_event=parsed_event.model_dump(mode="json"),
    )
    adapter = _delivering_adapter()
    egress = build_egress(
        adapter=adapter,
        surfaces=[surface],
        existing_link=link,
    )
    egress.delivery.conversation_link_repository.get_by_conversation_id.return_value = (
        link
    )

    sent = await egress.send_agent_message_for_conversation(
        conversation_id=conversation_id,
        message="assistant update",
    )

    assert sent is True
    adapter.send_message.assert_awaited_once()
    assert adapter.send_message.await_args.kwargs["message"] == "assistant update"


async def test_send_agent_message_strips_thinking_tokens_before_delivery():
    """Model reasoning/thinking tags must never reach a surface as a chat
    message. The ingress service strips them as a final safety net."""
    surface = _slack_surface()
    conversation_id = uuid4()
    parsed_event = _slack_event()
    link = AgentSurfaceConversationLink(
        surface_id=surface.id,
        conversation_id=conversation_id,
        platform="SLACK",
        external_channel_id=parsed_event.external_channel_id,
        external_thread_id=parsed_event.external_thread_id,
        external_user_id=parsed_event.sender_external_user_id,
        last_event=parsed_event.model_dump(mode="json"),
    )
    adapter = _delivering_adapter()
    egress = build_egress(
        adapter=adapter,
        surfaces=[surface],
        existing_link=link,
    )
    egress.delivery.conversation_link_repository.get_by_conversation_id.return_value = (
        link
    )

    # Build the message with literal thinking tags (constructed programmatically
    # so the tags survive in source without being stripped as markup).
    open_tag, close_tag = chr(60) + "think" + chr(62), chr(60) + "/think" + chr(62)
    raw_message = f"Let me check that. {open_tag}internal reasoning{close_tag} Here is your answer."

    sent = await egress.send_agent_message_for_conversation(
        conversation_id=conversation_id,
        message=raw_message,
    )

    assert sent is True
    delivered = adapter.send_message.await_args.kwargs["message"]
    assert "<think" not in delivered.lower()
    assert "internal reasoning" not in delivered
    assert "Here is your answer." in delivered
    assert "Let me check that." in delivered


async def test_send_agent_message_returns_false_when_only_thinking_tokens():
    """If the entire message is thinking content, nothing is sent."""
    surface = _slack_surface()
    conversation_id = uuid4()
    parsed_event = _slack_event()
    link = AgentSurfaceConversationLink(
        surface_id=surface.id,
        conversation_id=conversation_id,
        platform="SLACK",
        external_channel_id=parsed_event.external_channel_id,
        external_thread_id=parsed_event.external_thread_id,
        external_user_id=parsed_event.sender_external_user_id,
        last_event=parsed_event.model_dump(mode="json"),
    )
    adapter = AsyncMock()
    egress = build_egress(
        adapter=adapter,
        surfaces=[surface],
        existing_link=link,
    )
    egress.delivery.conversation_link_repository.get_by_conversation_id.return_value = (
        link
    )

    open_tag, close_tag = chr(60) + "think" + chr(62), chr(60) + "/think" + chr(62)
    raw_message = f"{open_tag}All reasoning, no answer{close_tag}"

    sent = await egress.send_agent_message_for_conversation(
        conversation_id=conversation_id,
        message=raw_message,
    )

    assert sent is False
    adapter.send_message.assert_not_awaited()


async def test_send_display_resource_for_conversation_sends_render_plan():
    surface = _slack_surface()
    conversation_id = uuid4()
    parsed_event = _slack_event()
    link = AgentSurfaceConversationLink(
        surface_id=surface.id,
        conversation_id=conversation_id,
        platform="SLACK",
        external_channel_id=parsed_event.external_channel_id,
        external_thread_id=parsed_event.external_thread_id,
        external_user_id=parsed_event.sender_external_user_id,
        last_event=parsed_event.model_dump(mode="json"),
    )
    adapter = _delivering_adapter()
    egress = build_egress(
        adapter=adapter,
        surfaces=[surface],
        existing_link=link,
    )
    egress.delivery.conversation_link_repository.get_by_conversation_id.return_value = (
        link
    )

    sent = await egress.send_display_resource_for_conversation(
        conversation_id=conversation_id,
        request=DisplayResourceRequest(type=DisplayResourceType.TABLE, name="deals"),
        tool_call_id="tool-display-1",
        tool_output={"success": True},
    )

    assert sent is True
    adapter._render_resource.assert_awaited_once()
    render_plan = adapter._render_resource.await_args.kwargs["render_plan"]
    assert isinstance(render_plan, SurfaceDisplayRenderPlan)
    assert render_plan.title == "Table: deals"
    assert render_plan.primary_action is not None
    assert "/pod/" in render_plan.primary_action.url
    assert "tab=deals" in render_plan.primary_action.url


async def test_a_delivered_file_carries_no_caption(monkeypatch):
    """A file goes out as the file, and nothing is written on it.

    The caption used to be the file's own name — which Telegram, WhatsApp and
    Slack all print on the bubble already, so the one line a media message can
    carry said only what the reader could see. Anything worth saying about the
    file is its own message.
    """
    surface = _slack_surface()
    conversation_id = uuid4()
    parsed_event = _slack_event()
    link = AgentSurfaceConversationLink(
        surface_id=surface.id,
        conversation_id=conversation_id,
        platform="SLACK",
        external_channel_id=parsed_event.external_channel_id,
        external_thread_id=parsed_event.external_thread_id,
        external_user_id=parsed_event.sender_external_user_id,
        last_event=parsed_event.model_dump(mode="json"),
    )
    adapter = _delivering_adapter()
    adapter._render_file.return_value = True
    egress = build_egress(adapter=adapter, surfaces=[surface], existing_link=link)
    egress.delivery.conversation_link_repository.get_by_conversation_id.return_value = (
        link
    )
    resolve = AsyncMock(
        return_value=PodFileParts(
            files=[
                EnvelopeFile(
                    file_name="shiplog.pdf",
                    content=b"%PDF",
                    mime_type="application/pdf",
                )
            ],
            facts=PodFileDelivery(delivered=True),
        )
    )
    monkeypatch.setattr(egress_service, "resolve_pod_file_parts", resolve)

    sent = await egress.send_display_resource_for_conversation(
        conversation_id=conversation_id,
        request=DisplayResourceRequest(
            type=DisplayResourceType.FILE, path="/me/reports/shiplog.pdf"
        ),
        tool_call_id="tool-file-caption",
    )

    assert sent is True
    assert resolve.await_args.kwargs["caption"] is None


async def test_send_questions_for_conversation_renders_native_then_falls_back():
    surface = _slack_surface()
    conversation_id = uuid4()
    parsed_event = _slack_event()
    link = await _ask_user_link(surface, conversation_id, parsed_event)
    adapter = _delivering_adapter()
    adapter._render_choices.return_value = True
    egress = build_egress(adapter=adapter, surfaces=[surface], existing_link=link)
    egress.delivery.conversation_link_repository.get_by_conversation_id.return_value = (
        link
    )
    agent_conversations.pending_question.return_value = _pending(
        "ask_user", tool_call_id="tool-1", tool_args=_ASK_USER_TOOL_ARGS
    )

    sent = await egress.send_questions_for_conversation(
        conversation_id=conversation_id, tool_call_id="tool-1"
    )
    assert sent is True
    plan = adapter._render_choices.await_args.kwargs["question_plan"]
    assert isinstance(plan, SurfaceQuestionRenderPlan)
    assert [q.header for q in plan.questions] == ["color"]
    assert plan.callback_id == f"{conversation_id}|tool-1"
    adapter.send_message.assert_not_awaited()

    # When native render returns False, it falls back to a formatted text message.
    adapter._render_choices.return_value = False
    sent = await egress.send_questions_for_conversation(
        conversation_id=conversation_id, tool_call_id="tool-1"
    )
    assert sent is True
    assert "Pick a color" in adapter.send_message.await_args.kwargs["message"]


async def test_send_questions_reads_flattened_pydantic_ai_args():
    """Regression: pydantic-ai flattens ask_user's single-model param, so the
    persisted args are {"questions": [...]} (NOT {"request": {...}}). The question
    must still be delivered — reading tool_args["request"] here swallowed it in
    production (no card, no text, run stuck WAITING)."""
    surface = _slack_surface()
    conversation_id = uuid4()
    parsed_event = _slack_event()
    link = await _ask_user_link(surface, conversation_id, parsed_event)
    adapter = _delivering_adapter()
    adapter._render_choices.return_value = True
    egress = build_egress(adapter=adapter, surfaces=[surface], existing_link=link)
    egress.delivery.conversation_link_repository.get_by_conversation_id.return_value = (
        link
    )
    agent_conversations.pending_question.return_value = _pending(
        # The real production shape: pydantic-ai flattens the single model
        # parameter, so the args are the model's own fields.
        "ask_user",
        tool_call_id="tool-1",
        tool_args=_ASK_USER_TOOL_ARGS_FLAT,
    )

    sent = await egress.send_questions_for_conversation(
        conversation_id=conversation_id, tool_call_id="tool-1"
    )
    assert sent is True
    plan = adapter._render_choices.await_args.kwargs["question_plan"]
    assert [q.header for q in plan.questions] == ["color"]

    # Native False → guaranteed text fallback still fires with the flat shape.
    adapter._render_choices.return_value = False
    await egress.send_questions_for_conversation(
        conversation_id=conversation_id, tool_call_id="tool-1"
    )
    assert "Pick a color" in adapter.send_message.await_args.kwargs["message"]


async def test_send_approval_prompt_renders_native_buttons():
    surface = _slack_surface()
    conversation_id = uuid4()
    parsed_event = _slack_event()
    link = await _ask_user_link(surface, conversation_id, parsed_event)
    adapter = _delivering_adapter()
    adapter._render_decision.return_value = True  # platform rendered native buttons
    egress = build_egress(adapter=adapter, surfaces=[surface], existing_link=link)
    egress.delivery.conversation_link_repository.get_by_conversation_id.return_value = (
        link
    )
    agent_conversations.pending_approval.return_value = _pending(
        "request_approval",
        tool_call_id="tool-2",
        tool_args=_REQUEST_APPROVAL_TOOL_ARGS,
    )

    sent = await egress.send_approval_prompt_for_conversation(
        conversation_id=conversation_id, tool_call_id="tool-2"
    )
    assert sent is True
    # Native render is attempted; the plan carries Approve + Deny and the callback.
    plan = adapter._render_decision.await_args.kwargs["approval_plan"]
    assert [b.decision for b in plan.buttons] == ["APPROVE_ONCE", "DENY"]
    assert plan.callback_id == f"{conversation_id}|tool-2"
    assert plan.title == "Write a record"
    # No permission_ids on this call → no approve-for-session button.
    assert all(b.decision != "APPROVE_FOR_SESSION" for b in plan.buttons)
    # When native buttons render, we do NOT also post the text prompt.
    adapter.send_message.assert_not_awaited()


async def test_an_older_unanswered_question_does_not_shadow_the_approval():
    """The bug this pairing exists to catch.

    A conversation can hold more than one unresolved pause. An `ask_user`
    nobody ever tapped stays unresolved forever, and being older it is what
    "what is this conversation waiting on" returns — so the approval renderer,
    which asked that question and then discarded anything that was not an
    approval, delivered nothing at all and left the run WAITING with nobody
    told. On a chat surface, where one conversation stands for the whole
    relationship with a person, that is permanent: dev's standing Telegram chat
    stopped rendering approval cards entirely.

    Wired through a stand-in that filters the way the real lookup does, so this
    fails if the renderer goes back to asking the unfiltered question.
    """
    surface = _slack_surface()
    conversation_id = uuid4()
    parsed_event = _slack_event()
    link = await _ask_user_link(surface, conversation_id, parsed_event)
    adapter = AsyncMock()
    adapter.deliver.return_value = DeliveryReceipt(
        parts={"decision": PartDelivery.NATIVE}
    )
    egress = build_egress(adapter=adapter, surfaces=[surface], existing_link=link)
    egress.delivery.conversation_link_repository.get_by_conversation_id.return_value = (
        link
    )

    stale_question = _pending("ask_user", tool_call_id="tool-ask")
    the_approval = _pending(
        "request_approval",
        tool_call_id="tool-2",
        tool_args=_REQUEST_APPROVAL_TOOL_ARGS,
    )
    # Oldest first, exactly as `oldest_unresolved_pause` walks them.
    pauses = [stale_question, the_approval]

    agent_conversations.pending_interaction.return_value = pauses[0]
    agent_conversations.pending_approval.return_value = next(
        (pause for pause in pauses if pause.is_approval), None
    )

    sent = await egress.send_approval_prompt_for_conversation(
        conversation_id=conversation_id, tool_call_id="tool-2"
    )

    assert sent is True
    # Through `deliver`, not `send_approval`: the per-content outbound verbs
    # became `_render_*` hooks only `deliver` calls, and this assertion was
    # left naming a method nothing invokes -- so it read `await_args` off a
    # never-awaited mock and died on None rather than checking the plan.
    plan = adapter.deliver.await_args.kwargs["envelope"].decision
    assert plan.title == "Write a record"
    assert [b.decision for b in plan.buttons] == ["APPROVE_ONCE", "DENY"]


async def test_send_approval_prompt_falls_back_to_text():
    surface = _slack_surface()
    conversation_id = uuid4()
    parsed_event = _slack_event()
    link = await _ask_user_link(surface, conversation_id, parsed_event)
    adapter = _delivering_adapter()
    adapter._render_decision.return_value = False  # platform has no native buttons
    egress = build_egress(adapter=adapter, surfaces=[surface], existing_link=link)
    egress.delivery.conversation_link_repository.get_by_conversation_id.return_value = (
        link
    )
    agent_conversations.pending_approval.return_value = _pending(
        "request_approval",
        tool_call_id="tool-2",
        tool_args=_REQUEST_APPROVAL_TOOL_ARGS,
    )

    sent = await egress.send_approval_prompt_for_conversation(
        conversation_id=conversation_id, tool_call_id="tool-2"
    )
    assert sent is True
    msg = adapter.send_message.await_args.kwargs["message"]
    assert "Write a record" in msg
    assert "approve" in msg.lower()
    assert "deny" in msg.lower()


async def test_send_approval_prompt_adds_session_button_with_permission_ids():
    surface = _slack_surface()
    conversation_id = uuid4()
    parsed_event = _slack_event()
    link = await _ask_user_link(surface, conversation_id, parsed_event)
    adapter = _delivering_adapter()
    adapter._render_decision.return_value = True
    egress = build_egress(adapter=adapter, surfaces=[surface], existing_link=link)
    egress.delivery.conversation_link_repository.get_by_conversation_id.return_value = (
        link
    )
    agent_conversations.pending_approval.return_value = _pending(
        "request_approval",
        tool_call_id="tool-2",
        tool_args={**_REQUEST_APPROVAL_TOOL_ARGS, "permission_ids": ["perm-1"]},
    )

    await egress.send_approval_prompt_for_conversation(
        conversation_id=conversation_id, tool_call_id="tool-2"
    )
    plan = adapter._render_decision.await_args.kwargs["approval_plan"]
    assert [b.decision for b in plan.buttons] == [
        "APPROVE_ONCE",
        "DENY",
        "APPROVE_FOR_SESSION",
    ]


async def test_send_approval_prompt_skips_when_no_pending():
    surface = _slack_surface()
    conversation_id = uuid4()
    parsed_event = _slack_event()
    link = await _ask_user_link(surface, conversation_id, parsed_event)
    adapter = AsyncMock()
    egress = build_egress(adapter=adapter, surfaces=[surface], existing_link=link)
    egress.delivery.conversation_link_repository.get_by_conversation_id.return_value = (
        link
    )
    agent_conversations.pending_approval.return_value = None

    sent = await egress.send_approval_prompt_for_conversation(
        conversation_id=conversation_id
    )
    assert sent is False
    adapter.send_message.assert_not_awaited()


async def test_an_email_surface_delivers_the_question_in_its_one_reply():
    """Email is asked, not suppressed. The prompt rides in the reply as text."""
    surface = _resend_surface()
    conversation_id = uuid4()
    parsed_event = _slack_event()
    link = AgentSurfaceConversationLink(
        surface_id=surface.id,
        conversation_id=conversation_id,
        platform="RESEND",
        external_channel_id=parsed_event.external_channel_id,
        external_thread_id=parsed_event.external_thread_id,
        external_user_id=parsed_event.sender_external_user_id,
        last_event=parsed_event.model_dump(mode="json"),
    )
    adapter = _delivering_adapter("RESEND")
    egress = build_egress(adapter=adapter, surfaces=[surface], existing_link=link)
    egress.delivery.conversation_link_repository.get_by_conversation_id.return_value = (
        link
    )
    agent_conversations.pending_question.return_value = _pending(
        "ask_user", tool_call_id="tool-1", tool_args=_ASK_USER_TOOL_ARGS
    )

    sent = await egress.send_questions_for_conversation(
        conversation_id=conversation_id, tool_call_id="tool-1"
    )

    assert sent is True
    assert "Pick a color" in adapter.send_message.await_args.kwargs["message"]


async def test_send_processing_indicator_for_conversation_stops_without_link():
    surface = _teams_surface()
    adapter = AsyncMock()
    egress = build_egress(adapter=adapter, surfaces=[surface])
    egress.delivery.conversation_link_repository.get_by_conversation_id.return_value = (
        None
    )

    sent = await egress.progress.show_typing(
        conversation_id=uuid4(),
    )

    assert sent is False
    adapter.add_processing_indicator.assert_not_awaited()
