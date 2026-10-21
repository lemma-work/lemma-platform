from uuid import UUID

from app.modules.schedule.services.paused_email import (
    render_schedule_paused_email,
)


SCHEDULE_ID = UUID("7c693f24-65f1-4dad-b846-5b69e288e583")


def test_schedule_paused_email_humanizes_schedule_name():
    subject, rendered = render_schedule_paused_email(
        schedule_name="customer_follow_up",
        schedule_id=SCHEDULE_ID,
        consecutive_failures=4,
        review_url="https://lemma.work/pod/test/schedules",
    )

    assert subject == "Customer Follow Up was paused after repeated failures"
    assert "Customer Follow Up needs attention." in rendered.html
    assert "Consecutive failures: 4" in rendered.text
    assert "Review schedule: https://lemma.work/pod/test/schedules" in rendered.text


def test_schedule_paused_email_falls_back_to_identifier():
    subject, rendered = render_schedule_paused_email(
        schedule_name=None,
        schedule_id=SCHEDULE_ID,
        consecutive_failures=3,
        review_url="https://lemma.work",
    )

    assert str(SCHEDULE_ID) in subject
    assert f"Schedule ID: {SCHEDULE_ID}" in rendered.text


def test_a_schedule_turned_off_for_its_mcp_subscription_says_why():
    left, rendered = render_schedule_paused_email(
        schedule_name="triage",
        schedule_id=SCHEDULE_ID,
        consecutive_failures=0,
        review_url="https://lemma.work",
        reason="MCP_AUTHOR_LEFT_POD",
    )
    assert (
        left == "Triage was turned off because the person who set it up left the space"
    )
    assert "Consecutive failures" not in rendered.text

    lapsed, rendered = render_schedule_paused_email(
        schedule_name="triage",
        schedule_id=SCHEDULE_ID,
        consecutive_failures=3,
        review_url="https://lemma.work",
        reason="MCP_SUBSCRIPTION_LAPSED",
    )
    assert "stopped telling us about events" in lapsed
    assert "Reconnect the account" in rendered.text


def test_a_schedule_paused_for_a_dead_fire_says_why():
    subject, rendered = render_schedule_paused_email(
        schedule_name="nightly",
        schedule_id=SCHEDULE_ID,
        consecutive_failures=0,
        review_url="https://lemma.work",
        reason="fire_failed",
    )
    assert (
        subject == "Nightly was paused because Lemma could not start its scheduled run"
    )
    assert "Turning it back on schedules its next run." in rendered.text
    assert "Consecutive failures" not in rendered.text
