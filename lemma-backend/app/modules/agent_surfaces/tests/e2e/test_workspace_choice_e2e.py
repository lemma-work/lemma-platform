"""Someone the system recognises, with nowhere yet to talk.

A verified identity and nowhere for the message to go used to fall through to
ordinary ingestion, which answered by telling a person who already has an
account to go and open the website. These drive the step that replaced that:
the offer, the answer, and the two ways an answer can be wrong.

The premise is deliberately the **shared bot**. On a company installation,
having a transport *is* having a candidate -- the surface the message arrived
through is the one routing would pick -- so "recognised, nothing to route to"
is not a state that installation can reach for one of its own members. The
shared number can: it carries the message without belonging to any workspace,
so a recognised sender with no system-credential surface they can reach has
genuinely nowhere to go. The last test here pins the other half, that somebody
routing *can* place is never interrupted by the question.

The binding key is read back from the row the first message creates rather than
computed here. It is a hash of platform, tenant, installation and actor, and a
test that recomputed it would keep passing after the real one changed shape.
"""

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.onboarding_state import OnboardingStep
from app.modules.agent_surfaces.infrastructure.models import AgentSurface
from app.modules.agent_surfaces.infrastructure.onboarding_models import (
    PendingChatOnboarding,
    VerifiedSurfaceIdentity,
)
from app.modules.agent_surfaces.services.chat_onboarding import (
    ChatOnboardingCoordinator,
)
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _create_agent,
    _create_agent_surface,
    _ensure_connector_account,
    _load_slack_dm_fixture,
    _telegram_payload,
    _whatsapp_payload,
)
from app.modules.identity.infrastructure.models.user_models import User
from app.modules.identity.services.email_challenges import EmailChallengeService
from app.modules.identity.tests.e2e.test_email_challenges_e2e import allow_test_delivery

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def _swallow(*, email: str, code: str) -> bool:
    del email, code
    return True


def _coordinator(db_session):
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    return sessions, ChatOnboardingCoordinator(
        SessionUnitOfWorkFactory(sessions),
        challenges=EmailChallengeService(
            sessions, send_email=_swallow, enforce_send_limits=allow_test_delivery
        ),
    )


@pytest.fixture
async def recognised_sender(
    db_session, test_pod, fixed_test_user, fake_whatsapp, monkeypatch
):
    """A shared-bot sender the system knows, with a pod and nowhere to talk.

    `test_pod` is the point: they are not stranded for want of a workspace,
    they are stranded for want of a surface in one. Built by letting the first
    message open signup for real and then keeping only its binding key -- that
    key is the one production computes, so this cannot drift away from it the
    way a hand-rolled hash would.
    """
    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.whatsapp.service._WHATSAPP_API_BASE",
        f"{fake_whatsapp.api_base}/v21.0",
    )
    monkeypatch.setattr(surface_settings, "whatsapp_access_token", "wa-token")
    monkeypatch.setattr(surface_settings, "whatsapp_phone_number_id", "1234567890")
    monkeypatch.setattr(surface_settings, "whatsapp_waba_id", "waba-workspace-choice")

    sessions, coordinator = _coordinator(db_session)
    sender_phone = "1555" + str(uuid4().int)[:7]

    async def say(text: str):
        return await coordinator.handle(
            SurfacePlatformWebhookIngress(
                source="whatsapp",
                payload=_whatsapp_payload(
                    text=text,
                    message_id=uuid4().hex,
                    # Read back off the settings rather than repeated here: a
                    # real webhook is addressed to the number the deployment is
                    # configured with, and two copies of it can disagree.
                    phone_number_id=surface_settings.whatsapp_phone_number_id,
                    waba_id=surface_settings.whatsapp_waba_id,
                    sender_phone=sender_phone,
                ),
            )
        )

    await say("hello")
    async with sessions() as session:
        pending = await session.scalar(select(PendingChatOnboarding))
        assert pending is not None, "the first message did not open signup"
        binding_key = pending.binding_key
        await session.delete(pending)
        session.add(
            VerifiedSurfaceIdentity(
                binding_key=binding_key,
                platform="WHATSAPP",
                tenant_id=surface_settings.whatsapp_waba_id,
                external_user_id=sender_phone,
                user_id=fixed_test_user["id"],
            )
        )
        await session.commit()
    return say, sessions, binding_key


