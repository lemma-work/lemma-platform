"""A Slack workspace the pod's organization holds: its people are contacts.

Against the real tables -- the manager's install, Slack's proof of a person,
contacts, channels -- the real ingress a work item runs, and the fake Slack Web
API. Lemma's own app binds the workspace to the pod's organization, and a
teammate's own bot there:

* answers a colleague outside the pod as a SLACK contact, ``{their team}:
  {their user}``, from what the pod made Public, in the contact's own channel;
  a guest only where a group welcomes people from outside the pod, never in a
  private chat, and nobody Slack did not describe;
* routes a person Slack proved as that person, whatever their profile email
  says, and never routes a Slack user who merely carries a proven person's
  email as them;
* asks nobody to sign up by email code first -- but only where the bot's own
  pod's organization holds the workspace. A bot in a workspace another
  organization holds behaves as it always did;
* answers by default, looked after by whoever connected it.

Forgetting such a contact forgets the Slack profile kept for them, and for a
Slack Connect visitor too, whose profile is kept under the pod's workspace.

Each test has a workspace of its own: a binding is one per workspace.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent_surfaces.composition import build_surface_ingress
from app.modules.agent_surfaces.domain.ingress_context import (
    SurfaceChatContext,
    SurfaceReplyContext,
)
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.infrastructure.models import (
    AgentSurfaceExternalUser,
)
from app.modules.agent_surfaces.infrastructure.onboarding_models import (
    PendingChatOnboarding,
)
from app.modules.agent_surfaces.infrastructure.repositories.external_user_repository import (  # noqa: E501
    ExternalSurfaceUserRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.slack_manager_repository import (  # noqa: E501
    PROVED_BY_OIDC,
    SlackManagerRepository,
)
from app.modules.agent_surfaces.services.chat_onboarding import (
    ChatOnboardingCoordinator,
)
from app.modules.agent_surfaces.services.slack_person_proof import record_proof
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _create_agent,
    _load_slack_dm_fixture,
)
from app.modules.agent_surfaces.tests.e2e.surface_work_support import (
    clean_surface_work,  # noqa: F401  (fixture)
    drain,
)
from app.modules.agent_surfaces.tests.e2e.test_slack_manager_e2e import (
    _bound,
    _hand_over,
    _id,
    _registry,
    _slack_account,
    _teammate,
    _Workspace,
    lemmas_app,  # noqa: F401  (fixture)
)
from app.modules.channels.infrastructure.message_models import ChannelMessageModel
from app.modules.channels.infrastructure.models import (
    ChannelModel,
    ChannelSourceModel,
)
from app.modules.contacts.contracts import (
    IdentityKind,
    IdentityStrength,
    open_contact,
)
from app.modules.contacts.infrastructure.models import (
    ContactIdentityModel,
    ContactSuppressionModel,
)
from app.modules.test_support.e2e_base import _create_org, _sign_up_verified_user

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.drives_work,
    pytest.mark.usefixtures("clean_surface_work", "lemmas_app"),
]


class _Bot:
    """A teammate's own bot in one workspace, and how a person writes to it."""

    def __init__(self, workspace: _Workspace, surface: dict, bot_user: str) -> None:
        self.workspace = workspace
        self.surface = surface
        self.bot_user = bot_user

    def says(
        self,
        sender: str,
        text: str = "where is the launch doc?",
        *,
        channel: str | None = None,
        mention: bool = True,
    ) -> SurfacePlatformWebhookIngress:
        payload = _load_slack_dm_fixture(
            text=text, ts=f"1700000000.{uuid4().int % 1_000_000:06d}"
        )
        team = self.workspace.team
        payload["team_id"] = team
        payload["api_app_id"] = "A-TEAMMATE"
        payload["authorizations"] = [
            {"team_id": team, "user_id": self.bot_user, "is_bot": True}
        ]
        event = payload["event"]
        event.pop("assistant_thread", None)
        event.update({"user": sender, "team": team, "channel": f"D{sender}"})
        if channel is not None:
            event.update(
                {
                    "type": "app_mention" if mention else "message",
                    "channel": channel,
                    "channel_type": "channel",
                    "text": f"<@{self.bot_user}> {text}" if mention else text,
                }
            )
        return SurfacePlatformWebhookIngress(
            source="slack",
            payload=payload,
            headers={},
            receiver_surface_ids=[UUID(self.surface["id"])],
        )


