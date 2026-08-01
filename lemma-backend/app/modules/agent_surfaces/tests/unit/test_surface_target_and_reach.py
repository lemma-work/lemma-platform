from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceConversationLink,
    AgentSurfaceEntity,
    MemberReach,
    ParsedInboundSurfaceEvent,
    ReachKind,
    ReachStatus,
    SendAudience,
    SurfaceConfig,
    SurfaceMode,
    SurfacePlatform,
    SurfaceSendPolicy,
    SurfaceTarget,
)
from app.modules.agent_surfaces.services.ingress_service import (
    AgentSurfaceIngressService,
)

# Every field any platform's send path reads off the event, per
# platform_capabilities and the per-platform services. If a new platform starts
# reading something else, this list is where it has to be added.
EGRESS_FIELDS = (
    "reply_target",
    "external_channel_id",
    "external_thread_id",
    "external_message_id",
    "sender_external_user_id",
    "sender_display_name",
    "sender_email",
    "sender_phone",
    "is_dm",
    "metadata",
)


def _event(**overrides) -> ParsedInboundSurfaceEvent:
    defaults = dict(
        platform=SurfacePlatform.TELEGRAM,
        conversation_type="EXTERNAL_DM",
        external_channel_id="chan-1",
        external_thread_id="thread-1",
        external_message_id="msg-1",
        sender_external_user_id="ext-1",
        sender_display_name="Deepak",
        sender_email="dj@acme.com",
        sender_phone="+15550134",
        message_text="hello",
        is_dm=True,
        reply_target={"chat_id": 4242, "message_id": 7, "in_reply_to": "<a@b>"},
        metadata={"message_thread_id": 9, "sender_username": "dj"},
        raw_payload={"bulky": "x" * 500},
    )
    defaults.update(overrides)
    return ParsedInboundSurfaceEvent(**defaults)


class TestSurfaceTarget:
    def test_every_egress_field_survives_the_round_trip(self):
        event = _event()
        rebuilt = event.to_target().to_event()
        for field in EGRESS_FIELDS:
            assert getattr(rebuilt, field) == getattr(event, field), field

    def test_it_survives_json_because_that_is_how_it_is_stored(self):
        """The target lives in a JSONB column, so the round trip that matters is
        through JSON, not through Python objects."""
        target = _event().to_target()
        revived = SurfaceTarget.model_validate(target.model_dump(mode="json"))
        assert revived.to_event().reply_target == target.reply_target
        assert revived.to_event().metadata == target.metadata

    def test_inbound_only_fields_are_dropped(self):
        """``raw_payload`` is large and only Outlook's inbound enrichment reads
        it; carrying it on every stored address would be waste."""
        rebuilt = _event().to_target().to_event()
        assert rebuilt.raw_payload == {}
        assert rebuilt.message_text == ""

    def test_group_and_dm_rebuild_the_right_conversation_type(self):
        assert _event(is_dm=True).to_target().to_event().conversation_type.value == (
            "EXTERNAL_DM"
        )
        dm_false = _event(is_dm=False, conversation_type="EXTERNAL_GROUP")
        assert dm_false.to_target().to_event().conversation_type.value == (
            "EXTERNAL_GROUP"
        )

    def test_email_threading_headers_ride_along(self):
        """Resend/Gmail/Outlook thread on In-Reply-To/References, which live in
        reply_target — losing them would turn every reply into a new thread."""
        target = _event(
            platform=SurfacePlatform.RESEND,
            reply_target={"in_reply_to": "<a@b>", "references": ["<a@b>", "<c@d>"]},
        ).to_target()
        rebuilt = target.to_event()
        assert rebuilt.reply_target["in_reply_to"] == "<a@b>"
        assert rebuilt.reply_target["references"] == ["<a@b>", "<c@d>"]


