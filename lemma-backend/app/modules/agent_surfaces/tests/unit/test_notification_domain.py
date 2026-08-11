"""The notification lifecycle, and the rules that decide where a message goes.

A notification owns the ask from creation until it resolves, and it resolves
exactly once. These are the tests for that claim — every illegal move, not just
the happy path, because the illegal moves are what a second device, a raced
sweep, or a re-delivered worker job will actually attempt.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceConversationLink,
    AgentSurfaceEntity,
    SurfaceConfig,
    SurfacePlatform,
    SurfaceSendPolicy,
)
from app.modules.agent_surfaces.domain.errors import (
    AgentSurfaceValidationError,
    NotificationTransitionError,
)
from app.modules.agent_surfaces.domain.notification import (
    NotificationDeliveryStatus,
    NotificationEntity,
    NotificationOriginKind,
    NotificationStatus,
)
from app.modules.agent_surfaces.services.ingress_service import (
    AgentSurfaceIngressService,
)
from app.modules.agent_surfaces.services.notification_delivery import (
    DeliveryChannel,
    rank_candidates,
    reply_window_open,
)
from app.modules.agent_surfaces.services.notification_service import attribute


def _notification(**overrides) -> NotificationEntity:
    payload = {
        "pod_id": uuid4(),
        "recipient_user_id": uuid4(),
        "recipient_pod_member_id": uuid4(),
        "origin_kind": NotificationOriginKind.AGENT_RUN,
        "title": "Standup",
        "body": "What did you ship yesterday?",
    }
    payload.update(overrides)
    return NotificationEntity(**payload)


def _surface(platform: SurfacePlatform, surface_id=None) -> AgentSurfaceEntity:
    return AgentSurfaceEntity(
        id=surface_id or uuid4(),
        pod_id=uuid4(),
        name=platform.value.lower(),
        surface_type=platform,
        config=SurfaceConfig(),
    )


def _link(last_inbound_at: datetime | None, updated_at: datetime | None = None):
    now = datetime.now(timezone.utc)
    return AgentSurfaceConversationLink(
        surface_id=uuid4(),
        conversation_id=uuid4(),
        platform="TELEGRAM",
        external_thread_id="t1",
        external_user_id="u1",
        updated_at=updated_at or now,
        last_inbound_at=last_inbound_at,
    )


# --------------------------------------------------------------- the lifecycle


def test_respond_records_the_answer_and_marks_read():
    notification = _notification()
    notification.respond(summary="Shipped the importer", data={"pr": 42})

    assert notification.status is NotificationStatus.RESPONDED
    assert notification.response_summary == "Shipped the importer"
    assert notification.response_data == {"pr": 42}
    assert notification.responded_at is not None
    # Answering implies having seen it; a badge that survives an answer is noise.
    assert notification.read_at is not None


def test_a_notification_resolves_exactly_once():
    """Two devices, or a retried worker job, must not overwrite an answer."""
    notification = _notification()
    notification.respond(summary="first")

    with pytest.raises(NotificationTransitionError) as excinfo:
        notification.respond(summary="second")

    assert excinfo.value.details["status"] == "RESPONDED"
    assert notification.response_summary == "first"


@pytest.mark.parametrize(
    "close, verb",
    [
        (lambda n: n.cancel(), "respond"),
        (lambda n: n.expire(), "respond"),
    ],
)
def test_a_closed_notification_cannot_be_answered(close, verb):
    notification = _notification()
    close(notification)
    with pytest.raises(NotificationTransitionError):
        notification.respond(summary="too late")


def test_acknowledge_is_refused_while_a_response_is_owed():
    """Dismissing a question is not answering it."""
    notification = _notification(expects_response=True)
    with pytest.raises(NotificationTransitionError):
        notification.acknowledge()


def test_respond_is_refused_when_nothing_was_asked():
    notification = _notification(expects_response=False)
    with pytest.raises(NotificationTransitionError):
        notification.respond(summary="unsolicited")
    notification.acknowledge()
    assert notification.status is NotificationStatus.ACKNOWLEDGED


def test_a_form_notification_refuses_free_text_but_accepts_its_action():
    """A form has one answer path, and it is the one the node schema validates."""
    notification = _notification(
        origin_kind=NotificationOriginKind.WORKFLOW_FORM,
        action={"type": "WORKFLOW_FORM", "run_id": str(uuid4()), "node_id": "approve"},
    )
    assert notification.responds_through_action is True

    with pytest.raises(NotificationTransitionError):
        notification.respond(summary="looks fine to me")

    notification.resolve_through_action(summary="Submitted", data={"approved": True})
    assert notification.status is NotificationStatus.RESPONDED


def test_a_form_notification_without_its_action_is_rejected_at_construction():
    """Otherwise it can never leave OPEN: respond refuses it and nothing else can."""
    with pytest.raises(AgentSurfaceValidationError):
        _notification(origin_kind=NotificationOriginKind.WORKFLOW_FORM)


def test_awaiting_response_is_what_draws_the_respond_button():
    assert _notification().awaiting_response is True
    assert _notification(expects_response=False).awaiting_response is False

    answered = _notification()
    answered.respond(summary="done")
    assert answered.awaiting_response is False


def test_delivery_status_is_independent_of_the_human_lifecycle():
    """UNDELIVERABLE and RESPONDED together is a real state: seen in the app."""
    notification = _notification()
    notification.mark_undeliverable("nobody home")
    notification.respond(summary="saw it in Lemma")

    assert notification.delivery_status is NotificationDeliveryStatus.UNDELIVERABLE
    assert notification.status is NotificationStatus.RESPONDED


def test_is_past_due_only_applies_to_open_notifications():
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    assert _notification(expires_at=past).is_past_due() is True
    assert _notification().is_past_due() is False

    answered = _notification(expires_at=past)
    answered.respond(summary="in time")
    assert answered.is_past_due() is False


# ------------------------------------------------------------------ attribution


def test_attribution_names_both_the_agent_and_the_human_behind_it():
    """The recipient sees the pod's bot; without this they cannot tell who asked."""
    rendered = attribute(
        "Send me your update", agent_name="Ops Assistant", actor_display_name="Anukul"
    )
    assert "Ops Assistant" in rendered
    assert "Anukul" in rendered
    assert rendered.endswith("Send me your update")