async def _shared_surfaces(sessions, pod_id) -> list[AgentSurface]:
    async with sessions() as session:
        rows = await session.scalars(
            select(AgentSurface).where(
                AgentSurface.pod_id == pod_id,
                AgentSurface.surface_type == "WHATSAPP",
            )
        )
        return list(rows)


async def test_a_recognised_sender_is_asked_which_workspace(recognised_sender) -> None:
    say, sessions, binding_key = recognised_sender

    result = await say("can you summarise my week")

    assert result.handled, "the message was left for ordinary ingestion"
    async with sessions() as session:
        parked = await session.scalar(
            select(PendingChatOnboarding).where(
                PendingChatOnboarding.binding_key == binding_key
            )
        )
        assert parked is not None
        assert parked.step == OnboardingStep.AWAITING_POD
        # The offer has to be recorded, because the answer is read against it.
        assert parked.offered_pods, "nothing was offered, so no reply can be read"
        # And the message that got them here is kept, or answering costs them it.
        assert parked.original_event is not None


async def test_choosing_a_workspace_wires_the_conversation_to_it(
    recognised_sender,
) -> None:
    say, sessions, binding_key = recognised_sender
    await say("can you summarise my week")
    async with sessions() as session:
        parked = await session.scalar(
            select(PendingChatOnboarding).where(
                PendingChatOnboarding.binding_key == binding_key
            )
        )
        offered = list(parked.offered_pods)

    await say("1")

    chosen_pod_id = offered[0]["id"]
    surfaces = await _shared_surfaces(sessions, chosen_pod_id)
    assert len(surfaces) == 1, "choosing a workspace left nowhere to talk"
    assert surfaces[0].status == "ACTIVE"
    # System credentials, not the pod's own: shared routing considers only
    # those, so a surface on anything else is a destination nothing reaches.
    assert surfaces[0].credential_mode == "SYSTEM"
    async with sessions() as session:
        done = await session.scalar(
            select(PendingChatOnboarding).where(
                PendingChatOnboarding.binding_key == binding_key
            )
        )
        assert done.step == OnboardingStep.READY
        assert done.ready_at is not None, "the original message will never replay"


async def test_an_unreadable_answer_asks_again_rather_than_guessing(
    recognised_sender,
) -> None:
    """The one outcome worth more than convenience: never guess a workspace."""
    say, sessions, binding_key = recognised_sender
    await say("can you summarise my week")
    async with sessions() as session:
        parked = await session.scalar(
            select(PendingChatOnboarding).where(
                PendingChatOnboarding.binding_key == binding_key
            )
        )
        offered = list(parked.offered_pods)

    await say("whatever you think is best")

    async with sessions() as session:
        still_asking = await session.scalar(
            select(PendingChatOnboarding).where(
                PendingChatOnboarding.binding_key == binding_key
            )
        )
        assert still_asking.step == OnboardingStep.AWAITING_POD
    assert not await _shared_surfaces(sessions, offered[0]["id"]), (
        "an unreadable answer attached a workspace anyway"
    )


async def test_a_number_nobody_was_offered_attaches_nothing(
    recognised_sender,
) -> None:
    say, sessions, binding_key = recognised_sender
    await say("can you summarise my week")
    async with sessions() as session:
        parked = await session.scalar(
            select(PendingChatOnboarding).where(
                PendingChatOnboarding.binding_key == binding_key
            )
        )
        offered = list(parked.offered_pods)

    await say("97")

    async with sessions() as session:
        still_asking = await session.scalar(
            select(PendingChatOnboarding).where(
                PendingChatOnboarding.binding_key == binding_key
            )
        )
        assert still_asking.step == OnboardingStep.AWAITING_POD
    assert not await _shared_surfaces(sessions, offered[0]["id"])


