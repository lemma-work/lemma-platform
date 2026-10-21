"""The in-memory channel seats against the real ones, place for place.

``InMemoryChannelSeats`` stands in for ``ChannelSeatsInDatabase`` in the
binder, routing and delivery unit tests. A double that keys places differently
from the tables certifies exactly the half it gets wrong -- it once keyed a
source by the *surface* that heard it, while channels key it by the
*account* (``external_accounts``: pod, platform, workspace and bot), so a
reinstalled bot found its old places in the database and lost them in every
unit test. Each scenario here runs against both, over the same pod and the
same surfaces, and what each answers must be the same.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.infrastructure.models import AgentSurface
from app.modules.agent_surfaces.services.channel_places import (
    ChannelSeats,
    ChannelSeatsInDatabase,
)
from app.modules.agent_surfaces.tests.e2e.helpers import _create_agent
from app.modules.channels.contracts import (
    AccountKey,
    GroupHome,
    MembersHome,
    Place,
    SourceKind,
)
from app.modules.test_support.channel_seats_double import InMemoryChannelSeats

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

PERSON = "U-PRIYA"
CHAT = "C-LAUNCH"


@dataclass(frozen=True)
class World:
    """The pod, one of its members, and three bots: two installations of one
    Slack app in workspace T1 (the second a reinstall), one in workspace T2."""

    pod_id: UUID
    user_id: UUID
    first: UUID
    reinstalled: UUID
    other_workspace: UUID


def _private(surface_id: UUID | None, team: str) -> Place:
    return Place(
        surface_id=surface_id,
        platform="SLACK",
        account=AccountKey(workspace=f"team:{team}"),
        kind=SourceKind.PERSON,
        external_channel_id=None,
        external_user_id=PERSON,
    )


def _shared(surface_id: UUID | None, team: str) -> Place:
    return Place(
        surface_id=surface_id,
        platform="SLACK",
        account=AccountKey(workspace=f"team:{team}"),
        kind=SourceKind.CHAT,
        external_channel_id=CHAT,
        external_user_id=None,
        title="launch",
        is_private=False,
    )


# -- the scenarios: each returns what the seats answered, ids made relative --


async def _the_same_place_is_one_source(seats: ChannelSeats, w: World) -> object:
    home = MembersHome(w.user_id)
    first = await seats.source_for(
        pod_id=w.pod_id, place=_private(w.first, "T1"), home=home
    )
    again = await seats.source_for(
        pod_id=w.pod_id, place=_private(w.first, "T1"), home=home
    )
    return {"same source": again.id == first.id, "surface": first.surface_id}


async def _a_reinstalled_bot_keeps_its_places(seats: ChannelSeats, w: World) -> object:
    home = MembersHome(w.user_id)
    before = await seats.source_for(
        pod_id=w.pod_id, place=_private(w.first, "T1"), home=home
    )
    after = await seats.source_for(
        pod_id=w.pod_id, place=_private(w.reinstalled, "T1"), home=home
    )
    via_old = await seats.person_places(
        platform="SLACK", external_user_id=PERSON, surface_ids=[w.first]
    )
    via_new = await seats.person_places(
        platform="SLACK", external_user_id=PERSON, surface_ids=[w.reinstalled]
    )
    people = await seats.people_places(
        platform="SLACK",
        external_user_ids=[PERSON],
        surface_id=w.reinstalled,
        pod_id=w.pod_id,
    )
    return {
        "same source": after.id == before.id,
        "same channel": after.channel_id == before.channel_id,
        "reached through": after.surface_id,
        "found through the old bot": [s.id == before.id for s in via_old],
        "found through the new bot": [s.id == before.id for s in via_new],
        "people": {k: v.id == before.id for k, v in people.items()},
    }


async def _another_workspace_is_another_place(seats: ChannelSeats, w: World) -> object:
    home = MembersHome(w.user_id)
    here = await seats.source_for(
        pod_id=w.pod_id, place=_private(w.first, "T1"), home=home
    )
    there = await seats.source_for(
        pod_id=w.pod_id, place=_private(w.other_workspace, "T2"), home=home
    )
    everywhere = await seats.person_places(
        platform="SLACK", external_user_id=PERSON, pod_id=w.pod_id
    )
    return {
        "same source": there.id == here.id,
        "same channel": there.channel_id == here.channel_id,
        "places in the pod": len(everywhere),
    }


async def _one_chat_in_two_workspaces_is_two_groups(
    seats: ChannelSeats, w: World
) -> object:
    home = GroupHome(title="launch")
    here = await seats.source_for(
        pod_id=w.pod_id, place=_shared(w.first, "T1"), home=home
    )
    there = await seats.source_for(
        pod_id=w.pod_id, place=_shared(w.other_workspace, "T2"), home=home
    )
    heard_by_first = await seats.places_hearing(
        platform="SLACK", external_channel_id=CHAT, surface_ids=[w.first]
    )
    heard_by_reinstall = await seats.places_hearing(
        platform="SLACK", external_channel_id=CHAT, surface_ids=[w.reinstalled]
    )
    heard_by_nobody = await seats.places_hearing(
        platform="SLACK", external_channel_id=CHAT, surface_ids=[]
    )
    return {
        "same source": there.id == here.id,
        "same channel": there.channel_id == here.channel_id,
        "heard by the first bot": [s.id == here.id for s in heard_by_first],
        "heard by a bot that never heard it": len(heard_by_reinstall),
        "heard by no surfaces": len(heard_by_nobody),
    }


async def _a_place_heard_with_no_bot_is_taken_up_by_the_next(
    seats: ChannelSeats, w: World
) -> object:
    home = MembersHome(w.user_id)
    orphan = await seats.source_for(
        pod_id=w.pod_id, place=_private(None, "T1"), home=home
    )
    taken = await seats.source_for(
        pod_id=w.pod_id, place=_private(w.first, "T1"), home=home
    )
    # A report naming no bot leaves the account with the bot it has.
    still = await seats.source_for(
        pod_id=w.pod_id, place=_private(None, "T1"), home=home
    )
    return {
        "orphan reached through": orphan.surface_id,
        "taken over by": taken.surface_id,
        "kept by": still.surface_id,
        "one source": len({orphan.id, taken.id, still.id}),
    }


async def _what_was_heard_is_kept_on_the_place(seats: ChannelSeats, w: World) -> object:
    home = MembersHome(w.user_id)
    source = await seats.source_for(
        pod_id=w.pod_id, place=_private(w.first, "T1"), home=home
    )
    await seats.heard_at(source.id, {"external_thread_id": "D1"})
    [heard] = await seats.person_places(
        platform="SLACK", external_user_id=PERSON, surface_ids=[w.first]
    )
    await seats.addressed_at(source.id, {"external_thread_id": "D2"})
    [addressed] = await seats.person_places(
        platform="SLACK", external_user_id=PERSON, surface_ids=[w.first]
    )
    return {
        "heard ref": heard.delivery_ref,
        "heard stamped": heard.last_inbound_at is not None,
        "addressed ref": addressed.delivery_ref,
        "addressed keeps the hearing": addressed.last_inbound_at
        == heard.last_inbound_at,
    }


type Scenario = Callable[[ChannelSeats, World], Awaitable[object]]

SCENARIOS: dict[str, tuple[Scenario, Callable[[World], object]]] = {
    "same place": (
        _the_same_place_is_one_source,
        lambda w: {"same source": True, "surface": w.first},
    ),
    "reinstalled bot": (
        _a_reinstalled_bot_keeps_its_places,
        lambda w: {
            "same source": True,
            "same channel": True,
            "reached through": w.reinstalled,
            "found through the old bot": [],
            "found through the new bot": [True],
            "people": {PERSON: True},
        },
    ),
    "another workspace": (
        _another_workspace_is_another_place,
        lambda w: {"same source": False, "same channel": True, "places in the pod": 2},
    ),
    "group in two workspaces": (
        _one_chat_in_two_workspaces_is_two_groups,
        lambda w: {
            "same source": False,
            "same channel": False,
            "heard by the first bot": [True],
            "heard by a bot that never heard it": 0,
            "heard by no surfaces": 0,
        },
    ),
    "taken over": (
        _a_place_heard_with_no_bot_is_taken_up_by_the_next,
        lambda w: {
            "orphan reached through": None,
            "taken over by": w.first,
            "kept by": w.first,
            "one source": 1,
        },
    ),
    "heard and addressed": (
        _what_was_heard_is_kept_on_the_place,
        lambda w: {
            "heard ref": {"external_thread_id": "D1"},
            "heard stamped": True,
            "addressed ref": {"external_thread_id": "D2"},
            "addressed keeps the hearing": True,
        },
    ),
}


async def _world(
    client: AsyncClient, db_session: AsyncSession, pod: dict, user: dict
) -> World:
    """A fresh pod's worth of bots, each its own agent's (one Slack bot per
    agent): the accounts are the pod's, so each scenario starts with none."""

    async def bot() -> UUID:
        agent = await _create_agent(client, pod["id"])
        row = AgentSurface(
            pod_id=UUID(pod["id"]),
            organization_id=UUID(pod["organization_id"]),
            agent_id=UUID(agent["id"]),
            name=f"slack-{uuid4().hex[:6]}",
            surface_type="SLACK",
            event_mode="WEBHOOK",
            credential_mode="SYSTEM",
            config={},
        )
        db_session.add(row)
        await db_session.flush()
        return row.id

    world = World(
        pod_id=UUID(pod["id"]),
        user_id=UUID(user["id"]),
        first=await bot(),
        reinstalled=await bot(),
        other_workspace=await bot(),
    )
    await db_session.commit()
    return world


@pytest.mark.parametrize("name", sorted(SCENARIOS))
async def test_the_double_keys_places_as_the_tables_do(
    name, authenticated_client, db_session, test_pod, fixed_test_user
):
    scenario, expected = SCENARIOS[name]
    world = await _world(authenticated_client, db_session, test_pod, fixed_test_user)

    uow = SqlAlchemyUnitOfWork(db_session)
    real = await scenario(ChannelSeatsInDatabase(uow), world)
    await uow.commit()
    double = await scenario(InMemoryChannelSeats(), world)

    assert real == expected(world), "the tables changed: say so in the double"
    assert double == real
