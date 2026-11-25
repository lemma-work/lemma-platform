"""A message heard in a connected Slack channel fires the schedules watching it.

The Slack connector's three triggers have no webhook of their own: the pod's
bot hears the channel, and each heard message is handed to `schedule` with the
pods it is for. Those pods are the tenancy -- a channel id in a schedule's
config is anybody's to type -- so what is tested here is mostly *who* hears:
the pods that connected the channel to a bot that heard it, in the workspace
the delivery names, and nobody else.
"""

from __future__ import annotations

from collections.abc import Collection
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import OperationalError

from app.core.domain.errors import DomainError

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
    SurfaceConfig,
    SurfacePlatform,
)
from app.modules.agent_surfaces.platforms.slack.parser import SlackMessageParser
from app.modules.agent_surfaces.services.channel_triggers import (
    fire_channel_triggers,
)
from app.modules.agent_surfaces.services.inbound_classifier import classify_slack
from app.modules.channels.contracts import Source
from app.modules.schedule.contracts.heard_events import NormalizedWebhook
from app.modules.test_support.group_sources import a_group

pytestmark = pytest.mark.unit

WORKSPACE = "T0INC"
CHANNEL = "C0INC42"


def _surface(*, workspace: str = WORKSPACE) -> AgentSurfaceEntity:
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        name="slack",
        agent_id=uuid4(),
        surface_type=SurfacePlatform.SLACK,
        pod_connector_slug="own-bot",
        external_workspace_id=workspace,
        surface_identity_id="U0BOT",
        config=SurfaceConfig(),
        is_active=True,
    )


def _said(**event: object) -> ParsedInboundSurfaceEvent:
    payload = {
        "type": "event_callback",
        "team_id": WORKSPACE,
        "event_id": "Ev0001",
        "event": {
            "type": "message",
            "user": "U0RESPONDER",
            "text": "Rolled back the deploy",
            "ts": "1700000100.000200",
            "channel": CHANNEL,
            "channel_type": "channel",
            **event,
        },
    }
    parsed = SlackMessageParser().parse(payload)
    assert parsed is not None
    return parsed


class _Places:
    """The places on a channel, by the surface whose account holds each."""

    def __init__(self, *groups: Source) -> None:
        self.groups = groups
        self.asked: list[Collection[UUID] | None] = []

    async def places_hearing(
        self,
        *,
        platform: str,
        external_channel_id: str,
        surface_ids: Collection[UUID] | None,
    ) -> list[Source]:
        self.asked.append(surface_ids)
        return [
            group
            for group in self.groups
            if group.external_channel_id == external_channel_id
            and surface_ids is not None
            and group.surface_id in surface_ids
        ]


class _Fired:
    def __init__(self) -> None:
        self.events: list[NormalizedWebhook] = []

    async def __call__(self, uow, event: NormalizedWebhook) -> list[UUID]:
        del uow
        self.events.append(event)
        return [uuid4()]


def _place(surface: AgentSurfaceEntity) -> Source:
    return a_group(
        pod_id=surface.pod_id,
        surface_id=surface.id,
        platform="SLACK",
        external_channel_id=CHANNEL,
    )


class _Savepoints:
    """A session's savepoints: how many were opened, and which were rolled back."""

    def __init__(self) -> None:
        self.opened = 0
        self.rolled_back: list[BaseException] = []

    def begin_nested(self) -> _Savepoints:
        self.opened += 1
        return self

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, kind, error, trace) -> bool:
        if error is not None:
            self.rolled_back.append(error)
        return False


class _Uow:
    def __init__(self) -> None:
        self.session = _Savepoints()


async def _fire(seats, surfaces, parsed, *, fire=None) -> tuple[bool, _Fired]:
    fired = fire or _Fired()
    result = await fire_channel_triggers(
        _Uow(), seats=seats, surfaces=surfaces, parsed=parsed, fire=fired
    )
    return result, fired