async def test_a_sender_routing_can_already_place_is_not_interrupted(
    authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
) -> None:
    """The other half, and the more dangerous one to get wrong.

    This question is asked before ordinary ingestion, so asking it of somebody
    ingestion could have routed parks a working conversation behind a prompt
    they never needed to answer. The check is the routing resolver's own, so
    what ingestion would do and what this predicts cannot drift apart.
    """
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="slack",
        credentials={
            "access_token": "xoxb-workspace-choice",
            "api_base_url": fake_slack.base_url,
            "raw_response": {
                "team_id": "T0123456",
                "bot_user_id": "U0AGSSTQZLH",
                "api_base_url": fake_slack.base_url,
            },
        },
    )
    await _create_agent_surface(
        authenticated_client,
        test_pod["id"],
        config={"type": "SLACK", "account_id": str(account.id)},
    )
    sessions, coordinator = _coordinator(db_session)
    actor = "U" + uuid4().hex[:10]

    async def say(text: str):
        payload = _load_slack_dm_fixture(text=text, ts=uuid4().hex)
        payload["event"].update(
            {"user": actor, "channel": f"D{actor}", "channel_type": "im"}
        )
        return await coordinator.handle(
            SurfacePlatformWebhookIngress(source="slack", payload=payload)
        )

    await say("hello")
    async with sessions() as session:
        pending = await session.scalar(select(PendingChatOnboarding))
        assert pending is not None
        binding_key = pending.binding_key
        await session.delete(pending)
        session.add(
            VerifiedSurfaceIdentity(
                binding_key=binding_key,
                platform="SLACK",
                tenant_id="T0123456",
                external_user_id=actor,
                user_id=fixed_test_user["id"],
            )
        )
        await session.commit()

    result = await say("can you summarise my week")

    assert not result.handled, "a routable sender was parked on a question"
    async with sessions() as session:
        parked = await session.scalar(
            select(PendingChatOnboarding).where(
                PendingChatOnboarding.binding_key == binding_key
            )
        )
        assert parked is None, "a routable sender was asked which workspace"


async def _pod_member_ids(sessions, pod_id) -> list:
    from app.modules.pod.infrastructure.models import PodMember

    async with sessions() as session:
        rows = await session.scalars(
            select(PodMember).where(PodMember.pod_id == pod_id)
        )
        return list(rows)