# ------------------------------------------------------------- the reply window


def test_whatsapp_reply_window_closes_after_24h():
    now = datetime.now(timezone.utc)
    assert reply_window_open(
        platform=SurfacePlatform.WHATSAPP,
        last_inbound_at=now - timedelta(hours=23),
        now=now,
    )
    assert not reply_window_open(
        platform=SurfacePlatform.WHATSAPP,
        last_inbound_at=now - timedelta(hours=25),
        now=now,
    )


def test_whatsapp_with_no_recorded_inbound_is_treated_as_closed():
    """The window is measured from their last message. We have no evidence of one."""
    assert not reply_window_open(
        platform=SurfacePlatform.WHATSAPP, last_inbound_at=None
    )


def test_platforms_without_a_window_are_always_open():
    old = datetime.now(timezone.utc) - timedelta(days=400)
    assert reply_window_open(platform=SurfacePlatform.TELEGRAM, last_inbound_at=old)
    assert reply_window_open(platform=SurfacePlatform.SLACK, last_inbound_at=None)


# ----------------------------------------------------------- channel resolution


def test_chat_outranks_email():
    """Email is the fallback that always works, not the one people are watching."""
    email = DeliveryChannel(surface=_surface(SurfacePlatform.RESEND), email_address="a@b.c")
    chat = DeliveryChannel(
        surface=_surface(SurfacePlatform.TELEGRAM),
        external_user_id="u1",
        link=_link(datetime.now(timezone.utc)),
    )

    ordered = rank_candidates([email, chat])
    assert ordered[0] is chat


