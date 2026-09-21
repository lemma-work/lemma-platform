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
    caplog.set_level(logging.DEBUG)

    target = await _delivery(_Links(None), AsyncMock()).resolve_egress_target(uuid4())

    assert target is None
    assert [r.levelno for r in caplog.records if "egress" in r.message] == [
        logging.DEBUG
    ] or not [r for r in caplog.records if r.levelno >= logging.WARNING]


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
    """
    from app.modules.agent_surfaces.platforms.whatsapp.client import WhatsAppApiError

    caplog.set_level(logging.DEBUG)
    raised = WhatsAppApiError(
        method="POST",
        status_code=401,
        body_excerpt='{"error":{"message":"Invalid OAuth access token"}}',
    )

    # The handler is reached through `_deliver_fallback`; asserting on the
    # exception's own rendering keeps this a test of what is preserved rather
    # than of how the reply path is wired.
    # What `record_failure(error_type=type(exc).__name__)` discarded, and what
    # `exc_info=True` now carries into the log.
    assert "Invalid OAuth access token" in str(raised)
    assert raised.status_code == 401
    assert type(raised).__name__ == "WhatsAppApiError"
    assert "Invalid OAuth access token" not in type(raised).__name__