async def _held_bot(client, db_session, pod, user, fake_slack) -> _Bot:
    """Lemma's app holds the workspace for the pod's organization, then the
    teammate's own bot arrives in it."""
    workspace = await _bound(client, db_session, pod, user, fake_slack)
    surface, bot = await _teammate(
        client,
        db_session,
        pod=pod,
        user=user,
        workspace=workspace,
        fake_slack=fake_slack,
    )
    return _Bot(workspace, surface, bot)


async def _prepare(request: SurfacePlatformWebhookIngress) -> Any:
    """What ingress makes of a delivery, as ``surface.ingest`` runs it."""
    async with async_session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        context = await build_surface_ingress(uow).prepare_ingress(request)
        await uow.commit()
    return context


async def _contact_of(db_session: AsyncSession, pod_id: str, handle: str):
    db_session.expire_all()
    return await db_session.scalar(
        select(ContactIdentityModel).where(
            ContactIdentityModel.pod_id == UUID(pod_id),
            ContactIdentityModel.kind == IdentityKind.SLACK.value,
            ContactIdentityModel.value == handle,
        )
    )


async def _channel_of(db_session: AsyncSession, source_id: UUID | None):
    return await db_session.scalar(
        select(ChannelModel)
        .join(ChannelSourceModel, ChannelSourceModel.channel_id == ChannelModel.id)
        .where(ChannelSourceModel.id == source_id)
    )


async def _prove(team: str, slack_user: str, user_id: str) -> None:
    async with async_session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        await record_proof(
            uow,
            team_id=team,
            slack_user_id=slack_user,
            user_id=UUID(user_id),
            proved_by=PROVED_BY_OIDC,
        )
        await uow.commit()


def _colleague(fake_slack, **profile: object) -> str:
    """A person in the workspace whose email is nobody's in Lemma."""
    user = _id("U")
    fake_slack.user_overrides[user] = {
        "email": f"{user.lower()}@acme.example",
        "display_name": "Colleague",
        **profile,
    }
    return user


# -- in a private chat ---------------------------------------------------------


async def test_a_colleague_writing_privately_is_a_slack_contact_in_their_own_channel(
    authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
):
    bot = await _held_bot(
        authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
    )
    colleague = _colleague(fake_slack)

    context = await _prepare(bot.says(colleague))

    assert isinstance(context, SurfaceChatContext), context
    # Answered for the pod, from what is Public: as the contact, never a seat.
    assert context.audience.is_contact
    identity = await _contact_of(
        db_session, test_pod["id"], f"{bot.workspace.team}:{colleague}"
    )
    assert identity is not None
    assert identity.strength == IdentityStrength.CHANNEL.value
    assert identity.contact_id == context.audience.contact_id
    # Looked after by whoever connected the bot.
    assert str(context.user_id) == fixed_test_user["id"]
    channel = await _channel_of(db_session, context.source_id)
    assert channel is not None
    assert (channel.kind, channel.contact_id) == ("CONTACT", identity.contact_id)


@pytest.mark.parametrize(
    ("who", "answered"),
    [("member", True), ("guest", False), ("single-channel guest", False)],
)
async def test_only_a_full_member_of_the_workspace_is_answered_privately(
    authenticated_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_slack,
    who,
    answered,
):
    """A guest was let into a channel, not into the organization."""
    bot = await _held_bot(
        authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
    )
    sender = _colleague(
        fake_slack,
        is_restricted=who == "guest",
        is_ultra_restricted=who == "single-channel guest",
    )

    context = await _prepare(bot.says(sender))

    assert isinstance(context, SurfaceChatContext) is answered, context
    contact = await _contact_of(
        db_session, test_pod["id"], f"{bot.workspace.team}:{sender}"
    )
    assert (contact is not None) is answered


async def test_somebody_slack_will_not_describe_is_not_answered_privately(
    authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
):
    """No profile, no word that they are not a guest: fail closed."""
    bot = await _held_bot(
        authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
    )
    sender = _id("U")
    fake_slack.unknown_users.add(sender)

    context = await _prepare(bot.says(sender))

    assert not isinstance(context, SurfaceChatContext)
    assert (
        await _contact_of(db_session, test_pod["id"], f"{bot.workspace.team}:{sender}")
        is None
    )