async def _stranger(sessions, *, email: str) -> User:
    """A verified account with nothing attached to it.

    Made here rather than through signup because what these tests need is the
    absence of everything signup would give them -- no organization, no pod, no
    surface -- and the shortest way to have none of it is never to have had it.
    """
    async with sessions() as session:
        user = User(email=email, is_verified=True, is_active=True)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def test_a_thread_on_a_pod_you_left_is_not_somewhere_to_talk(
    authenticated_client, db_session, test_pod, fixed_test_user
) -> None:
    """Routing answers this one with a surface the sender cannot use.

    It is the continuity fallback, and it exists so ordinary ingestion has
    somewhere to send the access-denied reply. Asking routing's selection here
    read that as "they have somewhere to talk" and withheld the workspace
    choice from the one person who most needs it: somebody who just lost access
    to the only pod they were in, whose thread is still sitting on its surface.
    """
    from app.modules.agent.infrastructure.models.conversation import ConversationModel
    from app.modules.agent_surfaces.infrastructure.models import (
        AgentSurfaceConversationLinkModel,
    )
    from app.modules.agent_surfaces.services.onboarding_pod_choice import (
        has_somewhere_to_talk,
    )
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork

    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    stranger = await _stranger(sessions, email=f"left+{uuid4().hex[:8]}@example.com")
    async with sessions() as session:
        surface = AgentSurface(
            pod_id=UUID(test_pod["id"]),
            agent_id=UUID(test_pod["id"]),
            name=f"whatsapp-{uuid4().hex[:6]}",
            surface_type="WHATSAPP",
            mode="DM",
            event_mode="WEBHOOK",
            credential_mode="SYSTEM",
            config={},
        )
        session.add(surface)
        await session.flush()
        conversation = ConversationModel(
            user_id=fixed_test_user["id"], pod_id=UUID(test_pod["id"])
        )
        session.add(conversation)
        await session.flush()
        # The thread they were talking in, still pointing at that surface.
        session.add(
            AgentSurfaceConversationLinkModel(
                surface_id=surface.id,
                conversation_id=conversation.id,
                platform="WHATSAPP",
                external_channel_id="15550001111",
                external_thread_id="15550001111",
                external_user_id="15550001111",
                last_inbound_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()

    event = ParsedInboundSurfaceEvent(
        platform=SurfacePlatform.WHATSAPP,
        conversation_type=ConversationType.EXTERNAL_DM,
        external_channel_id="15550001111",
        external_thread_id="15550001111",
        sender_external_user_id="15550001111",
        external_message_id=uuid4().hex,
        message_text="can you summarise my week",
        is_dm=True,
    )
    async with sessions() as session:
        uow = SqlAlchemyUnitOfWork(session)
        assert (
            await has_somewhere_to_talk(
                uow,
                user_id=stranger.id,
                platform=SurfacePlatform.WHATSAPP,
                parsed=event,
                system_credentials_only=True,
                receiver_surface_ids=None,
            )
            is False
        ), "a surface in a pod they are not in counted as somewhere to talk"
        # And the member of that pod is not interrupted, which is the other half.
        assert (
            await has_somewhere_to_talk(
                uow,
                user_id=UUID(str(fixed_test_user["id"])),
                platform=SurfacePlatform.WHATSAPP,
                parsed=event,
                system_credentials_only=True,
                receiver_surface_ids=None,
            )
            is True
        )


async def test_a_surface_in_another_slack_workspace_is_not_somewhere_to_talk(
    authenticated_client, db_session, test_pod, fixed_test_user
) -> None:
    """Pod membership is not the whole authorization; the tenant is the rest.

    Ordinary ingestion drops a surface whose Slack workspace is not the one the
    event arrived from. The onboarding check did not, so a person whose only
    Slack surface belongs to a different workspace was read as reachable and
    never offered a choice -- while the message they sent went nowhere.
    """
    from app.modules.agent_surfaces.services.onboarding_pod_choice import (
        has_somewhere_to_talk,
    )
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork

    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    async with sessions() as session:
        session.add(
            AgentSurface(
                pod_id=UUID(test_pod["id"]),
                agent_id=UUID(test_pod["id"]),
                name=f"slack-{uuid4().hex[:6]}",
                surface_type="SLACK",
                mode="DM",
                event_mode="WEBHOOK",
                credential_mode="CUSTOM",
                external_workspace_id="T-SOMEWHERE-ELSE",
                config={},
            )
        )
        await session.commit()

    event = ParsedInboundSurfaceEvent(
        platform=SurfacePlatform.SLACK,
        conversation_type=ConversationType.EXTERNAL_DM,
        external_channel_id="D1",
        external_thread_id="D1",
        sender_external_user_id="U1",
        external_message_id=uuid4().hex,
        tenant_id="T-THIS-ONE",
        message_text="hello",
        is_dm=True,
    )
    async with sessions() as session:
        uow = SqlAlchemyUnitOfWork(session)
        assert (
            await has_somewhere_to_talk(
                uow,
                user_id=UUID(str(fixed_test_user["id"])),
                platform=SurfacePlatform.SLACK,
                parsed=event,
                system_credentials_only=False,
                receiver_surface_ids=None,
            )
            is False
        ), "a surface in another Slack workspace counted as somewhere to talk"


async def test_a_workspace_that_cannot_carry_the_bot_asks_for_another(
    authenticated_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_whatsapp,
    monkeypatch,
) -> None:
    """ "Pick another workspace" was an instruction nothing was listening for.

    The refusal left the signup on VERIFIED, so the next message -- any message
    -- re-ran provisioning against the same pod and was refused again in the
    same words. A person whose only workspace already had its own WhatsApp bot
    could not get past this sentence.
    """
    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.whatsapp.service._WHATSAPP_API_BASE",
        f"{fake_whatsapp.api_base}/v21.0",
    )
    monkeypatch.setattr(surface_settings, "whatsapp_access_token", "wa-token")
    monkeypatch.setattr(surface_settings, "whatsapp_phone_number_id", "1234567890")
    monkeypatch.setattr(surface_settings, "whatsapp_waba_id", "waba-conflict")

    # The pod's own assistant already reaches WhatsApp on a connection of its
    # own, so there is no second place to put the shared number.
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    async with sessions() as session:
        session.add(
            AgentSurface(
                pod_id=UUID(test_pod["id"]),
                agent_id=UUID(test_pod["id"]),
                name=f"whatsapp-own-{uuid4().hex[:6]}",
                surface_type="WHATSAPP",
                mode="DM",
                event_mode="WEBHOOK",
                credential_mode="CUSTOM",
                config={},
            )
        )
        await session.commit()
    sibling = await authenticated_client.post(
        "/pods",
        json={
            "organization_id": test_pod["organization_id"],
            "name": f"Somewhere else {uuid4().hex[:6]}",
        },
    )
    assert sibling.status_code == 201, sibling.text

    _, coordinator = _coordinator(db_session)
    sender_phone = "1555" + str(uuid4().int)[:7]

    async def say(text: str):
        return await coordinator.handle(
            SurfacePlatformWebhookIngress(
                source="whatsapp",
                payload=_whatsapp_payload(
                    text=text,
                    message_id=uuid4().hex,
                    phone_number_id=surface_settings.whatsapp_phone_number_id,
                    waba_id=surface_settings.whatsapp_waba_id,
                    sender_phone=sender_phone,
                ),
            )
        )

    await say("hello")
    async with sessions() as session:
        row = await session.scalar(select(PendingChatOnboarding))
        assert row is not None
        binding_key = row.binding_key
        # Straight to the state the email code leaves behind. What is under test
        # is what happens *from* VERIFIED, not how it was reached.
        row.step = OnboardingStep.VERIFIED
        row.user_id = fixed_test_user["id"]
        row.destination = row.original_event
        await session.commit()

    await say("can you summarise my week")

    async with sessions() as session:
        parked = await session.scalar(
            select(PendingChatOnboarding).where(
                PendingChatOnboarding.binding_key == binding_key
            )
        )
        assert parked.step == OnboardingStep.AWAITING_POD, (
            "the refusal repeated instead of asking"
        )
        offered = [str(pod["id"]) for pod in (parked.offered_pods or [])]
    assert offered, "nothing was offered, so no reply can be read"
    assert test_pod["id"] not in offered, "the workspace that just refused was offered"
    assert sibling.json()["id"] in offered

    # And the answer now lands: they are unstuck, which is the whole point.
    await say("1")
    surfaces = await _shared_surfaces(sessions, offered[0])
    assert len(surfaces) == 1
    async with sessions() as session:
        done = await session.scalar(
            select(PendingChatOnboarding).where(
                PendingChatOnboarding.binding_key == binding_key
            )
        )
        assert done.step == OnboardingStep.READY
        # And the proof of who they are survived the refusal. It was written in
        # the same unit of work the conflicting surface rolled back, so the next
        # message found no binding and asked for another email code.
        identity = await session.scalar(
            select(VerifiedSurfaceIdentity).where(
                VerifiedSurfaceIdentity.binding_key == binding_key
            )
        )
        assert identity is not None, "recovering from the conflict lost the binding"
        assert identity.revoked_at is None


async def test_a_sender_with_no_organization_can_still_name_a_workspace(
    db_session, fake_whatsapp, monkeypatch
) -> None:
    """There was no administrator to ask.

    Naming a new workspace needed an organization to put it in, and someone
    arriving on the shared number with a personal address has none -- so a
    verified person was told to ask an admin who does not exist. A company
    installation still fixes the organization; the shared bot has no boundary to
    hold, and this is the policy the web signup would have run a minute earlier.
    """
    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.whatsapp.service._WHATSAPP_API_BASE",
        f"{fake_whatsapp.api_base}/v21.0",
    )
    monkeypatch.setattr(surface_settings, "whatsapp_access_token", "wa-token")
    monkeypatch.setattr(surface_settings, "whatsapp_phone_number_id", "1234567890")
    monkeypatch.setattr(surface_settings, "whatsapp_waba_id", "waba-no-org")

    sessions, coordinator = _coordinator(db_session)
    stranger = await _stranger(sessions, email=f"solo+{uuid4().hex[:8]}@example.com")
    sender_phone = "1555" + str(uuid4().int)[:7]

    async def say(text: str):
        return await coordinator.handle(
            SurfacePlatformWebhookIngress(
                source="whatsapp",
                payload=_whatsapp_payload(
                    text=text,
                    message_id=uuid4().hex,
                    phone_number_id=surface_settings.whatsapp_phone_number_id,
                    waba_id=surface_settings.whatsapp_waba_id,
                    sender_phone=sender_phone,
                ),
            )
        )

    await say("hello")
    async with sessions() as session:
        pending = await session.scalar(select(PendingChatOnboarding))
        assert pending is not None
        binding_key = pending.binding_key
        await session.delete(pending)
        session.add(
            VerifiedSurfaceIdentity(
                binding_key=binding_key,
                platform="WHATSAPP",
                tenant_id=surface_settings.whatsapp_waba_id,
                external_user_id=sender_phone,
                user_id=stranger.id,
            )
        )
        await session.commit()

    await say("can you summarise my week")
    async with sessions() as session:
        asked = await session.scalar(
            select(PendingChatOnboarding).where(
                PendingChatOnboarding.binding_key == binding_key
            )
        )
        assert asked.step == OnboardingStep.AWAITING_POD
        # Nothing to list: they have no workspace at all, which is the case.
        assert not asked.offered_pods

    await say("new Personal")

    async with sessions() as session:
        done = await session.scalar(
            select(PendingChatOnboarding).where(
                PendingChatOnboarding.binding_key == binding_key
            )
        )
        assert done.step == OnboardingStep.READY, (
            "naming a workspace was refused for want of an organization"
        )
        made = await session.scalars(
            select(AgentSurface).where(
                AgentSurface.surface_type == "WHATSAPP",
                AgentSurface.credential_mode == "SYSTEM",
            )
        )
        pods = {surface.pod_id for surface in made}
    assert pods, "no workspace was created for them to talk in"