def test_the_surface_the_agent_is_running_on_wins():
    """Reach out from the bot they are already talking to this agent through.

    Replaces the old rule, which preferred the *recipient's* chosen surface.
    That borrowed trust the sender never had: a person who set Slack as their
    default would get messaged there by an agent they only know on Telegram.
    """
    now = datetime.now(timezone.utc)
    here_id = uuid4()
    here = DeliveryChannel(
        surface=_surface(SurfacePlatform.SLACK, here_id),
        external_user_id="u1",
        # Deliberately the *staler* of the two, so freshness alone cannot explain
        # the result.
        link=_link(now - timedelta(days=3)),
    )
    elsewhere = DeliveryChannel(
        surface=_surface(SurfacePlatform.TELEGRAM),
        external_user_id="u2",
        link=_link(now),
    )

    ordered = rank_candidates([elsewhere, here], origin_surface_id=here_id)
    assert ordered[0] is here


def test_surfaces_are_scoped_to_the_sending_agent():
    """A pod's other agents have their own bots, and their own relationships."""
    from app.modules.agent_surfaces.services.notification_delivery import (
        surfaces_for_agent,
    )

    mine_id, theirs_id = uuid4(), uuid4()
    mine = _surface(SurfacePlatform.TELEGRAM)
    mine.agent_id = mine_id
    theirs = _surface(SurfacePlatform.SLACK)
    theirs.agent_id = theirs_id

    assert surfaces_for_agent([mine, theirs], actor_agent_id=mine_id) == [mine]


def test_the_pod_assistant_gets_the_surfaces_with_no_agent():
    """"No agent" is a deliberate choice on a surface, not an absence.

    Reading it as "any surface" would send the pod assistant out through a named
    agent's bot, over that agent's name.
    """
    from app.modules.agent_surfaces.services.notification_delivery import (
        surfaces_for_agent,
    )

    unowned = _surface(SurfacePlatform.RESEND)
    unowned.agent_id = None
    owned = _surface(SurfacePlatform.TELEGRAM)
    owned.agent_id = uuid4()

    assert surfaces_for_agent([unowned, owned], actor_agent_id=None) == [unowned]


def test_among_observed_channels_the_freshest_inbound_wins():
    now = datetime.now(timezone.utc)
    stale = DeliveryChannel(
        surface=_surface(SurfacePlatform.SLACK),
        external_user_id="u1",
        link=_link(now - timedelta(days=2)),
    )
    fresh = DeliveryChannel(
        surface=_surface(SurfacePlatform.TELEGRAM),
        external_user_id="u2",
        link=_link(now - timedelta(minutes=5)),
    )

    ordered = rank_candidates([stale, fresh])
    assert ordered[0] is fresh


def test_freshness_falls_back_to_updated_at_for_pre_migration_rows():
    """Rows written before ``last_inbound_at`` existed must still sort."""
    now = datetime.now(timezone.utc)
    legacy_fresh = DeliveryChannel(
        surface=_surface(SurfacePlatform.SLACK),
        external_user_id="u1",
        link=_link(None, updated_at=now),
    )
    stale = DeliveryChannel(
        surface=_surface(SurfacePlatform.TELEGRAM),
        external_user_id="u2",
        link=_link(now - timedelta(days=5)),
    )

    ordered = rank_candidates([stale, legacy_fresh])
    assert ordered[0] is legacy_fresh


# --------------------------------------------------- the DM-reset regression


def _dm_surface(reset_hours: int = 24) -> AgentSurfaceEntity:
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        name="telegram",
        surface_type=SurfacePlatform.TELEGRAM,
        config=SurfaceConfig(dm_conversation_reset_after_hours=reset_hours),
    )


def test_an_outbound_notification_does_not_suppress_the_dm_reset():
    """The bug this column exists to fix.

    Keying the reset off ``updated_at`` meant a proactive send counted as
    activity: the person comes back two days later and the agent is still
    holding a conversation from before, silently.
    """
    service = AgentSurfaceIngressService.__new__(AgentSurfaceIngressService)
    now = datetime.now(timezone.utc)
    link = _link(
        # They last wrote two days ago...
        last_inbound_at=now - timedelta(days=2),
        # ...but we messaged them a minute ago, which bumps updated_at.
        updated_at=now - timedelta(minutes=1),
    )

    assert service._should_reset_dm_conversation(surface=_dm_surface(), link=link)