async def test_a_colleagues_stop_is_answered_not_an_opt_out(
    authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
):
    """Nothing the pod sends reaches a Slack handle but an answer."""
    bot = await _held_bot(
        authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
    )
    colleague = _colleague(fake_slack)

    context = await _prepare(bot.says(colleague, "stop"))

    assert isinstance(context, SurfaceChatContext), context
    assert context.audience.is_contact
    suppressed = await db_session.scalar(
        select(func.count())
        .select_from(ContactSuppressionModel)
        .where(ContactSuppressionModel.pod_id == UUID(test_pod["id"]))
    )
    assert suppressed == 0


# -- in a channel --------------------------------------------------------------


async def test_a_guest_in_a_channel_that_welcomes_outsiders_is_answered_as_a_contact(
    authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
):
    """The group let them in; their later lines are theirs in its timeline."""
    bot = await _held_bot(
        authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
    )
    await _hand_over(authenticated_client, bot.workspace, bot.surface)
    await drain(_registry())
    guest = _colleague(fake_slack, is_restricted=True)
    handle = f"{bot.workspace.team}:{guest}"

    context = await _prepare(bot.says(guest, channel=bot.workspace.channel))

    assert isinstance(context, SurfaceChatContext), context
    assert context.audience.is_contact
    contact = await _contact_of(db_session, test_pod["id"], handle)
    assert contact is not None and contact.contact_id == context.audience.contact_id
    # Looked after by the group's owner: the person who handed it over.
    assert str(context.user_id) == fixed_test_user["id"]

    line = bot.says(guest, "thanks!", channel=bot.workspace.channel, mention=False)
    await _prepare(line)
    said = await db_session.scalar(
        select(ChannelMessageModel).where(
            ChannelMessageModel.external_message_id == line.payload["event"]["ts"]
        )
    )
    assert said is not None
    assert (said.sender_kind, said.sender_contact_id) == ("CONTACT", contact.contact_id)


async def test_a_colleague_in_a_channel_closed_to_outsiders_still_gets_the_nudge(
    authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
):
    """Not silence: what they were told before the workspace was held."""
    bot = await _held_bot(
        authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
    )
    await _hand_over(authenticated_client, bot.workspace, bot.surface)
    await drain(_registry())
    closed = await authenticated_client.patch(
        f"/pods/{test_pod['id']}/surfaces/{bot.surface['name']}",
        json={"config": {"groups": {"answers_outsiders": False}}},
    )
    assert closed.status_code == 200, closed.text
    colleague = _colleague(fake_slack)

    context = await _prepare(bot.says(colleague, channel=bot.workspace.channel))

    assert isinstance(context, SurfaceReplyContext), context
    assert context.reply_metadata.get("ephemeral_to") == colleague
    assert (
        await _contact_of(
            db_session, test_pod["id"], f"{bot.workspace.team}:{colleague}"
        )
        is None
    )


async def test_a_member_the_pod_has_not_recognised_yet_is_never_made_a_contact(
    authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
):
    """The group log runs before anybody resolves the sender."""
    bot = await _held_bot(
        authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
    )
    await _hand_over(authenticated_client, bot.workspace, bot.surface)
    await drain(_registry())
    # Slack's profile names the pod's own member; nothing has resolved them.
    member = _id("U")

    line = bot.says(member, "morning all", channel=bot.workspace.channel, mention=False)
    await _prepare(line)

    assert (
        await _contact_of(db_session, test_pod["id"], f"{bot.workspace.team}:{member}")
        is None
    )
    said = await db_session.scalar(
        select(ChannelMessageModel).where(
            ChannelMessageModel.external_message_id == line.payload["event"]["ts"]
        )
    )
    assert said is not None and said.sender_contact_id is None


# -- who a Slack user is ---------------------------------------------------------


@pytest.mark.parametrize("cache_cleared", [False, True])
async def test_a_person_slack_proved_routes_as_themselves_whatever_their_email(
    authenticated_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_slack,
    cache_cleared,
):
    """A profile phone change clears every cached platform identity of its
    person; the proof still says who they are."""
    bot = await _held_bot(
        authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
    )
    me = _colleague(fake_slack)  # Slack's email for them is not their Lemma one
    await _prove(bot.workspace.team, me, fixed_test_user["id"])
    if cache_cleared:
        async with async_session_maker() as session:
            uow = SqlAlchemyUnitOfWork(session)
            await ExternalSurfaceUserRepository(uow).clear_resolved_user(
                UUID(fixed_test_user["id"])
            )
            await uow.commit()

    context = await _prepare(bot.says(me))

    assert isinstance(context, SurfaceChatContext), context
    assert not context.audience.answers_outsiders
    assert str(context.user_id) == fixed_test_user["id"]
    assert (
        await _contact_of(db_session, test_pod["id"], f"{bot.workspace.team}:{me}")
        is None
    )