async def test_another_bot_in_the_same_workspace_is_not_somewhere_to_talk(
    authenticated_client, db_session, test_pod, fixed_test_user
) -> None:
    """The tenant says which workspace; it does not say which bot is listening.

    Two installations can share one Slack workspace, so a surface can pass the
    tenant filter and still belong to an app that will never see this message.
    Ingestion narrows to the surfaces the delivering bot serves; this check did
    not, so access through the other company's bot suppressed the question for a
    message only theirs could have answered.
    """
    from app.modules.agent_surfaces.services.onboarding_pod_choice import (
        has_somewhere_to_talk,
    )
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork

    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    workspace_id = f"T-SHARED-{uuid4().hex[:6]}"
    # A second agent, because one agent reaches a platform in one place -- which
    # is exactly why two installations in a workspace are two agents' surfaces.
    second_agent = await _create_agent(authenticated_client, test_pod["id"])
    async with sessions() as session:
        listening = AgentSurface(
            pod_id=UUID(test_pod["id"]),
            agent_id=UUID(test_pod["id"]),
            name=f"slack-a-{uuid4().hex[:6]}",
            surface_type="SLACK",
            mode="DM",
            event_mode="WEBHOOK",
            credential_mode="CUSTOM",
            external_workspace_id=workspace_id,
            config={},
        )
        # Same Slack workspace, same pod, a different installation: the tenant
        # filter cannot tell these two apart, and only one took delivery.
        other_bot = AgentSurface(
            pod_id=UUID(test_pod["id"]),
            agent_id=UUID(second_agent["id"]),
            name=f"slack-b-{uuid4().hex[:6]}",
            surface_type="SLACK",
            mode="DM",
            event_mode="WEBHOOK",
            credential_mode="CUSTOM",
            external_workspace_id=workspace_id,
            config={},
        )
        session.add_all([listening, other_bot])
        await session.commit()
        listening_id, other_id = listening.id, other_bot.id

    event = ParsedInboundSurfaceEvent(
        platform=SurfacePlatform.SLACK,
        conversation_type=ConversationType.EXTERNAL_DM,
        external_channel_id="D1",
        external_thread_id="D1",
        sender_external_user_id="U1",
        external_message_id=uuid4().hex,
        tenant_id=workspace_id,
        message_text="hello",
        is_dm=True,
    )

    async def reachable(receiver_surface_ids):
        async with sessions() as session:
            return await has_somewhere_to_talk(
                SqlAlchemyUnitOfWork(session),
                user_id=UUID(str(fixed_test_user["id"])),
                platform=SurfacePlatform.SLACK,
                parsed=event,
                system_credentials_only=False,
                receiver_surface_ids=receiver_surface_ids,
            )

    # Scoped to the bot that took delivery, both ways round.
    assert await reachable([listening_id]) is True
    assert await reachable([other_id]) is True
    # And a receiver serving neither has nowhere to put this message, however
    # much of the workspace the sender can otherwise reach.
    assert await reachable([]) is False


