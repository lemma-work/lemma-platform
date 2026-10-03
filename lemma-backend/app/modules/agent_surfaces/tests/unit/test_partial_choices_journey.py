"""A question set that lands half as buttons and half as words, then gets answered.

Two questions, one message each: the first arrives as buttons, the second's send
fails, and the remainder is asked in words. The delivery reported that as fully
native, so nothing recorded that a typed answer was expected -- and the person
who typed "Small, Blue" had a new request started in their name and the pending
question superseded.

Each test follows the journey with the real egress, delivery ladder, platform
service and resume path over doubled transports: deliver the questions, type the
answer, and see what the paused run is resumed with. A test of the platform's
return value alone cannot notice that the receipt, the recorded intent and the
reply reader have to agree.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import fakeredis
import pytest

from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.modules.agent.contracts import (
    conversations_for_surfaces as agent_conversations,
)
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceConversationLink,
    SurfacePlatform,
)
from app.modules.agent_surfaces.platforms.telegram import callback_token_store
from app.modules.agent_surfaces.services.pending_interaction_resume import (
    ResumeOutcome,
    maybe_resume_pending_interaction,
)
from app.modules.agent_surfaces.tests.unit.surface_doubles import (
    _delivering_adapter,
    _pending,
    _slack_surface,
    build_egress,
    conversation_operations,  # noqa: F401  (autouse fixture)
)
from app.modules.agent_surfaces.tests.unit.test_delivery_completeness import (
    _RecordingWhatsAppClient,
    _ScriptedTelegramClient,
    _TelegramService,
    _tg_event,
    _wa_event,
    _wa_service,
)

pytestmark = pytest.mark.asyncio

TOOL_CALL_ID = "call-two-questions"
QUESTIONS = {
    "questions": [
        {
            "header": "size",
            "question": "Which size?",
            "options": [{"label": "Small"}, {"label": "Large"}],
        },
        {
            "header": "color",
            "question": "Which colour?",
            "options": [{"label": "Red"}, {"label": "Blue"}],
        },
    ]
}


@pytest.fixture
def callback_tokens():
    cache = RedisJsonCache(
        redis_url=settings.redis_url, key_prefix="test:cb-journey", ttl_seconds=60
    )
    cache._redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    previous = callback_token_store._token_store
    callback_token_store._token_store = cache
    yield cache
    callback_token_store._token_store = previous


@pytest.fixture
def conversation_metadata():
    """Conversation metadata that remembers what was written, as the real one does."""
    stored: dict[str, object] = {}

    async def write(_uow, _conversation_id, key, value):
        stored[key] = value

    async def read(_uow, _conversation_id, key):
        return stored.get(key)

    agent_conversations.set_conversation_metadata_value.side_effect = write
    agent_conversations.conversation_metadata_value.side_effect = read
    return stored


def _adapter_over(service, platform: str):
    """The real delivery ladder, with the platform service under test behind it."""
    adapter = _delivering_adapter(platform)

    async def render_choices(*, credentials, event, question_plan, metadata=None):
        return await service._render_choices(event, question_plan, metadata)

    async def send_message(*, credentials, event, message, metadata=None):
        await service.send_message(event, message, metadata)

    adapter._render_choices = AsyncMock(side_effect=render_choices)
    adapter.send_message = AsyncMock(side_effect=send_message)
    return adapter


async def _waiting_conversation(adapter, platform: SurfacePlatform, event):
    """An egress whose conversation is paused on the two questions."""
    conversation_id = uuid4()
    surface = _slack_surface().model_copy(update={"surface_type": platform})
    link = AgentSurfaceConversationLink(
        surface_id=surface.id,
        conversation_id=conversation_id,
        platform=platform.value,
        external_channel_id=event.external_channel_id,
        external_thread_id=event.external_thread_id,
        external_user_id="user-1",
        last_event=event.model_dump(mode="json"),
    )
    egress = build_egress(adapter=adapter, surfaces=[surface], existing_link=link)
    egress.delivery.conversation_link_repository.get_by_conversation_id.return_value = (
        link
    )
    pending = _pending("ask_user", tool_call_id=TOOL_CALL_ID, tool_args=QUESTIONS)
    agent_conversations.pending_question.return_value = pending
    agent_conversations.pending_interaction.return_value = pending
    return egress, conversation_id


def _context(conversation_id, platform: str):
    return SimpleNamespace(
        conversation_id=conversation_id,
        user_id=uuid4(),
        pod_id=uuid4(),
        platform=platform,
        agent_name=None,
    )


async def _type(egress, conversation_id, platform: str, text: str) -> ResumeOutcome:
    return await maybe_resume_pending_interaction(
        _context(conversation_id, platform), text, uow=egress.uow
    )


ANSWERS = {"size": "Small", "color": "Blue"}


@pytest.mark.parametrize("typed", ["Small, Blue", "Small, Blue,", "1, 2"])
async def test_whatsapp_questions_half_asked_in_words_accept_the_typed_answer(
    conversation_metadata, typed
):
    client = _RecordingWhatsAppClient(fail_interactive_after=1)
    service = _wa_service()
    service._client = client
    egress, conversation_id = await _waiting_conversation(
        _adapter_over(service, "WHATSAPP"), SurfacePlatform.WHATSAPP, _wa_event()
    )

    delivered = await egress.send_questions_for_conversation(
        conversation_id=conversation_id, tool_call_id=TOOL_CALL_ID
    )
    assert delivered
    assert len(client.interactives) == 1, "the first question is buttons"
    assert "Which colour?" in client.payloads[0]["text"]["body"], "the rest is words"

    outcome = await _type(egress, conversation_id, "WHATSAPP", typed)

    assert outcome is ResumeOutcome.CONSUMED, (
        "the typed answer must resolve the pause, not start a new request"
    )
    resolved = agent_conversations.resolve_pending_interaction.await_args.kwargs
    assert resolved["approval_id"] == TOOL_CALL_ID
    assert resolved["response"] == {"answers": ANSWERS}


async def test_telegram_questions_half_asked_in_words_accept_the_typed_answer(
    conversation_metadata, callback_tokens
):
    service = _TelegramService(_ScriptedTelegramClient(), fail_after=1)
    egress, conversation_id = await _waiting_conversation(
        _adapter_over(service, "TELEGRAM"), SurfacePlatform.TELEGRAM, _tg_event()
    )

    delivered = await egress.send_questions_for_conversation(
        conversation_id=conversation_id, tool_call_id=TOOL_CALL_ID
    )
    assert delivered
    assert service.sent[0] == "Which size?"
    assert "Which colour?" in service.sent[1]

    outcome = await _type(egress, conversation_id, "TELEGRAM", "Small, Blue")

    assert outcome is ResumeOutcome.CONSUMED
    resolved = agent_conversations.resolve_pending_interaction.await_args.kwargs
    assert resolved["response"] == {"answers": ANSWERS}


async def test_whatsapp_questions_shown_as_buttons_still_take_a_typed_answer(
    conversation_metadata,
):
    """On WhatsApp typing is how people answer, buttons or not.

    Buttons hold three short options and a list ten, so the answer a person
    has in mind is often not on them. With every question rendered natively, a
    typed reply used to be a new message that cancelled the question it was
    answering; `typed_reply_answers_native_choices` makes it the answer.
    """
    client = _RecordingWhatsAppClient()
    service = _wa_service()
    service._client = client
    egress, conversation_id = await _waiting_conversation(
        _adapter_over(service, "WHATSAPP"), SurfacePlatform.WHATSAPP, _wa_event()
    )

    await egress.send_questions_for_conversation(
        conversation_id=conversation_id, tool_call_id=TOOL_CALL_ID
    )
    outcome = await _type(egress, conversation_id, "WHATSAPP", "Small, Blue")

    assert len(client.interactives) == 2
    assert outcome is ResumeOutcome.CONSUMED
    agent_conversations.resolve_pending_interaction.assert_awaited_once()


async def test_an_instruction_typed_past_half_asked_questions_is_still_consumed_once(
    conversation_metadata,
):
    """The typed-answer intent is spent by the message that uses it."""
    client = _RecordingWhatsAppClient(fail_interactive_after=1)
    service = _wa_service()
    service._client = client
    egress, conversation_id = await _waiting_conversation(
        _adapter_over(service, "WHATSAPP"), SurfacePlatform.WHATSAPP, _wa_event()
    )
    await egress.send_questions_for_conversation(
        conversation_id=conversation_id, tool_call_id=TOOL_CALL_ID
    )

    assert await _type(egress, conversation_id, "WHATSAPP", "Small, Blue") is (
        ResumeOutcome.CONSUMED
    )
    assert await _type(egress, conversation_id, "WHATSAPP", "and another thing") is (
        ResumeOutcome.NOT_A_DECISION
    )
