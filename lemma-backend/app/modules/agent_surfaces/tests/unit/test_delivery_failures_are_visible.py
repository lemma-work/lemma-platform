"""A message that reached nobody must say so, at a level the deployment keeps.

Every path here failed silently in production and none of it was a mystery in
the code -- it was `logger.debug`, and the deployment runs `LOG_LEVEL=INFO`,
which drops `debug` before formatting. So a WhatsApp send that Meta rejected,
and a run whose answer was never delivered, both looked exactly like success.

`caplog` is set to DEBUG here on purpose: these assert the *level* each call was
made at, which is the whole of what was wrong.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

pytestmark = pytest.mark.asyncio


class _Links:
    def __init__(self, link=None):
        self._link = link

    async def get_by_conversation_id(self, conversation_id):
        return self._link


def _link(surface_id, *, last_event=None):
    from types import SimpleNamespace

    return SimpleNamespace(
        surface_id=surface_id,
        platform="WHATSAPP",
        last_event=last_event,
    )


def _delivery(links, surfaces):
    from types import SimpleNamespace

    from app.modules.agent_surfaces.services.egress_delivery import SurfaceDelivery

    return SurfaceDelivery(
        uow=SimpleNamespace(session=object()),
        surface_repository=surfaces,
        conversation_link_repository=links,
        adapter_registry=SimpleNamespace(get=lambda _platform: None),
        credential_resolver=AsyncMock(),
    )


async def test_a_conversation_with_no_surface_stays_quiet(caplog):
    """The one ordinary case: somebody typed in the web app.

    Every agent run asks for an egress target, so warning here would fire on
    conversations that were never on a platform at all.
    """
    with caplog.at_level("DEBUG"):
        target = await _delivery(_Links(None), AsyncMock()).resolve_egress_target(
            uuid4()
        )

    assert target is None
    # Asserted as "not a warning" rather than "exactly DEBUG", and the reason is
    # this PR's own subject: structlog filters by the configured level before
    # anything reaches stdlib, so `caplog` cannot see a `debug` record at all
    # and `at_level` cannot lift it. Claiming to check the level here would be
    # claiming something the harness cannot observe.
    #
    # What matters is testable and is the actual design decision: the ordinary
    # case must stay quiet. Its opposite is pinned by the test below, and the
    # pair is the distinction -- a link means somebody is waiting, no link means
    # somebody typed in the web app.
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING], [
        r.message for r in caplog.records
    ]


async def test_a_surface_that_cannot_answer_is_a_warning(caplog):
    """A link exists, so somebody on a platform is waiting for this answer."""
    caplog.set_level(logging.DEBUG)
    surfaces = AsyncMock()
    surfaces.get.return_value = None

    target = await _delivery(_Links(_link(uuid4())), surfaces).resolve_egress_target(
        uuid4()
    )

    assert target is None
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "a platform message that reached nobody must not be debug-only"
    assert "surface_cannot_answer" in warnings[0].message


async def test_the_fallback_reply_logs_what_the_platform_said(caplog):
    """The finding that started this: only the class name survived.

    `WhatsAppApiError` carries Meta's own body excerpt -- an invalid token, a
    number not registered, a recipient outside the tester allow-list -- and the
    handler recorded `type(exc).__name__` into an incident counter and nothing
    else. Three failures bought one anonymous line saying "WhatsAppApiError".

    Driven through `deliver_fallback_reply` rather than by constructing the
    error and reading it back. The first version of this test did the latter:
    it asserted that an exception renders its own message, which was never in
    doubt and would have passed with the whole logging change reverted.
    """
    from app.modules.agent_surfaces.domain.entities import (
        ConversationType,
        ParsedInboundSurfaceEvent,
        SurfacePlatform,
    )
    from app.modules.agent_surfaces.domain.ingress_context import SurfaceReplyContext
    from app.modules.agent_surfaces.platforms.whatsapp.client import WhatsAppApiError
    from app.modules.agent_surfaces.services.fallback_reply_service import (
        deliver_fallback_reply,
    )

    refused = WhatsAppApiError(
        method="POST",
        status_code=401,
        body_excerpt='{"error":{"message":"Invalid OAuth access token"}}',
    )
    adapter = AsyncMock()
    adapter.send_message = AsyncMock(side_effect=refused)
    dedup = AsyncMock()
    dedup.claim_stranger_reply = AsyncMock(return_value=True)

    with caplog.at_level("DEBUG"):
        await deliver_fallback_reply(
            adapter=adapter,
            context=SurfaceReplyContext(
                platform=SurfacePlatform.WHATSAPP,
                surface_id=uuid4(),
                reply_message="Sign up to talk to this agent.",
                event=ParsedInboundSurfaceEvent(
                    platform=SurfacePlatform.WHATSAPP,
                    conversation_type=ConversationType.EXTERNAL_DM,
                    external_thread_id="wa-thread",
                    sender_external_user_id="14155550000",
                    message_text="hello",
                    is_dm=True,
                ),
            ),
            credentials={"access_token": "wa-token", "phone_number_id": "1234567890"},
            event_dedup_store=dedup,
        )

    failures = [
        r for r in caplog.records if "surface_fallback_send_failed" in r.message
    ]
    assert failures, [r.message for r in caplog.records]
    record = failures[0]
    assert record.levelno >= logging.WARNING
    # Asserted on the rendered record rather than `record.exc_info`: structlog's
    # processors fold the exception into the event dict -- `error_message` and
    # `error_traceback`, which is exactly how it appears in the deployment's
    # logs -- and leave `exc_info` on the stdlib record empty.
    #
    # The whole point: Meta's own words reach the log, where before only the
    # string "WhatsAppApiError" did.
    assert "Invalid OAuth access token" in caplog.text
    assert "WhatsAppApiError" in caplog.text
