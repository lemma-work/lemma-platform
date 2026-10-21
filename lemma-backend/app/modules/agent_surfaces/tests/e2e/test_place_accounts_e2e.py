"""Places, the accounts they are in, and what they make of their channel.

Against Postgres, through ``channels.contracts.source_for`` -- what every
inbound message calls -- with real surface rows:

* a Slack surface heard before its bot id is known, then after, is one
  account and one channel: the account is given its bot in place. Another
  app in the same workspace is still an account of its own;
* a WhatsApp number's surface made again (the same phone-number id) finds the
  same account, so its groups keep their channels and history;
* a private place joining a public group makes the group private, and nothing
  a place says makes it public again;
* the lookup every inbound message makes first uses
  ``uq_channel_sources_place``, however many places other pods have.
"""

from __future__ import annotations

import json
from uuid import UUID, uuid4, uuid7

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.dialects import postgresql

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.infrastructure.models import AgentModel
from app.modules.agent_surfaces.domain.entities import AgentSurfaceStatus
from app.modules.agent_surfaces.domain.external_accounts import account_of
from app.modules.agent_surfaces.infrastructure.models import AgentSurface
from app.modules.channels.contracts import (
    AccountKey,
    GroupHome,
    Place,
    SourceKind,
    Visibility,
    source_for,
)
from app.modules.channels.contracts.places import place_became_private
from app.modules.channels.infrastructure.accounts import AccountRepository
from app.modules.channels.infrastructure.models import (
    ChannelModel,
    ChannelSourceModel,
    ExternalAccountModel,
)
from app.modules.channels.infrastructure.repository import ChannelRepository
from app.modules.channels.infrastructure.source_repository import place_lookup
from app.modules.channels.services.places import ChannelPlaces
from app.modules.pod.infrastructure.models.pod_models import Pod

pytestmark = pytest.mark.e2e


@pytest.fixture
async def pod_id(test_pod) -> UUID:
    return UUID(str(test_pod["id"]))


async def _surface(
    db_session,
    pod_id: UUID,
    platform: str,
    *,
    workspace: str | None = None,
    identity: str | None = None,
) -> AgentSurface:
    """An ACTIVE surface on an agent of its own (one surface per agent and
    platform)."""
    template = (
        await db_session.scalars(
            select(AgentModel).where(AgentModel.pod_id == pod_id).limit(1)
        )
    ).one()
    agent = AgentModel(
        id=uuid7(),
        pod_id=pod_id,
        user_id=template.user_id,
        name=f"agent-{uuid4().hex[:8]}",
        visibility=template.visibility,
        instruction="",
        toolsets=[],
        kind="USER",
    )
    db_session.add(agent)
    surface = AgentSurface(
        id=uuid7(),
        pod_id=pod_id,
        organization_id=await db_session.scalar(
            select(Pod.organization_id).where(Pod.id == pod_id)
        ),
        agent_id=agent.id,
        name=f"{platform.lower()}-{uuid4().hex[:8]}",
        surface_type=platform,
        event_mode="WEBHOOK",
        credential_mode="SYSTEM",
        config={},
        status=AgentSurfaceStatus.ACTIVE.value,
        external_workspace_id=workspace,
        surface_identity_id=identity,
    )
    db_session.add(surface)
    await db_session.commit()
    return surface


def _group(surface: AgentSurface, chat: str, *, is_private: bool = True) -> Place:
    entity = surface.to_entity()
    return Place(
        surface_id=surface.id,
        platform=entity.surface_type.value,
        account=account_of(entity),
        kind=SourceKind.CHAT,
        external_channel_id=chat,
        external_user_id=None,
        title=chat,
        is_private=is_private,
    )


async def _accounts(db_session, pod_id: UUID, platform: str):
    return (
        await db_session.scalars(
            select(ExternalAccountModel).where(
                ExternalAccountModel.pod_id == pod_id,
                ExternalAccountModel.platform == platform,
            )
        )
    ).all()


# -- one account per bot, whenever its id became known --------------------------


