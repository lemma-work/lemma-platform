from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.connectors.contracts.pod_connectors import (
    ConnectorNeedsAccountError,
    PodConnectorAccount,
    PodConnectorMode,
)
from app.modules.connectors.contracts.triggers import TriggerBinding
from app.modules.connectors.domain.account import AccountEntity
from app.modules.schedule.domain.errors import ScheduleValidationError
from app.modules.schedule.domain.schedule import ScheduleEntity, ScheduleType
from app.modules.schedule.infrastructure.adapters.external_schedule_writer import (
    ExternalScheduleWriterAdapter,
)


def _schedule(**overrides) -> ScheduleEntity:
    fields = {
        "user_id": uuid4(),
        "schedule_type": ScheduleType.WEBHOOK,
        "pod_id": uuid4(),
        "connector_trigger_id": "gmail_new_email",
        "pod_connector_slug": "support-inbox",
        "config": {"labelIds": ["INBOX"]},
    }
    fields.update(overrides)
    return ScheduleEntity(**fields)


def _resolved(schedule: ScheduleEntity, *, owner=None) -> PodConnectorAccount:
    owner = owner or uuid4()
    return PodConnectorAccount(
        pod_id=schedule.pod_id,
        slug=schedule.pod_connector_slug,
        mode=PodConnectorMode.SHARED,
        pod_account_id=uuid4(),
        owner_user_id=owner,
        auth_config_id=uuid4(),
        connector_id="gmail",
        account=AccountEntity(
            user_id=owner,
            organization_id=uuid4(),
            auth_config_id=uuid4(),
            connector_id="gmail",
        ),
    )


def _adapter(
    binding: TriggerBinding,
    subscribed: list[dict],
    *,
    resolve_connector=None,
    bound: list[dict] | None = None,
):
    """An adapter whose connector operations are stood up, not patched."""

    async def _resolve(_uow, **kwargs) -> TriggerBinding:
        if bound is not None:
            bound.append(kwargs)
        return binding

    async def _resolve_connector(_uow, schedule):
        return _resolved(schedule)

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
        resolve_connector=resolve_connector or _resolve_connector,
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
        _schedule(connector_trigger_id=None, pod_connector_slug=None)
    )

    assert provisioned.provider_trigger_id is None
    assert provisioned.bound_config == {}


@pytest.mark.asyncio
async def test_the_trigger_is_bound_to_the_account_the_pod_connector_resolves_to():
    """Never an account the schedule names: the connector decides, and the
    account is bound as its owner's -- a shared account's owner is not the
    schedule's."""
    schedule = _schedule()
    owner = uuid4()
    resolved = _resolved(schedule, owner=owner)
    asked: list[ScheduleEntity] = []

    async def _resolve_connector(_uow, asked_for):
        asked.append(asked_for)
        return resolved

    bound: list[dict] = []
    adapter = _adapter(
        TriggerBinding(
            connector_id="gmail",
            event_type="GMAIL_NEW_EMAIL",
            connection_id="ca_1",
            subscribable=True,
        ),
        [],
        resolve_connector=_resolve_connector,
        bound=bound,
    )

    await adapter.create_provider_trigger(schedule)

    assert asked == [schedule]
    assert bound == [
        {
            "trigger_id": "gmail_new_email",
            "account_id": resolved.account_id,
            "user_id": owner,
        }
    ]


@pytest.mark.asyncio
async def test_a_connector_that_no_longer_resolves_refuses_with_its_own_error():
    """CONNECTOR_NEEDS_ACCOUNT names its remedy; a schedule error would not."""

    async def _withdrawn(_uow, schedule):
        raise ConnectorNeedsAccountError(
            slug=schedule.pod_connector_slug, connect_url="https://app.test/c"
        )

    adapter = _adapter(
        TriggerBinding(connector_id="gmail", event_type="x", subscribable=True),
        [],
        resolve_connector=_withdrawn,
    )

    with pytest.raises(ConnectorNeedsAccountError):
        await adapter.create_provider_trigger(_schedule())


@pytest.mark.asyncio
async def test_deleting_drops_the_subscription_without_resolving_the_connector():
    """The subscription must stop most of all when the connector no longer
    resolves -- its owner left, the account was withdrawn."""
    subscribed: list[dict] = []

    async def _withdrawn(_uow, schedule):
        raise ConnectorNeedsAccountError(
            slug=schedule.pod_connector_slug, connect_url="https://app.test/c"
        )

    adapter = _adapter(
        TriggerBinding(connector_id="gmail", event_type="x", subscribable=True),
        subscribed,
        resolve_connector=_withdrawn,
    )
    schedule = _schedule(config={"provider_trigger_id": "ti_9"})

    await adapter.delete_provider_trigger(schedule)

    assert subscribed == [{"deleted": "ti_9"}]
