"""The stranger-reply window: held around a fallback send, given back when it fails.

Message-level claims are durable rows now (``surface_message_claims``), on the
session of the work they guard, so there is nothing to hand back by hand: a
failure rolls the claim back with everything else (see
``test_surface_inbound_e2e``). What stays in Redis is the window, a throttle.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.ingress_context import SurfaceReplyContext
from app.modules.agent_surfaces.services.fallback_reply_service import (
    deliver_fallback_reply,
)

pytestmark = pytest.mark.asyncio


def _unrouted_event() -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform=SurfacePlatform.WHATSAPP,
        conversation_type=ConversationType.EXTERNAL_DM,
        external_message_id="wamid.1",
        external_thread_id="4477",
        sender_external_user_id="4477",
        message_text="hi",
        is_dm=True,
    )


def _fallback_context() -> SurfaceReplyContext:
    return SurfaceReplyContext(
        platform=SurfacePlatform.WHATSAPP,
        surface_id=uuid4(),
        reply_kind="signup",
        reply_message="Sign up to talk to this agent.",
        event=_unrouted_event(),
    )


def _window_store():
    return SimpleNamespace(
        claim_stranger_reply=AsyncMock(return_value=True),
        release_stranger_reply=AsyncMock(),
    )


async def test_a_failed_send_gives_the_stranger_window_back(caplog):
    adapter = SimpleNamespace(deliver=AsyncMock(side_effect=RuntimeError("boom")))
    store = _window_store()
    context = _fallback_context()

    with caplog.at_level(logging.WARNING):
        await deliver_fallback_reply(
            adapter=adapter,
            context=context,
            credentials={"access_token": "token"},
            event_dedup_store=store,
        )

    store.release_stranger_reply.assert_awaited_once_with(
        platform=str(context.platform),
        surface_installation_id=context.surface_id,
        sender_external_user_id="4477",
    )


async def test_a_delivered_reply_keeps_the_window():
    adapter = SimpleNamespace(deliver=AsyncMock(return_value=None))
    store = _window_store()

    await deliver_fallback_reply(
        adapter=adapter,
        context=_fallback_context(),
        credentials={"access_token": "token"},
        event_dedup_store=store,
    )

    store.release_stranger_reply.assert_not_awaited()


async def test_the_redis_store_releases_the_key_it_claims(monkeypatch):
    """The two halves of the window have to name the same key."""
    from app.modules.agent_surfaces.infrastructure.adapters.stranger_reply_window import (
        RedisStrangerReplyWindow,
    )

    store = RedisStrangerReplyWindow(redis_url="redis://unused")
    redis = SimpleNamespace(set=AsyncMock(return_value=True), delete=AsyncMock())
    store._redis = redis
    surface_id = uuid4()

    await store.claim_stranger_reply(
        platform="WHATSAPP",
        surface_installation_id=surface_id,
        sender_external_user_id="4477",
    )
    await store.release_stranger_reply(
        platform="WHATSAPP",
        surface_installation_id=surface_id,
        sender_external_user_id="4477",
    )

    assert redis.delete.await_args.args[0] == redis.set.await_args.args[0]