def test_dm_reset_falls_back_to_updated_at_for_pre_migration_rows():
    service = AgentSurfaceIngressService.__new__(AgentSurfaceIngressService)
    now = datetime.now(timezone.utc)

    recent_legacy = _link(last_inbound_at=None, updated_at=now - timedelta(hours=1))
    assert not service._should_reset_dm_conversation(
        surface=_dm_surface(), link=recent_legacy
    )

    old_legacy = _link(last_inbound_at=None, updated_at=now - timedelta(days=3))
    assert service._should_reset_dm_conversation(
        surface=_dm_surface(), link=old_legacy
    )


def test_a_live_thread_is_not_reset():
    service = AgentSurfaceIngressService.__new__(AgentSurfaceIngressService)
    link = _link(last_inbound_at=datetime.now(timezone.utc) - timedelta(minutes=10))
    assert not service._should_reset_dm_conversation(surface=_dm_surface(), link=link)


# --------------------------------------------------------- the surface policy


def test_surface_send_policy_does_not_gate_reaching_other_members():
    """The MESSAGING toolset is the grant; the surface policy is not a second one.

    ``allow_send`` governs the surface's own current-conversation
    ``surface_send_message`` tool and nothing else. Gating ``message_user`` on it
    too would mean a pod editor had to flip a setting on a bot before a toolset
    grant they had already made took effect — a rule nobody would guess.
    """
    policy = SurfaceSendPolicy()
    assert policy.allow_send is False
    assert not hasattr(policy, "audience")
    assert not hasattr(policy, "allows_messaging_other_members")


def test_surface_send_policy_round_trips_through_stored_json():
    stored = SurfaceSendPolicy(allow_send=True).model_dump(mode="json")
    assert SurfaceSendPolicy.model_validate(stored).allow_send is True


def test_the_ingress_service_answers_every_call_delivery_makes():
    """One line that would have caught both shipped AttributeErrors.

    Notification delivery holds the ingress service through a port. Two of the
    methods it declared were never written on the implementation, and nothing
    noticed: the attribute was untyped, so mypy saw nothing, and every existing
    test ran in a pod with no surface, so delivery returned before calling
    either. A structural check costs nothing and fails the moment the two drift.
    """
    from app.modules.agent_surfaces.domain.ports import (
        SurfaceNotificationEgressPort,
    )

    service = AgentSurfaceIngressService.__new__(AgentSurfaceIngressService)
    assert isinstance(service, SurfaceNotificationEgressPort)


def test_an_agent_falls_back_to_the_pods_own_surface():
    """The shape almost every existing pod has, and the regression that broke it.

    One pod-level Slack or Telegram bot with no agent of its own, routed to
    named agents by channel. Scoping strictly to `surface.agent_id == actor`
    resolved to nothing, so every agent in every pod predating per-agent
    mailboxes could suddenly reach nobody. The pod's own bot borrows no other
    agent's identity, and the message still names the agent.
    """
    from app.modules.agent_surfaces.services.notification_delivery import (
        surfaces_for_agent,
    )

    pod_surface = _surface(SurfacePlatform.SLACK)
    pod_surface.agent_id = None

    assert surfaces_for_agent([pod_surface], actor_agent_id=uuid4()) == [pod_surface]


def test_an_agent_with_its_own_surface_does_not_borrow_the_pods():
    """The fallback must not weaken the identity rule it sits behind."""
    from app.modules.agent_surfaces.services.notification_delivery import (
        surfaces_for_agent,
    )

    agent_id = uuid4()
    pod_surface = _surface(SurfacePlatform.SLACK)
    pod_surface.agent_id = None
    own = _surface(SurfacePlatform.TELEGRAM)
    own.agent_id = agent_id

    assert surfaces_for_agent([pod_surface, own], actor_agent_id=agent_id) == [own]