class TestReachDeliverability:
    def _reach(self, **overrides) -> MemberReach:
        defaults = dict(
            pod_id=uuid4(),
            user_id=uuid4(),
            kind=ReachKind.TELEGRAM,
            surface_id=uuid4(),
            target=_event().to_target(),
        )
        defaults.update(overrides)
        return MemberReach(**defaults)

    def test_the_app_reach_is_always_deliverable(self):
        """This is the property the whole fallback rests on."""
        assert MemberReach(
            pod_id=uuid4(), user_id=uuid4(), kind=ReachKind.APP
        ).is_deliverable()

    def test_opting_out_beats_everything_including_the_app(self):
        assert not MemberReach(
            pod_id=uuid4(),
            user_id=uuid4(),
            kind=ReachKind.APP,
            opted_out_at=datetime.now(timezone.utc),
        ).is_deliverable()

    def test_a_chat_reach_without_an_address_is_not_deliverable(self):
        assert not self._reach(target=None).is_deliverable()

    @pytest.mark.parametrize("status", [ReachStatus.STALE, ReachStatus.BLOCKED])
    def test_non_active_statuses_are_not_deliverable(self, status):
        assert not self._reach(status=status).is_deliverable()

    def test_an_expired_reply_window_closes_the_reach(self):
        expired = self._reach(
            kind=ReachKind.WHATSAPP,
            window_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        assert not expired.is_deliverable()

    def test_a_naive_window_timestamp_does_not_explode(self):
        """Postgres can hand back naive datetimes on some paths; the existing DM
        reset rule guards the same way."""
        reach = self._reach(
            kind=ReachKind.WHATSAPP,
            window_expires_at=datetime.now() + timedelta(hours=1),  # noqa: DTZ005
        )
        assert reach.is_deliverable()

    def test_reach_kind_maps_from_every_surface_platform(self):
        for platform in SurfacePlatform:
            assert ReachKind.for_platform(platform).value == platform.value


class TestDmResetKeysOffInboundRecency:
    """The regression ``last_inbound_at`` exists to prevent."""

    def _service(self):
        return AgentSurfaceIngressService.__new__(AgentSurfaceIngressService)

    def _surface(self):
        return AgentSurfaceEntity(
            pod_id=uuid4(),
            name="telegram",
            surface_type=SurfacePlatform.TELEGRAM,
            mode=SurfaceMode.DM,
            config=SurfaceConfig(),
        )

    def _link(self, **overrides):
        defaults = dict(
            surface_id=uuid4(),
            conversation_id=uuid4(),
            platform="TELEGRAM",
            external_thread_id="t",
        )
        defaults.update(overrides)
        return AgentSurfaceConversationLink(**defaults)

    def test_a_proactive_send_does_not_suppress_the_reset(self):
        """Row touched a second ago by an agent message, but the person has not
        spoken in 48h — the conversation must still reset."""
        now = datetime.now(timezone.utc)
        link = self._link(updated_at=now, last_inbound_at=now - timedelta(hours=48))
        assert self._service()._should_reset_dm_conversation(
            surface=self._surface(), link=link
        )

    def test_a_recent_human_message_keeps_the_conversation(self):
        now = datetime.now(timezone.utc)
        link = self._link(updated_at=now, last_inbound_at=now - timedelta(hours=1))
        assert not self._service()._should_reset_dm_conversation(
            surface=self._surface(), link=link
        )

    def test_rows_written_before_the_column_existed_keep_the_old_behaviour(self):
        now = datetime.now(timezone.utc)
        old = self._link(updated_at=now - timedelta(hours=48), last_inbound_at=None)
        fresh = self._link(updated_at=now - timedelta(hours=1), last_inbound_at=None)
        service, surface = self._service(), self._surface()
        assert service._should_reset_dm_conversation(surface=surface, link=old)
        assert not service._should_reset_dm_conversation(surface=surface, link=fresh)


class TestSendPolicyBackCompat:
    def test_the_default_grants_nothing(self):
        """A new capability must not switch itself on for surfaces nobody has
        revisited — the old default was allow_send=False."""
        policy = SurfaceSendPolicy()
        assert policy.audience is SendAudience.NOBODY
        assert not policy.allows_self and not policy.allows_pod_members

    @pytest.mark.parametrize(
        "legacy,expected",
        [(True, SendAudience.SELF), (False, SendAudience.NOBODY)],
    )
    def test_the_legacy_boolean_still_speaks(self, legacy, expected):
        assert (
            SurfaceSendPolicy.model_validate({"allow_send": legacy}).audience is expected
        )

    def test_an_explicit_audience_beats_a_stale_boolean(self):
        policy = SurfaceSendPolicy.model_validate(
            {"allow_send": False, "audience": "POD_MEMBERS"}
        )
        assert policy.audience is SendAudience.POD_MEMBERS
        assert policy.allows_pod_members and policy.allows_self

    def test_the_deprecated_field_does_not_leak_into_stored_config(self):
        assert "allow_send" not in SurfaceSendPolicy().model_dump()
        stored = SurfaceConfig.model_validate({"send_policy": {"allow_send": True}})
        assert "allow_send" not in stored.model_dump()["send_policy"]
