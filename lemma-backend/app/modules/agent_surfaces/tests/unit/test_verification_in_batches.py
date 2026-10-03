"""A ``LEMMA VERIFY`` code anywhere in a batched WhatsApp delivery.

The check read only the first message: a code behind an ordinary message was
answered as chat and never verified, and one in front swallowed the delivery.
"""

from __future__ import annotations

import pytest

from app.modules.agent_surfaces.api.controllers import webhook_ingest
from app.modules.agent_surfaces.tests.e2e.platform_payloads import whatsapp

pytestmark = pytest.mark.asyncio


def _verify(message_id: str) -> dict:
    return whatsapp._message(
        message_id, type="text", text={"body": "LEMMA VERIFY 23456789AB"}
    )


def _chat(message_id: str) -> dict:
    return whatsapp._message(message_id, type="text", text={"body": "hello"})


class _Publisher:
    """Records which messages were published as verification, by id."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    async def __call__(self, payload, _uows) -> bool:
        message = payload["entry"][0]["changes"][0]["value"]["messages"][0]
        if message["text"]["body"].startswith("LEMMA VERIFY"):
            self.seen.append(message["id"])
            return True
        return False


@pytest.fixture
def published() -> _Publisher:
    return _Publisher()


async def _without(body, published: _Publisher):
    return await webhook_ingest._without_whatsapp_verifications(
        body, None, publish=published
    )


def _ids(payload: dict) -> list[str]:
    return [
        message["id"]
        for entry in payload["entry"]
        for change in entry["changes"]
        for message in change["value"].get("messages") or []
    ]


async def test_a_code_behind_a_message_is_verified_and_the_message_kept(published):
    body = whatsapp.envelope(_chat("m-1"), _verify("m-2"))

    remaining = await _without(body, published)

    assert published.seen == ["m-2"]
    assert remaining is not None and _ids(remaining) == ["m-1"]


async def test_a_code_alone_leaves_nothing_to_publish(published):
    body = whatsapp.envelope(_verify("m-1"))

    assert await _without(body, published) is None
    assert published.seen == ["m-1"]


async def test_a_body_with_no_code_is_handed_back_untouched(published):
    body = whatsapp.envelope(_chat("m-1"), _chat("m-2"))

    assert await _without(body, published) is body
    assert published.seen == []


async def test_statuses_beside_a_code_are_kept(published):
    body = whatsapp.envelope(_verify("m-1"))
    body["entry"][0]["changes"][0]["value"]["statuses"] = [
        {"id": "wamid.out", "status": "failed"}
    ]

    remaining = await _without(body, published)

    assert remaining is not None and _ids(remaining) == []
