"""A message that failed in our own pipeline is answered once, from its number."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.platforms.whatsapp.adapter import (
    WhatsAppSurfaceAdapter,
)
from app.modules.agent_surfaces.services import processing_failure_notice
from app.modules.agent_surfaces.tests.e2e.platform_payloads import whatsapp

pytestmark = pytest.mark.asyncio


@asynccontextmanager
async def _uows():
    yield SimpleNamespace()


async def test_the_sender_is_told_once_from_the_number_it_arrived_on():
    adapter = WhatsAppSurfaceAdapter()
    adapter.send_message = AsyncMock()
    adapters = SimpleNamespace(get=lambda _platform: adapter)
    resolver = SimpleNamespace(for_platform=AsyncMock(return_value={"token": "t"}))
    claims: set[tuple] = set()

    async def claim_message(**key) -> bool:
        frozen = tuple(sorted(key.items()))
        if frozen in claims:
            return False
        claims.add(frozen)
        return True

    store = SimpleNamespace(claim_message=claim_message)
    request = SurfacePlatformWebhookIngress(
        source="whatsapp", payload=whatsapp.text(message_id="wamid.in-9")
    )

    for _ in range(2):  # the same message, redelivered after its last attempt
        await processing_failure_notice.tell_sender_it_failed(
            request,
            source="whatsapp",
            uow_factory=_uows,
            event_dedup_store=store,
            adapters=adapters,
            resolvers=lambda uow: resolver,
        )

    adapter.send_message.assert_awaited_once()
    assert (
        adapter.send_message.await_args.kwargs["message"]
        == processing_failure_notice.FAILURE_NOTICE
    )
    assert (
        resolver.for_platform.await_args.kwargs["arrived_on"]
        == whatsapp.PHONE_NUMBER_ID
    )


async def test_a_body_that_is_not_a_message_tells_nobody():
    store = SimpleNamespace(claim_message=AsyncMock())

    told = await processing_failure_notice.tell_sender_it_failed(
        SurfacePlatformWebhookIngress(
            source="whatsapp", payload=whatsapp.status_failed()
        ),
        source="whatsapp",
        uow_factory=_uows,
        event_dedup_store=store,
    )

    assert told is False
    store.claim_message.assert_not_awaited()