async def test_a_shared_bot_webhook_reads_no_surface_list_to_find_its_transport(
    authenticated_client, db_session, test_pod, monkeypatch
) -> None:
    """The shared bot's transport is its system credentials, and nothing else.

    `_shared_transport` consults the candidate list only to refuse a
    receiver-scoped delivery. A platform-wide webhook is not one, so the list
    was read -- every system surface of the platform in the deployment -- and
    then not used, on every message a shared-bot sender ever sends.
    """
    from app.modules.agent_surfaces.services.onboarding_transport import (
        _transport_candidates,
    )
    from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory

    monkeypatch.setattr(surface_settings, "whatsapp_access_token", "wa-token")
    monkeypatch.setattr(surface_settings, "whatsapp_phone_number_id", "1234567890")
    monkeypatch.setattr(surface_settings, "whatsapp_waba_id", "waba-transport")

    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    async with sessions() as session:
        # A live system surface exists, so an unscoped read would find one.
        session.add(
            AgentSurface(
                pod_id=UUID(test_pod["id"]),
                agent_id=UUID(test_pod["id"]),
                name=f"whatsapp-{uuid4().hex[:6]}",
                surface_type="WHATSAPP",
                mode="DM",
                event_mode="WEBHOOK",
                credential_mode="SYSTEM",
                config={},
            )
        )
        await session.commit()

    request = SurfacePlatformWebhookIngress(
        source="whatsapp",
        payload=_whatsapp_payload(
            text="hello",
            message_id=uuid4().hex,
            phone_number_id="1234567890",
            waba_id="waba-transport",
            sender_phone="15559990000",
        ),
    )
    loaded = await _transport_candidates(request, SessionUnitOfWorkFactory(sessions))
    assert loaded is not None
    platform, surfaces = loaded
    assert platform is SurfacePlatform.WHATSAPP
    assert surfaces == [], "the shared webhook read a surface list it cannot use"