@pytest.mark.asyncio
async def test_a_message_fires_for_the_pod_that_connected_the_channel():
    surface = _surface()

    fired_any, fired = await _fire(_Places(_place(surface)), [surface], _said())

    assert fired_any is True
    (event,) = fired.events
    assert event.pod_ids == frozenset({str(surface.pod_id)})
    assert event.match == {
        "source": "slack",
        "event": "message",
        "team_id": WORKSPACE,
        "channel_id": CHANNEL,
    }


@pytest.mark.asyncio
async def test_a_receiving_pod_that_never_connected_the_channel_hears_nothing():
    """Its bot is in the workspace and verified the delivery, but the channel
    is not one it was brought into: no place, no run."""
    connected, elsewhere = _surface(), _surface()

    _, fired = await _fire(_Places(_place(connected)), [connected, elsewhere], _said())

    (event,) = fired.events
    assert event.pod_ids == frozenset({str(connected.pod_id)})


@pytest.mark.asyncio
async def test_a_channel_nobody_connected_fires_nothing():
    surface = _surface()

    fired_any, fired = await _fire(_Places(), [surface], _said())

    assert fired_any is False
    assert fired.events == []


@pytest.mark.asyncio
async def test_a_surface_in_another_workspace_is_not_asked():
    """A place is found by channel id, and nothing promises a channel id is
    unique across workspaces: only the workspace the delivery names counts."""
    ours, theirs = _surface(), _surface(workspace="T0OTHER")
    seats = _Places(_place(ours), _place(theirs))

    _, fired = await _fire(seats, [ours, theirs], _said())

    assert seats.asked == [[ours.id]]
    (event,) = fired.events
    assert event.pod_ids == frozenset({str(ours.pod_id)})


@pytest.mark.asyncio
async def test_a_direct_message_fires_no_channel_trigger():
    surface = _surface()
    seats = _Places(_place(surface))

    fired_any, fired = await _fire(
        seats, [surface], _said(channel="D0DM", channel_type="im")
    )

    assert fired_any is False
    assert seats.asked == []


@pytest.mark.asyncio
async def test_a_thread_reply_is_a_channel_message_and_a_thread_reply():
    surface = _surface()

    _, fired = await _fire(
        _Places(_place(surface)), [surface], _said(thread_ts="1700000000.000100")
    )

    assert [event.match["event"] for event in fired.events if event.match] == [
        "message",
        "message.thread",
    ]


def test_an_apps_own_message_never_reaches_the_triggers():
    """What stops a schedule that posts to the channel it watches from
    starting itself: an app's message is not a message anybody heard."""
    payload = {
        "type": "event_callback",
        "team_id": WORKSPACE,
        "event": {
            "type": "message",
            "bot_id": "B0LEMMA",
            "text": "Timeline updated",
            "ts": "1700000100.000300",
            "channel": CHANNEL,
            "channel_type": "channel",
        },
    }

    assert classify_slack(payload).drop_reason == "bot_echo"
    assert SlackMessageParser().parse(payload) is None


class _Refused:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def __call__(self, uow, event: NormalizedWebhook) -> list[UUID]:
        del uow, event
        raise self.error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        DomainError("a matched schedule names no agent"),
        OperationalError("insert", {}, Exception("deadlock detected")),
    ],
)
async def test_a_schedule_that_cannot_fire_never_costs_the_message(error):
    """The line the message wrote and the answer to it are the surface's: a
    schedule watching the channel that cannot be matched or fired is rolled
    back on its own, and the message carries on."""
    surface = _surface()
    uow = _Uow()

    fired_any = await fire_channel_triggers(
        uow,
        seats=_Places(_place(surface)),
        surfaces=[surface],
        parsed=_said(),
        fire=_Refused(error),
    )

    assert fired_any is False
    assert uow.session.opened == 1
    assert uow.session.rolled_back == [error]
