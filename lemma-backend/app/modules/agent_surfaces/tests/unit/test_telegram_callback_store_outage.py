"""A Redis blip behind Telegram's button tokens, from the person's side.

Every button carries a short token whose payload lives in Redis. When Redis
could not be reached, the read used to come back as "expired" -- the tap was
answered with a dead button and its keyboard cleared, though tapping again a
moment later would have worked -- and the write was unguarded, so the run-error
notice carrying a "Try again" button was lost along with the button.

The outage here is a real one: the store is pointed at a port nothing listens
on, so the redis client raises what it raises in production.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.config import settings
from app.modules.agent_surfaces.platforms.telegram.adapter import (
    TelegramSurfaceAdapter,
)
from app.modules.agent_surfaces.platforms.telegram.callback_token_store import (
    CallbackTokenUnreadable,
    get_callback_token,
)
from app.modules.agent_surfaces.platforms.telegram.parser import TelegramMessageParser
from app.modules.agent_surfaces.platforms.telegram.service import (
    TelegramPlatformService,
)
from app.modules.agent_surfaces.services.display_resource_renderer import (
    build_approval_render_plan,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def redis_is_down(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", "redis://127.0.0.1:1/0")


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, method, payload, client=None):
        self.calls.append((method, payload))
        return {"ok": True, "result": {"message_id": len(self.calls)}}


def _service(recorder: _Recorder) -> TelegramPlatformService:
    service = TelegramPlatformService({"bot_token": "test-token"})
    service._client.call = recorder  # type: ignore[assignment]
    return service


def _event():
    parsed = TelegramMessageParser().parse(
        {
            "message": {
                "message_id": 5,
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 1, "first_name": "U"},
                "text": "hi",
            }
        }
    )
    assert parsed is not None
    return parsed


async def test_the_run_error_notice_still_arrives_without_its_button() -> None:
    recorder = _Recorder()

    await _service(recorder).send_message(
        _event(), "I couldn't finish that.", metadata={"retry_action": True}
    )

    assert len(recorder.calls) == 1, "the failure notice was lost with its button"
    _, payload = recorder.calls[0]
    assert "reply_markup" not in payload


async def test_an_approval_card_falls_back_to_words_rather_than_dead_buttons() -> None:
    recorder = _Recorder()
    plan = build_approval_render_plan(
        conversation_id=uuid4(),
        tool_call_id="call-1",
        title="Delete the draft",
        reason=None,
        tool_name=None,
    )

    rendered = await _service(recorder)._render_decision(_event(), plan)

    assert rendered is False
    assert recorder.calls == []


async def test_a_read_failure_is_not_an_expired_token() -> None:
    with pytest.raises(CallbackTokenUnreadable):
        await get_callback_token("0123456789abcdef")


async def test_a_tap_during_the_outage_parses_as_unreadable_not_expired() -> None:
    parsed = await TelegramSurfaceAdapter().parse_inbound_interaction(
        {
            "update_id": 1,
            "callback_query": {
                "id": "cbq-1",
                "data": "0123456789abcdef",
                "from": {"id": 1},
                "message": {"message_id": 7, "chat": {"id": 123}},
            },
        }
    )

    assert parsed is not None
    assert parsed.interaction_state == "unreadable"
