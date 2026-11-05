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
from app.modules.schedule.services.trigger_fire import FireOutcome
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

    async def __call__(self, uow, schedule: ScheduleEntity, event) -> FireOutcome:
        del uow, event
        self.schedule_ids.append(schedule.id)
        return FireOutcome.STARTED


@asynccontextmanager
async def _no_uow():
    yield None


def _webhook(config: dict[str, Any], *, bound: bool) -> ScheduleEntity:
    """A webhook schedule; ``bound`` ones listen through a pod connector."""
    return ScheduleEntity(
        id=uuid4(),
        user_id=uuid4(),
        pod_id=uuid4(),
        schedule_type=ScheduleType.WEBHOOK,
        pod_connector_slug="tracker" if bound else None,
        connector_trigger_id="trigger" if bound else None,
        config=config,
        is_active=True,
    )


class _Resolves:
    """What each schedule's pod connector resolves to now; absent: nothing."""

    def __init__(self, accounts: dict[UUID, UUID]) -> None:
        self.accounts = accounts

    async def __call__(self, _uow, schedule: ScheduleEntity) -> UUID | None:
        return self.accounts.get(schedule.id)


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
    owner = _webhook({"provider_trigger_id": "ti_1"}, bound=True)
    forged = _webhook({"provider_trigger_id": "ti_1"}, bound=False)
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
    owner = _webhook(routing, bound=True)
    forged = _webhook(dict(routing), bound=False)
    matcher = WebhookScheduleMatcher(schedule_repository=_Schedules(owner, forged))

    assert [s.id for s in await matcher.match_criteria(routing)] == [owner.id]


@pytest.mark.asyncio
async def test_a_key_anyone_may_type_still_matches_unbound_schedules() -> None:
    plain = _webhook({"source": "custom", "event": "ping"}, bound=False)
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
    own = _webhook(dict(routed), bound=True)
    other = _webhook(dict(routed), bound=True)
    fired = _Fired()
    handler = WebhookHandler(
        matcher_factory=lambda _uow: WebhookScheduleMatcher(
            schedule_repository=_Schedules(own, other)
        ),
        uow_factory=_no_uow,
        fire=fired,
        bound_account=_Resolves({own.id: mine, other.id: theirs}),
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


@pytest.mark.asyncio
async def test_a_schedule_whose_connector_no_longer_resolves_does_not_fire() -> None:
    """Its owner left the pod, or the shared account was withdrawn: the
    routing key still matches, and the schedule must not run on it."""
    routing = {"source": "github", "installation_id": "42", "event": "push"}
    live = _webhook(dict(routing), bound=True)
    stopped = _webhook(dict(routing), bound=True)
    fired = _Fired()
    handler = WebhookHandler(
        matcher_factory=lambda _uow: WebhookScheduleMatcher(
            schedule_repository=_Schedules(live, stopped)
        ),
        uow_factory=_no_uow,
        fire=fired,
        bound_account=_Resolves({live.id: uuid4()}),
    )

    started = await handler.handle_webhook(
        source="github",
        payload={},
        normalized=NormalizedWebhook(
            payload={"action": "opened"},
            source_event_id="github:delivery-1",
            match=routing,
        ),
    )

    assert started == [live.id]
    assert fired.schedule_ids == [live.id]


@pytest.mark.asyncio
async def test_a_delivery_verified_by_an_account_never_fires_an_unbound_schedule() -> (
    None
):
    routed = {"provider_trigger_id": "sub_1"}
    plain = _webhook(dict(routed), bound=False)
    fired = _Fired()
    handler = WebhookHandler(
        matcher_factory=lambda _uow: WebhookScheduleMatcher(
            schedule_repository=_Schedules(plain)
        ),
        uow_factory=_no_uow,
        fire=fired,
        bound_account=_Resolves({}),
    )

    started = await handler.handle_webhook(
        source="mcp",
        payload={},
        normalized=NormalizedWebhook(
            payload={},
            source_event_id="mcp:sub_1:evt_1",
            match=routed,
            account_id=str(uuid4()),
        ),
    )

    assert started == []
