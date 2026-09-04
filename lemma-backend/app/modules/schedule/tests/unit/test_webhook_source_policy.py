"""A webhook schedule may only route on a source this deployment can deliver.

The registry is the ingress allow-list -- `POST /webhooks/{source}` refuses a
source with no plugin -- so a schedule routed on anything else is accepted,
stored, listed as active and permanently silent. These tests are about that
refusal happening where the author is still there to read it, and about it
sparing the schedules whose routing key provisioning writes instead.
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.connectors.contracts.webhook_sources import default_webhook_sources
from app.modules.schedule.contracts.webhook_source import (
    NormalizedWebhook,
    VerifiedDelivery,
    WebhookDelivery,
    WebhookSourceRegistry,
)
from app.modules.schedule.domain.errors import ScheduleValidationError
from app.modules.schedule.domain.schedule import (
    ScheduleCreateEntity,
    ScheduleEntity,
    ScheduleType,
    ScheduleUpdateEntity,
)
from app.modules.schedule.services.schedule_service import ScheduleService
from app.modules.schedule.services.webhook_source_policy import (
    validate_webhook_source,
)


class _StubSource:
    """A plugin that claims one source and is never actually delivered to.

    Named rather than reusing the real plugins so the refusal can be tested
    against sources that certainly do not exist, without asserting anything
    about which ones this deployment happens to ship.
    """

    def __init__(self, source: str) -> None:
        self._source = source

    @property
    def source(self) -> str:
        return self._source

    async def verify(self, delivery: WebhookDelivery) -> VerifiedDelivery:
        return VerifiedDelivery(delivery=delivery, payload={})

    async def observe(self, verified: VerifiedDelivery) -> None:
        return None

    def normalize(self, verified: VerifiedDelivery) -> NormalizedWebhook | None:
        return None


def _webhook_create(**overrides: object) -> ScheduleCreateEntity:
    fields: dict[str, object] = {
        "user_id": uuid4(),
        "schedule_type": ScheduleType.WEBHOOK,
        "config": {"source": "beta"},
        "visibility": "PERSONAL",
    }
    fields.update(overrides)
    return ScheduleCreateEntity(**fields)


def _service(registry: WebhookSourceRegistry, repository: AsyncMock) -> ScheduleService:
    return ScheduleService(
        uow=AsyncMock(),
        schedule_repository=repository,
        external_schedule_writer=AsyncMock(),
        webhook_sources=registry,
    )


def test_an_unknown_source_is_refused_and_the_message_names_the_accepted_ones():
    registry = WebhookSourceRegistry([_StubSource("alpha"), _StubSource("beta")])

    with pytest.raises(ScheduleValidationError) as refusal:
        validate_webhook_source(_webhook_create(config={"source": "gamma"}), registry)

    message = str(refusal.value)
    assert "gamma" in message
    # Read out of the registry rather than spelled out here: a deployment that
    # adds a source must not need this test edited to keep telling the truth.
    for accepted in registry.sources:
        assert accepted in message


def test_every_source_this_deployment_accepts_may_be_named_by_a_schedule():
    registry = default_webhook_sources()

    assert registry.sources, "a deployment that accepts no webhook source"
    for accepted in registry.sources:
        validate_webhook_source(_webhook_create(config={"source": accepted}), registry)


def test_a_source_is_matched_the_way_the_ingress_matches_it():
    """Creation and delivery must agree, or a schedule is refused for casing."""
    registry = WebhookSourceRegistry([_StubSource("alpha")])

    validate_webhook_source(_webhook_create(config={"source": " Alpha "}), registry)


def test_a_config_that_names_no_source_is_left_alone():
    # A connector-backed schedule is routed by the key its provisioning writes
    # -- a GitHub installation id, a Composio provider trigger id -- and that
    # key does not exist yet at the moment the row is created.
    validate_webhook_source(
        _webhook_create(config={"actions": ["opened"]}), WebhookSourceRegistry([])
    )


def test_a_schedule_that_provisioning_will_route_is_not_judged_on_its_source():
    """`source` is not the routing key once a provider trigger supplies one.

    A connector-bound schedule is matched on what provisioning writes -- a
    provider trigger id, or an installation id and event that overwrite
    `source` outright -- and one that cannot be provisioned is already refused
    by the writer. Refusing it here as well would reject working schedules over
    a word nothing reads.
    """
    registry = WebhookSourceRegistry([_StubSource("alpha")])

    validate_webhook_source(
        _webhook_create(
            config={"source": "gmail"},
            account_id=uuid4(),
            connector_trigger_id="gmail:new_message",
        ),
        registry,
    )


def test_a_blank_source_is_refused():
    registry = WebhookSourceRegistry([_StubSource("alpha")])

    with pytest.raises(ScheduleValidationError):
        validate_webhook_source(_webhook_create(config={"source": "   "}), registry)


@pytest.mark.asyncio
async def test_create_refuses_a_webhook_schedule_no_source_will_ever_deliver():
    repository = AsyncMock()
    service = _service(WebhookSourceRegistry([_StubSource("alpha")]), repository)

    with pytest.raises(ScheduleValidationError) as refusal:
        await service.create_schedule(_webhook_create())

    assert "alpha" in str(refusal.value)
    repository.create.assert_not_called()


@pytest.mark.asyncio
async def test_create_accepts_a_webhook_schedule_naming_a_source_that_delivers():
    repository = AsyncMock()
    service = _service(WebhookSourceRegistry([_StubSource("alpha")]), repository)
    schedule_create = _webhook_create(config={"source": "alpha"})
    repository.create.return_value = ScheduleEntity(
        id=uuid4(), **schedule_create.model_dump()
    )

    created = await service.create_schedule(schedule_create)

    assert created.config == {"source": "alpha"}


@pytest.mark.asyncio
async def test_update_refuses_repointing_a_schedule_at_a_source_that_cannot_deliver():
    repository = AsyncMock()
    service = _service(WebhookSourceRegistry([_StubSource("alpha")]), repository)
    schedule_id = uuid4()
    repository.get.return_value = ScheduleEntity(
        id=schedule_id,
        user_id=uuid4(),
        schedule_type=ScheduleType.WEBHOOK,
        config={"source": "alpha"},
    )

    with pytest.raises(ScheduleValidationError):
        await service.update_schedule(
            schedule_id,
            ScheduleUpdateEntity(config={"source": "beta"}),
        )

    repository.update.assert_not_called()