async def test_changing_workspace_keeps_a_telegram_senders_phone_proof(
    db_session, test_pod, fixed_test_user, fake_telegram, monkeypatch
) -> None:
    """The message after the choice has to land in chat, not back at signup.

    A pending row carries a phone when a challenge just supplied one. A
    returning person changing workspaces supplies none -- there was nothing to
    verify -- and writing that nothing over their live proof is invisible to
    onboarding, which recognises them by binding key alone. Ordinary ingestion
    does not: for WhatsApp and Telegram `resolve_shared_verified_identity` requires
    the stored phone and a match. So the choice reached READY and the next
    message was answered with "please share your phone number".
    """
    from app.modules.agent_surfaces.services.verified_surface_identity import (
        resolve_shared_verified_identity,
    )
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork

    monkeypatch.setattr(surface_settings, "telegram_bot_token", "native-telegram")
    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.telegram.client._TELEGRAM_API_BASE",
        f"{fake_telegram.api_base}/bot",
    )
    sessions, coordinator = _coordinator(db_session)
    actor = int(uuid4().hex[:8], 16)
    phone = "+15550" + str(int(uuid4().hex[:7], 16)).zfill(9)[:9]

    async with sessions() as session:
        user = await session.get(User, fixed_test_user["id"])
        user.mobile_number = phone
        user.mobile_verified_at = datetime.now(timezone.utc)
        await session.commit()

    async def say(text: str):
        return await coordinator.handle(
            SurfacePlatformWebhookIngress(
                source="telegram",
                payload=_telegram_payload(
                    text=text, message_id=int(uuid4().hex[:7], 16), sender_id=actor
                ),
            )
        )

    await say("hello")
    async with sessions() as session:
        pending = await session.scalar(select(PendingChatOnboarding))
        assert pending is not None
        binding_key = pending.binding_key
        await session.delete(pending)
        # A binding that already holds a proven number, which is what a
        # returning Telegram sender has.
        session.add(
            VerifiedSurfaceIdentity(
                binding_key=binding_key,
                platform="TELEGRAM",
                tenant_id="",
                external_user_id=str(actor),
                user_id=fixed_test_user["id"],
                verified_phone=phone,
            )
        )
        await session.commit()

    await say("can you summarise my week")
    async with sessions() as session:
        asked = await session.scalar(
            select(PendingChatOnboarding).where(
                PendingChatOnboarding.binding_key == binding_key
            )
        )
        assert asked.step == OnboardingStep.AWAITING_POD

    await say("new Another")

    async with sessions() as session:
        done = await session.scalar(
            select(PendingChatOnboarding).where(
                PendingChatOnboarding.binding_key == binding_key
            )
        )
        assert done.step == OnboardingStep.READY
        identity = await session.scalar(
            select(VerifiedSurfaceIdentity).where(
                VerifiedSurfaceIdentity.binding_key == binding_key
            )
        )
        assert identity.verified_phone == phone, "the workspace choice erased the proof"

    # And the question that actually matters: does the next message reach chat?
    event = ParsedInboundSurfaceEvent(
        platform=SurfacePlatform.TELEGRAM,
        conversation_type=ConversationType.EXTERNAL_DM,
        external_channel_id=str(actor),
        external_thread_id=str(actor),
        sender_external_user_id=str(actor),
        external_message_id=uuid4().hex,
        message_text="and now the week",
        is_dm=True,
    )
    async with sessions() as session:
        resolved = await resolve_shared_verified_identity(
            SqlAlchemyUnitOfWork(session), event=event, installation_id=None
        )
    assert resolved is not None
    assert resolved.internal_user_id == UUID(str(fixed_test_user["id"])), (
        "ingestion no longer recognises them, so the next message restarts signup"
    )