async def test_a_proven_persons_email_does_not_make_another_slack_user_them(
    authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
):
    """A workspace admin can set anybody's Slack email."""
    bot = await _held_bot(
        authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
    )
    me = _colleague(fake_slack)
    await _prove(bot.workspace.team, me, fixed_test_user["id"])
    # The fake's default profile carries the pod member's own email.
    impostor = _id("U")

    context = await _prepare(bot.says(impostor))

    assert isinstance(context, SurfaceChatContext), context
    assert context.audience.is_contact
    assert (
        await _contact_of(
            db_session, test_pod["id"], f"{bot.workspace.team}:{impostor}"
        )
        is not None
    )
    cached = await db_session.scalar(
        select(AgentSurfaceExternalUser.resolved_user_id).where(
            AgentSurfaceExternalUser.platform == "SLACK",
            AgentSurfaceExternalUser.tenant_id == bot.workspace.team,
            AgentSurfaceExternalUser.external_user_id == impostor,
        )
    )
    assert cached is None


# -- whose workspace it is -------------------------------------------------------


def _coordinator(db_session: AsyncSession) -> ChatOnboardingCoordinator:
    return ChatOnboardingCoordinator(
        SessionUnitOfWorkFactory(
            async_sessionmaker(db_session.bind, expire_on_commit=False)
        ),
        email_deliverable=lambda: True,
    )


async def _pending(db_session: AsyncSession) -> int:
    db_session.expire_all()
    return int(
        await db_session.scalar(select(func.count()).select_from(PendingChatOnboarding))
        or 0
    )


async def test_nobody_in_the_pods_organizations_workspace_is_asked_to_sign_up(
    authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
):
    bot = await _held_bot(
        authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
    )
    before = await _pending(db_session)

    result = await _coordinator(db_session).handle(bot.says(_colleague(fake_slack)))

    assert not result.handled
    assert await _pending(db_session) == before


async def _held_by_another_organization(
    client: AsyncClient,
    async_client: AsyncClient,
    db_session: AsyncSession,
    *,
    pod: dict,
    user: dict,
    fake_slack: Any,
) -> _Bot:
    """Another organization's Lemma app holds the workspace; this pod's own
    teammate bot is in it all the same."""
    workspace = _Workspace()
    other = await _sign_up_verified_user(async_client)
    other_org = UUID(str((await _create_org(async_client, other["token"]))["id"]))
    theirs = await _slack_account(
        db_session,
        user_id=other["id"],
        organization_id=other_org,
        config_source="SYSTEM_DEFAULT",
        raw={},
        fake_slack=fake_slack,
        token=f"xoxb-{workspace.manager_bot.lower()}",
    )
    async with async_session_maker() as session:
        await SlackManagerRepository(session).claim_install(
            team_id=workspace.team,
            team_name="Elsewhere",
            organization_id=other_org,
            account_id=theirs.id,
            bot_user_id=workspace.manager_bot,
            installed_by_user_id=UUID(other["id"]),
        )
        await session.commit()
    surface, bot = await _teammate(
        client,
        db_session,
        pod=pod,
        user=user,
        workspace=workspace,
        fake_slack=fake_slack,
    )
    return _Bot(workspace, surface, bot)


async def test_a_bot_in_a_workspace_another_organization_holds_is_unchanged(
    authenticated_client,
    async_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_slack,
):
    """No default, no contacts even once it answers anyone, and sign-up as before."""
    bot = await _held_by_another_organization(
        authenticated_client,
        async_client,
        db_session,
        pod=test_pod,
        user=fixed_test_user,
        fake_slack=fake_slack,
    )
    detail = await authenticated_client.get(
        f"/pods/{test_pod['id']}/surfaces/{bot.surface['name']}"
    )
    assert detail.json()["config"]["contacts"]["answer"] == "off"
    opened = await authenticated_client.patch(
        f"/pods/{test_pod['id']}/surfaces/{bot.surface['name']}",
        json={"config": {"contacts": {"answer": "anyone"}}},
    )
    assert opened.status_code == 200, opened.text
    colleague = _colleague(fake_slack)

    context = await _prepare(bot.says(colleague))
    assert not isinstance(context, SurfaceChatContext)
    assert (
        await _contact_of(
            db_session, test_pod["id"], f"{bot.workspace.team}:{colleague}"
        )
        is None
    )

    before = await _pending(db_session)
    result = await _coordinator(db_session).handle(bot.says(colleague))
    assert result.handled
    assert await _pending(db_session) == before + 1