async def test_a_slack_bot_named_late_keeps_its_account_and_channel(db_session, pod_id):
    uow = SqlAlchemyUnitOfWork(db_session)
    surface = await _surface(db_session, pod_id, "SLACK", workspace="T-LATE")
    before = await source_for(
        uow, pod_id=pod_id, place=_group(surface, "C-1"), home=GroupHome(title=None)
    )
    await db_session.commit()

    surface.surface_identity_id = "B-REVIEWER"
    await db_session.commit()
    after = await source_for(
        uow, pod_id=pod_id, place=_group(surface, "C-1"), home=GroupHome(title=None)
    )
    await db_session.commit()

    assert after.id == before.id
    assert after.channel_id == before.channel_id
    [account] = await _accounts(db_session, pod_id, "SLACK")
    assert (account.workspace_key, account.bot_key) == ("T-LATE", "B-REVIEWER")

    # Another agent's app in the same workspace is an account of its own.
    other = await _surface(
        db_session, pod_id, "SLACK", workspace="T-LATE", identity="B-SUPPORT"
    )
    theirs = await source_for(
        uow, pod_id=pod_id, place=_group(other, "C-1"), home=GroupHome(title=None)
    )
    await db_session.commit()
    assert theirs.account_id != after.account_id
    assert theirs.channel_id != after.channel_id
    assert len(await _accounts(db_session, pod_id, "SLACK")) == 2


async def test_a_bot_another_surface_reached_unnamed_is_not_handed_that_account(
    db_session, pod_id
):
    """The bot-less account is given a bot only by the surface that reached it:
    a different app learning its own id in that workspace gets its own."""
    uow = SqlAlchemyUnitOfWork(db_session)
    unnamed = await _surface(db_session, pod_id, "SLACK", workspace="T-TWO")
    theirs = await source_for(
        uow, pod_id=pod_id, place=_group(unnamed, "C-9"), home=GroupHome(title=None)
    )
    named = await _surface(
        db_session, pod_id, "SLACK", workspace="T-TWO", identity="B-OTHER"
    )
    mine = await source_for(
        uow, pod_id=pod_id, place=_group(named, "C-9"), home=GroupHome(title=None)
    )
    await db_session.commit()

    assert mine.account_id != theirs.account_id
    keys = {
        (row.workspace_key, row.bot_key)
        for row in await _accounts(db_session, pod_id, "SLACK")
    }
    assert keys == {("T-TWO", None), ("T-TWO", "B-OTHER")}


async def test_a_whatsapp_number_set_up_again_keeps_its_groups(db_session, pod_id):
    uow = SqlAlchemyUnitOfWork(db_session)
    first = await _surface(db_session, pod_id, "WHATSAPP", identity="PN-100")
    group = await source_for(
        uow,
        pod_id=pod_id,
        place=_group(first, "group-1"),
        home=GroupHome(title="Suppliers"),
    )
    await db_session.commit()

    # Removed and set up again: a new surface for the same number.
    await db_session.delete(first)
    await db_session.commit()
    again = await _surface(db_session, pod_id, "WHATSAPP", identity="PN-100")
    found = await source_for(
        uow,
        pod_id=pod_id,
        place=_group(again, "group-1"),
        home=GroupHome(title="Suppliers"),
    )
    await db_session.commit()

    assert found.id == group.id
    assert found.channel_id == group.channel_id
    assert found.surface_id == again.id
    [account] = await _accounts(db_session, pod_id, "WHATSAPP")
    assert account.workspace_key == "PN-100" and account.bot_key is None


# -- visibility, from the places ---------------------------------------------------


async def test_a_private_place_joining_a_public_group_makes_it_private(
    db_session, pod_id
):
    uow = SqlAlchemyUnitOfWork(db_session)
    channel = await ChannelRepository(db_session).open_group(
        pod_id=pod_id, title="launch", visibility=Visibility.PUBLIC
    )
    account = await AccountRepository(db_session).account_for(
        pod_id=pod_id,
        platform="TELEGRAM",
        key=AccountKey(workspace="bot-visibility"),
        surface_id=None,
    )
    place = Place(
        surface_id=None,
        platform="TELEGRAM",
        account=AccountKey(workspace="bot-visibility"),
        kind=SourceKind.CHAT,
        external_channel_id="tg-private-group",
        external_user_id=None,
        is_private=True,
    )

    added = await ChannelPlaces(uow).attach(channel, account.id, place)
    await db_session.commit()

    assert added is not None
    joined = await db_session.get(ChannelModel, channel.id, populate_existing=True)
    assert joined.visibility == Visibility.PRIVATE.value


