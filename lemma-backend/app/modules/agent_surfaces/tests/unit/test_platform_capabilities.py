from __future__ import annotations

import pytest

from app.modules.agent_surfaces.platforms.platform_capabilities import (
    PLATFORM_CAPABILITIES,
    get_platform_capabilities,
    platform_agent_guidance,
)


def test_get_platform_capabilities_is_case_insensitive():
    assert get_platform_capabilities("slack") is get_platform_capabilities("SLACK")
    assert get_platform_capabilities("SLACK").platform == "SLACK"


def test_get_platform_capabilities_unknown_is_none():
    assert get_platform_capabilities("DISCORD") is None
    assert get_platform_capabilities(None) is None
    assert get_platform_capabilities("") is None


@pytest.mark.parametrize("platform", sorted(PLATFORM_CAPABILITIES))
def test_attachment_cap_reused_from_limits(platform):
    from app.modules.agent_surfaces.platforms.attachment_limits import attachment_cap

    caps = get_platform_capabilities(platform)
    assert caps.attachment_byte_cap == attachment_cap(platform)


def test_native_choices_platforms():
    native = {
        p for p, c in PLATFORM_CAPABILITIES.items() if c.supports_native_choices
    }
    # All chat surfaces render native choices; only email surfaces don't.
    assert native == {"SLACK", "TEAMS", "TELEGRAM", "WHATSAPP"}


def test_email_platforms_flagged():
    email = {p for p, c in PLATFORM_CAPABILITIES.items() if c.is_email}
    assert email == {"GMAIL", "OUTLOOK", "RESEND"}


def test_channel_capable_only_slack_teams():
    channel = {p for p, c in PLATFORM_CAPABILITIES.items() if c.is_channel_capable}
    assert channel == {"SLACK", "TEAMS"}


def test_slack_guidance_has_native_choices_channel_and_mrkdwn():
    text = platform_agent_guidance("SLACK")
    assert "Talking over Slack" in text
    assert "ask_user" in text and "native tappable options" in text
    assert "Channel background context" in text
    assert "mrkdwn" in text
    assert "5 MB" in text  # effective inline cap = min(30MB hard, 5MB soft)


def test_whatsapp_guidance_has_native_choices_and_omits_channel():
    text = platform_agent_guidance("WHATSAPP")
    assert "Talking over WhatsApp" in text
    assert "Channel background context" not in text  # not channel-capable
    assert "ask_user" in text and "native tappable options" in text


def test_unknown_platform_guidance_is_empty():
    assert platform_agent_guidance("DISCORD") == ""
    assert platform_agent_guidance(None) == ""


def test_email_platforms_carry_reply_tool():
    assert get_platform_capabilities("GMAIL").reply_tool == "gmail_reply_email"
    assert get_platform_capabilities("OUTLOOK").reply_tool == "outlook_reply_email"
    assert get_platform_capabilities("SLACK").reply_tool is None


def test_email_guidance_points_to_reply_tool_not_display_resource():
    text = platform_agent_guidance("GMAIL")
    assert "gmail_reply_email" in text
    assert "attachment_paths" in text
    # Email must NOT tell the agent display_resource delivers files to the user.
    assert "type=FILE" not in text
    assert "## Delivering things" not in text
    assert "does NOT reach the email recipient" in text
