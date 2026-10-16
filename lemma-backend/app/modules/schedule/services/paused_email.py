"""The email that tells an owner the platform paused their schedule."""

from __future__ import annotations

from uuid import UUID

from app.core.email.transactional import (
    EmailAction,
    EmailDetail,
    RenderedEmail,
    render_transactional_email,
)
from app.core.helpers.humanize import humanize_name


def render_schedule_paused_email(
    *,
    schedule_name: str | None,
    schedule_id: UUID,
    consecutive_failures: int,
    review_url: str,
    reason: str = "consecutive_failures",
) -> tuple[str, RenderedEmail]:
    display_name = humanize_name(schedule_name) if schedule_name else None
    display_name = display_name or f"Schedule {schedule_id}"
    copy = {
        "SCHEDULE_TOO_FREQUENT": (
            "was paused because it runs too frequently",
            "Lemma paused this scheduled automation because its recurring frequency exceeds the configured minimum interval.",
        ),
        "SCHEDULE_ONE_TIME_MISSED": (
            "was paused because its one-time execution was missed",
            "Lemma paused this one-time automation because its scheduled timestamp has already passed.",
        ),
        "SCHEDULE_REJECTED": (
            "was paused because the scheduler rejected it",
            "Lemma paused this scheduled automation because the scheduler could not accept its configuration.",
        ),
        "MCP_AUTHOR_LEFT_POD": (
            "was turned off because the person who set it up left the space",
            (
                "Lemma turned off this automation because it listened to a connected "
                "server through the account of someone who is no longer in the "
                "space. Someone still in the space can set it up again on their own "
                "account."
            ),
        ),
        "MCP_SUBSCRIPTION_LAPSED": (
            "was turned off because its server stopped telling us about events",
            (
                "Lemma turned off this automation because the connected server "
                "stopped accepting its subscription, so it could never start again. "
                "Reconnect the account if it needs signing in again, then turn the "
                "automation back on."
            ),
        ),
        "fire_cap": (
            "was paused because it fired more times in a day than allowed",
            (
                "Lemma paused this automation because it started more runs in "
                "one day than its daily limit. That usually means it is "
                "triggering itself -- a workflow writing to the table it "
                "watches, or a webhook sending in a loop. What it would have "
                "fired since is listed as skipped on the schedule."
            ),
        ),
        "fire_failed": (
            "was paused because Lemma could not start its scheduled run",
            (
                "Lemma paused this scheduled automation because starting one of "
                "its scheduled runs kept failing, and it would otherwise have "
                "stopped without a word. Turning it back on schedules its next "
                "run."
            ),
        ),
        "SCHEDULE_VALIDATION_ERROR": (
            "was paused because its configuration is invalid",
            "Lemma paused this scheduled automation because its time configuration is invalid.",
        ),
    }.get(
        reason,
        (
            "was paused after repeated failures",
            "Lemma automatically paused this scheduled automation after repeated failed runs.",
        ),
    )
    summary, explanation = copy
    details = [
        EmailDetail("Schedule", display_name),
        EmailDetail("Schedule ID", str(schedule_id)),
    ]
    if reason == "consecutive_failures":
        details.insert(
            1, EmailDetail("Consecutive failures", str(consecutive_failures))
        )
    rendered = render_transactional_email(
        preheader=f"{display_name} {summary}.",
        eyebrow="Automation paused",
        heading=f"{display_name} needs attention.",
        body=(
            explanation,
            (
                "Review the underlying error, then re-enable the schedule when the "
                "cause has been addressed."
            ),
        ),
        action=EmailAction("Review schedule", review_url),
        details=tuple(details),
        footer=(
            "You are receiving this because you created this scheduled automation.",
        ),
    )
    return f"{display_name} {summary}", rendered
