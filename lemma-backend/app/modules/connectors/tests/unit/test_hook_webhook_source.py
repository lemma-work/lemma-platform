"""The hook source: the address proves the delivery, and the delivery is kept."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.connectors.infrastructure.webhook_sources.hook import (
    HookWebhookSource,
)
from app.modules.datastore.contracts.hooks import HookDeliveryRefused, HookReceipt
from app.modules.schedule.contracts import (
    KeepingWebhookSource,
    WebhookDelivery,
    WebhookNotVerified,
    WebhookRefused,
    WebhookSourcePlugin,
)

HOOK_ID = uuid4()


def _delivery(**query: str) -> WebhookDelivery:
    return WebhookDelivery(
        source="hook",
        raw_body=b'{"a": 1}',
        headers={"Content-Type": "application/json"},
        query=query,
    )


def test_it_is_a_source_whose_deliveries_are_kept():
    source = HookWebhookSource()

    assert isinstance(source, WebhookSourcePlugin)
    assert isinstance(source, KeepingWebhookSource)


@pytest.mark.parametrize(
    "query", [{}, {"hook": str(HOOK_ID)}, {"key": "k"}, {"hook": "nope", "key": "k"}]
)
async def test_an_address_missing_its_hook_or_key_is_not_verified(query):
    async def never(**_kwargs):
        raise AssertionError("nothing is looked up for an incomplete address")

    with pytest.raises(WebhookNotVerified):
        await HookWebhookSource(admit=never).verify(_delivery(**query))


async def test_a_secret_the_hook_does_not_have_is_not_verified():
    async def unproven(**_kwargs):
        return None

    with pytest.raises(WebhookNotVerified):
        await HookWebhookSource(admit=unproven).verify(
            _delivery(hook=str(HOOK_ID), key="k")
        )


async def test_a_proven_delivery_carries_the_body_and_what_was_sent():
    seen: dict[str, object] = {}

    async def admitted(**kwargs):
        seen.update(kwargs)
        return {"a": 1}

    verified = await HookWebhookSource(admit=admitted).verify(
        _delivery(hook=str(HOOK_ID), key="k")
    )

    assert verified.payload == {"a": 1}
    assert seen == {
        "hook_id": HOOK_ID,
        "key": "k",
        "content_type": "application/json",
        "raw_body": b'{"a": 1}',
    }
    assert HookWebhookSource().normalize(verified) is None


async def test_a_refusal_reaches_the_sender_with_its_status():
    async def refused(**_kwargs):
        raise HookDeliveryRefused(429, "Too fast")

    with pytest.raises(WebhookRefused) as raised:
        await HookWebhookSource(admit=refused).verify(
            _delivery(hook=str(HOOK_ID), key="k")
        )

    assert (raised.value.status_code, raised.value.reason) == (429, "Too fast")


@pytest.mark.parametrize(
    ("receipt", "answer"),
    [
        (HookReceipt(), {"received": True}),
        (HookReceipt(duplicate=True), {"received": True, "duplicate": True}),
    ],
)
async def test_keeping_answers_received_and_nothing_about_the_row(receipt, answer):
    async def admitted(**_kwargs):
        return {"a": 1}

    async def kept(**kwargs):
        assert kwargs == {"hook_id": HOOK_ID, "body": {"a": 1}}
        return receipt

    source = HookWebhookSource(admit=admitted, keep=kept)

    verified = await source.verify(_delivery(hook=str(HOOK_ID), key="k"))

    assert await source.keep(verified) == answer
