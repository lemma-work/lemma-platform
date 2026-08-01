from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceConversationLink,
    AgentSurfaceEntity,
    MemberReach,
    Notification,
    NotificationOrigin,
    ReachKind,
    ReachStatus,
    SurfaceConfig,
    SurfaceMode,
    SurfacePlatform,
    SurfaceTarget,
)
from app.modules.agent_surfaces.services.notification_service import (
    NotificationService,
)

POD = uuid4()
RECIPIENT = uuid4()
SOMEONE_ELSE = uuid4()
SURFACE_ID = uuid4()


def _target() -> SurfaceTarget:
    return SurfaceTarget(
        platform=SurfacePlatform.TELEGRAM,
        external_thread_id="thread-1",
        reply_target={"chat_id": 4242},
    )


def _chat_reach(**overrides) -> MemberReach:
    defaults = dict(
        pod_id=POD,
        user_id=RECIPIENT,
        kind=ReachKind.TELEGRAM,
        surface_id=SURFACE_ID,
        external_user_id="tg-1",
        target=_target(),
        last_inbound_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return MemberReach(**defaults)


def _app_reach() -> MemberReach:
    return MemberReach(pod_id=POD, user_id=RECIPIENT, kind=ReachKind.APP)


class FakeReachRepo:
    def __init__(self, reaches):
        self.reaches = reaches

    async def list_for_user(self, *, pod_id, user_id):
        return list(self.reaches)


class FakeNotificationRepo:
    def __init__(self):
        self.created: list[Notification] = []

    async def create(self, notification):
        self.created.append(notification)
        return notification


class FakeConversationRepo:
    def __init__(self, owners: dict[UUID, UUID] | None = None):
        self.owners = owners or {}
        self.messages: list[dict] = []

    async def get_conversation(self, conversation_id):
        owner = self.owners.get(conversation_id)
        return None if owner is None else SimpleNamespace(user_id=owner)

    async def append_message(self, *, conversation_id, agent_run_id, draft):
        self.messages.append({"conversation_id": conversation_id, "draft": draft})
        return draft


class FakeSurfaceRepo:
    def __init__(self, surface=None):
        self.surface = surface

    async def get(self, surface_id):
        return self.surface


class FakeLinkRepo:
    def __init__(self, link=None):
        self.link = link
        self.repointed: list[tuple] = []

    async def get_by_external_thread(self, **kwargs):
        return self.link

    async def repoint_conversation(
        self, *, link_id, conversation_id, expected_conversation_id
    ):
        self.repointed.append((link_id, conversation_id, expected_conversation_id))
        return True


class FakeConversationService:
    def __init__(self):
        self.created: list[dict] = []
        self.next_id = uuid4()

    async def create_conversation(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(id=self.next_id)


class FakeIngress:
    def __init__(self, *, succeeds=True):
        self.succeeds = succeeds
        self.sent: list[dict] = []

    async def send_agent_message_to_target(self, *, surface, target, message):
        self.sent.append({"target": target, "message": message})
        return self.succeeds


class FakeMembership:
    def __init__(self, pods):
        self.pods = pods

    async def get_user_pod_ids(self, user_id):
        return list(self.pods)


def _surface() -> AgentSurfaceEntity:
    return AgentSurfaceEntity(
        pod_id=POD,
        name="telegram",
        surface_type=SurfacePlatform.TELEGRAM,
        mode=SurfaceMode.DM,
        config=SurfaceConfig(),
        id=SURFACE_ID,
    )


def _build(
    *,
    reaches=None,
    conversation_owners=None,
    link=None,
    membership_pods=(POD,),
    membership=...,
    ingress_succeeds=True,
):
    notif_repo = FakeNotificationRepo()
    conv_repo = FakeConversationRepo(conversation_owners)
    link_repo = FakeLinkRepo(link)
    conv_service = FakeConversationService()
    ingress = FakeIngress(succeeds=ingress_succeeds)
    service = NotificationService(
        uow=SimpleNamespace(),
        reach_repository=FakeReachRepo(reaches or []),
        notification_repository=notif_repo,
        conversation_repository=conv_repo,
        surface_repository=FakeSurfaceRepo(_surface()),
        conversation_link_repository=link_repo,
        conversation_service=conv_service,
        ingress_service=ingress,
        pod_membership_port=(
            FakeMembership(membership_pods) if membership is ... else membership
        ),
    )
    return service, SimpleNamespace(
        notifications=notif_repo,
        conversations=conv_repo,
        links=link_repo,
        conversation_service=conv_service,
        ingress=ingress,
    )


# ``_open_conversation`` builds the recipient's auth context; that is a DB call
# and orthogonal to the routing decisions under test here.
def _no_auth_context():
    return patch(
        "app.modules.agent_surfaces.services.notification_service."
        "create_authorization_data_service"
    )


async def _notify(service, **kwargs):
    with _no_auth_context() as auth:
        auth.return_value.build_user_context = _AsyncNone()
        return await service.notify(
            pod_id=POD, recipient_user_id=RECIPIENT, body="standup in 10", **kwargs
        )


class _AsyncNone:
    async def __call__(self, *args, **kwargs):
        return None


@pytest.mark.asyncio
async def test_non_member_is_refused_and_nothing_is_written():
    service, fakes = _build(reaches=[_app_reach()], membership_pods=(uuid4(),))
    assert await _notify(service) is None
    assert fakes.notifications.created == []
    assert fakes.ingress.sent == []


@pytest.mark.asyncio
async def test_missing_membership_port_fails_closed():
    """A wiring bug must mean "reach nobody", never "reach anybody"."""
    service, fakes = _build(reaches=[_app_reach()], membership=None)
    assert await _notify(service) is None
    assert fakes.notifications.created == []


@pytest.mark.asyncio
async def test_app_only_recipient_still_gets_the_notification():
    service, fakes = _build(reaches=[_app_reach()])
    outcome = await _notify(service)
    assert outcome is not None
    assert outcome.delivered_via is ReachKind.APP
    assert not outcome.reached_a_chat_surface
    assert len(fakes.notifications.created) == 1
    # No chat surface was even attempted.
    assert fakes.ingress.sent == []


@pytest.mark.asyncio
async def test_chat_reach_receives_the_message_and_it_is_persisted():
    service, fakes = _build(reaches=[_chat_reach(), _app_reach()])
    outcome = await _notify(service)
    assert outcome.delivered_via is ReachKind.TELEGRAM
    assert outcome.reached_a_chat_surface
    assert fakes.ingress.sent[0]["message"] == "standup in 10"
    assert fakes.ingress.sent[0]["target"].reply_target == {"chat_id": 4242}
    # The outbound is in the conversation, so a bare "yes" later has a question
    # to attach to.
    assert len(fakes.conversations.messages) == 1
    draft = fakes.conversations.messages[0]["draft"]
    assert draft.kind.value == "NOTIFICATION"
    assert draft.role.value == "assistant"
    assert draft.text == "standup in 10"


@pytest.mark.asyncio
async def test_chat_delivery_failure_still_reaches_the_inbox():
    service, fakes = _build(reaches=[_chat_reach()], ingress_succeeds=False)
    outcome = await _notify(service)
    assert outcome.delivered_via is ReachKind.APP
    assert outcome.attempted == [ReachKind.TELEGRAM]
    assert len(fakes.notifications.created) == 1


@pytest.mark.asyncio
async def test_opted_out_and_stale_reaches_are_skipped():
    service, fakes = _build(
        reaches=[
            _chat_reach(opted_out_at=datetime.now(timezone.utc)),
            _chat_reach(kind=ReachKind.SLACK, status=ReachStatus.STALE),
            _app_reach(),
        ]
    )
    outcome = await _notify(service)
    assert outcome.delivered_via is ReachKind.APP
    assert fakes.ingress.sent == []


@pytest.mark.asyncio
async def test_expired_reply_window_is_skipped():
    service, fakes = _build(
        reaches=[
            _chat_reach(
                kind=ReachKind.WHATSAPP,
                window_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            ),
            _app_reach(),
        ]
    )
    outcome = await _notify(service)
    assert outcome.delivered_via is ReachKind.APP
    assert fakes.ingress.sent == []


@pytest.mark.asyncio
async def test_conversation_hint_is_used_when_the_recipient_owns_it():
    hinted = uuid4()
    service, fakes = _build(
        reaches=[_app_reach()], conversation_owners={hinted: RECIPIENT}
    )
    outcome = await _notify(service, conversation_id=hinted)
    assert outcome.conversation_id == hinted
    # Nothing new was opened — the run's own conversation was reused.
    assert fakes.conversation_service.created == []


@pytest.mark.asyncio
async def test_conversation_hint_owned_by_someone_else_is_rejected():
    """The authority boundary: an agent running as one person must not be able
    to drop a colleague into its own thread."""
    someone_elses = uuid4()
    service, fakes = _build(
        reaches=[_app_reach()], conversation_owners={someone_elses: SOMEONE_ELSE}
    )
    outcome = await _notify(service, conversation_id=someone_elses)
    assert outcome.conversation_id != someone_elses
    # A fresh conversation was opened, owned by the recipient.
    assert len(fakes.conversation_service.created) == 1
    assert fakes.conversation_service.created[0]["user_id"] == RECIPIENT


@pytest.mark.asyncio
async def test_a_live_thread_is_continued_rather_than_replaced():
    link = AgentSurfaceConversationLink(
        surface_id=SURFACE_ID,
        conversation_id=uuid4(),
        platform="TELEGRAM",
        external_thread_id="thread-1",
        updated_at=datetime.now(timezone.utc),
    )
    service, fakes = _build(reaches=[_chat_reach()], link=link)
    outcome = await _notify(service)
    assert outcome.conversation_id == link.conversation_id
    assert fakes.conversation_service.created == []


@pytest.mark.asyncio
async def test_a_cold_thread_opens_a_new_conversation_and_is_repointed():
    """A digest the morning after yesterday's support chat is a new subject."""
    link = AgentSurfaceConversationLink(
        surface_id=SURFACE_ID,
        conversation_id=uuid4(),
        platform="TELEGRAM",
        external_thread_id="thread-1",
        updated_at=datetime.now(timezone.utc) - timedelta(hours=6),
    )
    service, fakes = _build(reaches=[_chat_reach()], link=link)
    outcome = await _notify(service)
    assert outcome.conversation_id != link.conversation_id
    # The person's thread now points at the new conversation, so their reply
    # lands where the question is.
    assert fakes.links.repointed == [
        (link.id, outcome.conversation_id, link.conversation_id)
    ]


@pytest.mark.asyncio
async def test_attribution_is_prepended_to_what_the_person_sees():
    service, fakes = _build(reaches=[_chat_reach()])
    outcome = await _notify(service, attribution="Ops Assistant, for Deepak")
    assert fakes.ingress.sent[0]["message"].startswith("Ops Assistant, for Deepak")
    assert "standup in 10" in fakes.ingress.sent[0]["message"]
    # The stored body stays clean — attribution is presentation, not content.
    assert fakes.notifications.created[0].body == "standup in 10"
    assert outcome is not None


@pytest.mark.asyncio
async def test_origin_is_recorded_so_why_am_i_being_told_this_is_answerable():
    origin = uuid4()
    service, fakes = _build(reaches=[_app_reach()])
    await _notify(
        service, origin_type=NotificationOrigin.SCHEDULE_RUN, origin_id=origin
    )
    created = fakes.notifications.created[0]
    assert created.origin_type is NotificationOrigin.SCHEDULE_RUN
    assert created.origin_id == origin
    assert created.read_at is None and not created.is_read