async def test_a_mirror_is_public_only_on_the_platforms_word_and_never_again(
    db_session, pod_id
):
    uow = SqlAlchemyUnitOfWork(db_session)
    surface = await _surface(
        db_session, pod_id, "SLACK", workspace="T-VIS", identity="B-VIS"
    )
    public = await source_for(
        uow,
        pod_id=pod_id,
        place=_group(surface, "C-OPEN", is_private=False),
        home=GroupHome(title="#open"),
    )
    private = await source_for(
        uow,
        pod_id=pod_id,
        place=_group(surface, "C-SHUT"),
        home=GroupHome(title="#shut"),
    )
    await db_session.commit()

    def visibility(channel_id):
        return db_session.scalar(
            select(ChannelModel.visibility)
            .where(ChannelModel.id == channel_id)
            .execution_options(populate_existing=True)
        )

    assert await visibility(public.channel_id) == "PUBLIC"
    assert await visibility(private.channel_id) == "PRIVATE"

    assert await place_became_private(uow, public.id)
    await db_session.commit()
    assert await visibility(public.channel_id) == "PRIVATE"
    # Said again, nothing changes; and nothing a place says opens it back up.
    assert not await place_became_private(uow, public.id)


# -- the first read of every inbound message ---------------------------------------


_OTHER_PODS = 3
_PLACES_PER_ACCOUNT = 150


async def _crowded_account(db_session, pod_id: UUID, *, prefix: str) -> UUID:
    """An account with many places in it: group chats and private chats."""
    account = ExternalAccountModel(
        id=uuid7(),
        pod_id=pod_id,
        platform="TELEGRAM",
        workspace_key=f"bot-{prefix}",
    )
    channel = ChannelModel(
        id=uuid7(), pod_id=pod_id, kind="GROUP", visibility="PRIVATE"
    )
    db_session.add_all([account, channel])
    await db_session.flush()
    for index in range(_PLACES_PER_ACCOUNT):
        for kind, chat, person in (
            ("CHAT", f"{prefix}-chat-{index}", None),
            ("PERSON", None, f"{prefix}-user-{index}"),
        ):
            db_session.add(
                ChannelSourceModel(
                    id=uuid7(),
                    channel_id=channel.id,
                    pod_id=pod_id,
                    account_id=account.id,
                    platform="TELEGRAM",
                    kind=kind,
                    external_channel_id=chat,
                    external_user_id=person,
                )
            )
    await db_session.commit()
    return account.id


def _nodes(plan: dict):
    yield plan
    for child in plan.get("Plans", []):
        yield from _nodes(child)


async def test_finding_a_place_reads_its_unique_index_across_pods(
    db_session, test_pod, pod_id
):
    owner = await db_session.scalar(select(Pod.user_id).where(Pod.id == pod_id))
    organization = await db_session.scalar(
        select(Pod.organization_id).where(Pod.id == pod_id)
    )
    for index in range(_OTHER_PODS):
        other = Pod(
            user_id=owner,
            organization_id=organization,
            name=f"other pod {index} {uuid4().hex[:6]}",
            config={},
        )
        db_session.add(other)
        await db_session.flush()
        await _crowded_account(db_session, other.id, prefix=f"other-{index}")
    account_id = await _crowded_account(db_session, pod_id, prefix="mine")
    await db_session.execute(text("ANALYZE channel_sources"))
    assert (
        await db_session.scalar(select(func.count()).select_from(ChannelSourceModel))
        >= (_OTHER_PODS + 1) * _PLACES_PER_ACCOUNT * 2
    )

    for chat, person in (("mine-chat-7", None), (None, "mine-user-7")):
        statement = place_lookup(
            account_id=account_id, external_channel_id=chat, external_user_id=person
        ).compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
        payload = (
            await db_session.execute(
                text(f"EXPLAIN (ANALYZE, FORMAT JSON) {statement}")
            )
        ).scalar_one()
        plan = (json.loads(payload) if isinstance(payload, str) else payload)[0]["Plan"]
        on_sources = [
            node
            for node in _nodes(plan)
            if node.get("Relation Name") == "channel_sources"
        ]
        assert on_sources, plan
        assert {node.get("Index Name") for node in on_sources} == {
            "uq_channel_sources_place"
        }, [node["Node Type"] for node in on_sources]
        read = sum(
            int(node.get("Actual Rows", 0)) + int(node.get("Rows Removed by Filter", 0))
            for node in on_sources
        )
        assert read == 1, f"the lookup read {read} places to find one"
