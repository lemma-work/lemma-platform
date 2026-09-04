from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.connectors.contracts.triggers import TriggerBinding
from app.modules.schedule.domain.errors import ScheduleValidationError
from app.modules.schedule.domain.schedule import ScheduleEntity, ScheduleType
from app.modules.schedule.infrastructure.adapters.external_schedule_writer import (
    ExternalScheduleWriterAdapter,
)


def _schedule(**overrides) -> ScheduleEntity:
    fields = {
        "user_id": uuid4(),
        "schedule_type": ScheduleType.WEBHOOK,
        "connector_trigger_id": "gmail_new_email",
        "account_id": uuid4(),
        "config": {"labelIds": ["INBOX"]},
    }
    fields.update(overrides)
    return ScheduleEntity(**fields)


def _adapter(binding: TriggerBinding, subscribed: list[dict]):
    """An adapter whose three connector operations are stood up, not patched."""

    async def _resolve(_uow, **_kwargs) -> TriggerBinding:
        return binding

    async def _subscribe(**kwargs) -> str:
        subscribed.append(kwargs)
        return "ti_123"

    async def _unsubscribe(subscription_id: str) -> None:
        subscribed.append({"deleted": subscription_id})

    return ExternalScheduleWriterAdapter(
        None,
        resolve_binding=_resolve,
        subscribe=_subscribe,
        unsubscribe=_unsubscribe,
    )


@pytest.mark.asyncio
async def test_a_subscribable_trigger_is_subscribed_with_the_schedules_own_config():
    subscribed: list[dict] = []
    schedule = _schedule()
    adapter = _adapter(
        TriggerBinding(
            connector_id="gmail",
            event_type="GMAIL_NEW_EMAIL",
            connection_id="ca_123",
            subscribable=True,
        ),
        subscribed,
    )

    provisioned = await adapter.create_provider_trigger(schedule)

    assert provisioned.provider_trigger_id == "ti_123"
    (call,) = subscribed
    assert call == {
        "slug": "GMAIL_NEW_EMAIL",
        "connection_id": "ca_123",
        "config": schedule.config,
    }
    # The native /webhooks/schedules/{id} callback was removed; the provider no
    # longer receives a callback_url (composio delivers via its own verified
    # webhook).
    assert "callback_url" not in call


@pytest.mark.asyncio
async def test_an_account_with_no_provider_connection_is_refused():
    """An inactive account has nothing to subscribe, so the schedule is not created."""
    adapter = _adapter(
        TriggerBinding(
            connector_id="gmail",
            event_type="GMAIL_NEW_EMAIL",
            connection_id=None,
            subscribable=True,
        ),
        [],
    )

    with pytest.raises(ScheduleValidationError):
        await adapter.create_provider_trigger(_schedule())


@pytest.mark.asyncio
async def test_a_connector_with_neither_a_subscription_nor_a_binder_is_refused():
    """The case that used to return `None` and read as success.

    The row would exist, nothing would be subscribed, and the schedule could
    never fire.
    """
    adapter = _adapter(
        TriggerBinding(
            connector_id="slack",
            event_type="message_posted",
            subscribable=False,
        ),
        [],
    )

    with pytest.raises(ScheduleValidationError):
        await adapter.create_provider_trigger(_schedule())


@pytest.mark.asyncio
async def test_a_schedule_naming_no_connector_trigger_provisions_nothing():
    adapter = _adapter(
        TriggerBinding(connector_id="gmail", event_type="x", subscribable=True), []
    )

    provisioned = await adapter.create_provider_trigger(
        _schedule(connector_trigger_id=None, account_id=None)
    )

    assert provisioned.provider_trigger_id is None
    assert provisioned.bound_config == {}
