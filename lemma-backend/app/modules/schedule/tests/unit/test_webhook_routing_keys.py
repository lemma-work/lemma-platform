"""A routing key only provisioning writes cannot be claimed by typing it in.

An inbound webhook is matched by containment against every tenant's schedule
config. A trigger, subscription or installation id is what keeps one tenant's
events out of another's schedules, so a schedule that carried one it was never
provisioned with would receive someone else's events.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.modules.schedule.contracts.webhook_source import NormalizedWebhook
from app.modules.schedule.domain.schedule import (
    ScheduleEntity,
    ScheduleType,
    authored_config,
)
from app.modules.schedule.services.webhook_handler import WebhookHandler
from app.modules.schedule.services.webhook_schedule_matcher import (
    WebhookScheduleMatcher,
)


class _Schedules:
    """Every row whose config contains the criteria, across all tenants --
    which is what the real `find_by_config` returns."""

    def __init__(self, *rows: ScheduleEntity) -> None:
        self.rows = rows

    async def find_by_config(
        self, schedule_type: ScheduleType, criteria: dict[str, Any] | None = None
    ) -> list[ScheduleEntity]:
        wanted = criteria or {}
        return [
            row
            for row in self.rows
            if row.schedule_type == schedule_type
            and all(row.config.get(key) == value for key, value in wanted.items())
        ]


class _Fired:
    def __init__(self) -> None:
        self.schedule_ids: list[UUID] = []

    async def publish_schedule_fired(self, *, schedule: ScheduleEntity, **_: object):
        self.schedule_ids.append(schedule.id)


@asynccontextmanager
async def _no_uow():
    yield None


def _webhook(config: dict[str, Any], *, account_id: UUID | None) -> ScheduleEntity:
    return ScheduleEntity(
        id=uuid4(),
        user_id=uuid4(),
        schedule_type=ScheduleType.WEBHOOK,
        account_id=account_id,
        connector_trigger_id="trigger" if account_id else None,
        config=config,
        is_active=True,
    )


def test_an_author_cannot_send_a_key_only_provisioning_writes() -> None:
    assert authored_config(
        {
            "source": "github",
            "event": "push",
            "installation_id": "42",
            "provider_trigger_id": "ti_1",
        }
    ) == {"source": "github", "event": "push"}


@pytest.mark.asyncio
async def test_a_typed_in_trigger_id_routes_nothing_to_an_unbound_schedule() -> None:
    owner = _webhook({"provider_trigger_id": "ti_1"}, account_id=uuid4())
    forged = _webhook({"provider_trigger_id": "ti_1"}, account_id=None)
    matcher = WebhookScheduleMatcher(schedule_repository=_Schedules(owner, forged))

    by_plugin = await matcher.match_criteria({"provider_trigger_id": "ti_1"})
    by_composio = await matcher.match("composio", {"provider_id": "ti_1"})

    assert [s.id for s in by_plugin] == [owner.id]
    assert [s.id for s in by_composio] == [owner.id]


@pytest.mark.asyncio
async def test_a_typed_in_installation_id_routes_nothing_to_an_unbound_schedule() -> (
    None
):
    routing = {"source": "github", "installation_id": "42", "event": "push"}
    owner = _webhook(routing, account_id=uuid4())
    forged = _webhook(dict(routing), account_id=None)
    matcher = WebhookScheduleMatcher(schedule_repository=_Schedules(owner, forged))

    assert [s.id for s in await matcher.match_criteria(routing)] == [owner.id]


@pytest.mark.asyncio
async def test_a_key_anyone_may_type_still_matches_unbound_schedules() -> None:
    plain = _webhook({"source": "custom", "event": "ping"}, account_id=None)
    matcher = WebhookScheduleMatcher(schedule_repository=_Schedules(plain))

    assert await matcher.match_criteria({"source": "custom", "event": "ping"}) == [
        plain
    ]


@pytest.mark.asyncio
async def test_a_delivery_verified_by_one_account_fires_only_that_accounts_schedules() -> (
    None
):
    mine, theirs = uuid4(), uuid4()
    routed = {"provider_trigger_id": "sub_1"}
    own = _webhook(dict(routed), account_id=mine)
    other = _webhook(dict(routed), account_id=theirs)
    fired = _Fired()
    handler = WebhookHandler(
        matcher_factory=lambda _uow: WebhookScheduleMatcher(
            schedule_repository=_Schedules(own, other)
        ),
        uow_factory=_no_uow,
        event_publisher=fired,
    )

    started = await handler.handle_webhook(
        source="mcp",
        payload={},
        normalized=NormalizedWebhook(
            payload={"event": "issue.created"},
            source_event_id="mcp:sub_1:evt_1",
            match=routed,
            account_id=str(mine),
        ),
    )

    assert started == [own.id]
    assert fired.schedule_ids == [own.id]
