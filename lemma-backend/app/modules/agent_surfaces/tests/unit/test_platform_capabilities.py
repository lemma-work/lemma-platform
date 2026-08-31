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
    native = {p for p, c in PLATFORM_CAPABILITIES.items() if c.supports_native_choices}
    # All chat surfaces render native choices; only email surfaces don't.
    assert native == {"SLACK", "TEAMS", "TELEGRAM", "WHATSAPP"}


def test_email_platforms_flagged():
    email = {p for p, c in PLATFORM_CAPABILITIES.items() if c.is_email}
    assert email == {"RESEND"}, "email is Resend; the Composio mailboxes are gone"


def test_channel_capable_only_slack_teams():
    channel = {p for p, c in PLATFORM_CAPABILITIES.items() if c.is_channel_capable}
    assert channel == {"SLACK", "TEAMS"}


def test_slack_guidance_has_native_choices_channel_and_mrkdwn():
    text = platform_agent_guidance("SLACK")
    assert "Talking over Slack" in text
    assert "ask_user" in text and "native tappable options" in text
    assert "Channel background context" in text
    assert "mrkdwn" in text
    assert "20 MB" in text  # effective inline cap = min(30MB hard, 20MB soft)


def test_whatsapp_guidance_has_native_choices_and_omits_channel():
    text = platform_agent_guidance("WHATSAPP")
    assert "Talking over WhatsApp" in text
    assert "Channel background context" not in text  # not channel-capable
    assert "ask_user" in text and "native tappable options" in text


def test_chat_guidance_tells_the_agent_how_to_ask_for_a_go_ahead():
    """A chat agent has to be told `request_approval` exists.

    Its own tool docstring frames it as what to do after a permission error, so
    an action the agent is already allowed to take gives the model no reason to
    reach for it — and "ask me to approve it first" gets answered in prose, with
    nothing to press and nothing paused. The buttons are a product promise
    (PS-SURF-021), and nothing else asserts the agent is ever told about them.
    """
    for platform in ("TELEGRAM", "SLACK", "WHATSAPP"):
        text = platform_agent_guidance(platform)
        assert "request_approval" in text, platform
        assert "rather than asking in prose" in text, platform

    # Where the platform has native controls, say they are buttons to tap.
    assert "buttons they can tap" in platform_agent_guidance("TELEGRAM")


def test_email_guidance_says_asking_works_but_not_with_buttons():
    """Email is interactive now, and the guidance has to say which way.

    This asserted the opposite until email could be asked: `ask_user` and
    `request_approval` failed fast there, so the guidance told the agent not to
    call them. They work now -- the question rides the one reply and the
    person's reply resolves the pause -- so the guidance must say so, or the
    agent avoids a tool that would have worked.

    The other half of the original assertion still holds and is why this is one
    test rather than two: the chat branch's "buttons to tap" line must not leak
    into email, which has no controls to tap.
    """
    text = platform_agent_guidance("RESEND")
    assert "`ask_user` and `request_approval` work here" in text
    assert "rather than asking in prose" not in text
    assert "buttons they can tap" not in text


def test_unknown_platform_guidance_is_empty():
    assert platform_agent_guidance("DISCORD") == ""
    assert platform_agent_guidance(None) == ""


def test_no_platform_names_a_reply_tool_any_more():
    """Deleted with the tool. The observer sends the one reply on every email
    surface now, so there is nothing for the prompt to name."""
    for platform in PLATFORM_CAPABILITIES:
        assert not hasattr(get_platform_capabilities(platform), "reply_tool")


def test_email_guidance_routes_everything_through_the_one_reply():
    """Email delivers once, so the prompt has to describe one reply, not a chat."""
    text = platform_agent_guidance("RESEND")
    assert "exactly one" in text
    assert "sent when you finish" in text
    # No tool to call: writing the reply is sending it.
    assert "reply_email" not in text
    # The chat delivery section belongs to platforms that can send more than once.
    assert "## Delivering things" not in text


def test_email_guidance_no_longer_calls_display_resource_useless():
    """It reaches the recipient now, as an attachment on the single reply.

    The prompt said it did not, which was true and is the reason the tool was
    left returning success while delivering nothing. Both halves are fixed, and
    a prompt still saying "do NOT call display_resource" would now be the lie.
    """
    text = platform_agent_guidance("RESEND")
    assert "does NOT reach the email recipient" not in text
    assert "attached to that reply" in text
    # And no longer forbids asking: what email cannot do is ask twice in a turn.
    assert "You can ask." in text
    assert "round trip" in text


def test_whatsapp_guidance_names_the_kinds_capped_below_the_headline():
    """The headline number is the document cap; the smaller kinds must be said.

    WhatsApp takes a 20 MB PDF and refuses a 6 MB PNG. An agent told only "20 MB"
    would compress a report it never needed to and hand over an image that comes
    out the other side as a link.
    """
    text = platform_agent_guidance("WHATSAPP")
    assert "20 MB" in text  # documents: min(100MB hard, 20MB soft)
    assert "images up to 5 MB" in text
    assert "audio and video up to 16 MB" in text


def test_uniform_platforms_carry_no_per_kind_caveat():
    assert get_platform_capabilities("SLACK").media_cap_note is None
    assert get_platform_capabilities("TELEGRAM").media_cap_note is None
    assert "stricter about some kinds" not in platform_agent_guidance("TELEGRAM")


def test_teams_files_are_link_only_and_say_so():
    """Teams has no outbound file upload, so the guidance must not promise one."""
    caps = get_platform_capabilities("TEAMS")
    assert caps.supports_native_files is False
    text = platform_agent_guidance("TEAMS")
    assert "cannot receive a file attachment from Lemma" in text
    assert "arrive as a real attachment" not in text


def test_chat_guidance_asks_for_a_pod_path_not_a_workspace_path():
    """`display_resource` rejects workspace paths, so the prompt cannot ask for one."""
    for platform in ("SLACK", "TELEGRAM", "WHATSAPP", "TEAMS"):
        text = platform_agent_guidance(platform)
        assert "path=<pod file path>" in text
        assert "path=<workspace path>" not in text


def test_chat_guidance_does_not_call_the_fallback_a_download_link():
    """The chat fallback is a pod-authenticated deep link, not a download URL.

    Promising a "download link" to an agent talking to an outside contact is how
    a file silently becomes nothing the recipient can open.
    """
    for platform in ("SLACK", "TELEGRAM", "WHATSAPP", "TEAMS"):
        text = platform_agent_guidance(platform)
        assert "download link" not in text
        assert "only opens for someone who can sign in to this pod" in text


def test_email_quotes_the_base64_adjusted_cap_not_the_chat_cap():
    """A 40 MB provider ceiling is ~28 MB of file once base64 has had its 33%."""
    assert get_platform_capabilities("RESEND").inline_mb_cap == 28
    assert "28 MB" in platform_agent_guidance("RESEND")