async def test_a_new_bot_in_the_pods_organizations_workspace_answers_anyone(
    authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
):
    bot = await _held_bot(
        authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
    )

    detail = await authenticated_client.get(
        f"/pods/{test_pod['id']}/surfaces/{bot.surface['name']}"
    )

    contacts = detail.json()["config"]["contacts"]
    assert contacts["answer"] == "anyone"
    assert contacts["looked_after_by"] == fixed_test_user["id"]


@pytest.mark.parametrize(
    ("sent", "kept"),
    [({}, "anyone"), ({"contacts": {"answer": "off"}}, "off")],
    ids=["nothing-sent", "chose-off"],
)
async def test_a_contact_policy_sent_with_a_new_bot_is_kept_as_sent(
    authenticated_client, db_session, test_pod, fixed_test_user, fake_slack, sent, kept
):
    """ "Off" sent on purpose is a choice, not the default it looks like."""
    workspace = await _bound(
        authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
    )
    bot = _id("U")
    account = await _slack_account(
        db_session,
        user_id=fixed_test_user["id"],
        organization_id=UUID(test_pod["organization_id"]),
        config_source="ORG_CUSTOM",
        raw={"app_id": _id("A"), "bot_user_id": bot, "team": {"id": workspace.team}},
        fake_slack=fake_slack,
        token=f"xoxb-{bot.lower()}",
    )
    agent = await _create_agent(authenticated_client, test_pod["id"])

    created = await authenticated_client.post(
        f"/pods/{test_pod['id']}/surfaces",
        json={
            "platform": "SLACK",
            "account_id": str(account.id),
            "name": f"slack-{bot.lower()}",
            "default_agent_name": agent["name"],
            **({"config": sent} if sent else {}),
        },
    )

    assert created.status_code == 200, created.text
    assert created.json()["config"]["contacts"]["answer"] == kept


# -- forgetting ------------------------------------------------------------------


async def test_forgetting_a_slack_contact_forgets_the_profile_kept_for_them(
    authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
):
    """A colleague's, and a Slack Connect visitor's -- whose profile is kept
    under the pod's workspace, not the team their handle names."""
    bot = await _held_bot(
        authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
    )
    team = bot.workspace.team
    colleague = _colleague(fake_slack)
    answered = await _prepare(bot.says(colleague))
    assert isinstance(answered, SurfaceChatContext), answered
    visitor, elsewhere = _id("U"), _id("T")
    async with async_session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        cache = ExternalSurfaceUserRepository(uow)
        for tenant in (team, elsewhere):
            await cache.upsert(
                platform="SLACK",
                tenant_id=tenant,
                external_user_id=visitor,
                email=None,
                phone=None,
                display_name="Partner",
                raw_profile=None,
            )
        await open_contact(
            uow,
            pod_id=UUID(test_pod["id"]),
            kind=IdentityKind.SLACK,
            value=f"{_id('T')}:{visitor}",
            strength=IdentityStrength.CHANNEL,
            display_name="Partner",
        )
        await uow.commit()

    for user in (colleague, visitor):
        identity = await db_session.scalar(
            select(ContactIdentityModel).where(
                ContactIdentityModel.pod_id == UUID(test_pod["id"]),
                ContactIdentityModel.value.endswith(f":{user}"),
            )
        )
        assert identity is not None
        forgotten = await authenticated_client.delete(
            f"/pods/{test_pod['id']}/contacts/{identity.contact_id}"
        )
        assert forgotten.status_code in (200, 204), forgotten.text

    db_session.expire_all()
    kept = await db_session.execute(
        select(
            AgentSurfaceExternalUser.tenant_id,
            AgentSurfaceExternalUser.external_user_id,
        ).where(
            AgentSurfaceExternalUser.platform == "SLACK",
            AgentSurfaceExternalUser.external_user_id.in_([colleague, visitor]),
        )
    )
    # Only the profile another workspace keeps, which is not this pod's.
    assert kept.all() == [(elsewhere, visitor)]
