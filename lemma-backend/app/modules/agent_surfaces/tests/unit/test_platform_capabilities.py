from __future__ import annotations

import pytest

from app.modules.agent_surfaces.platforms.platform_capabilities import (
    PLATFORM_CAPABILITIES,
    get_platform_capabilities,
    has_shared_system_bot,
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


def test_channel_capable_is_the_platforms_whose_history_we_can_read():
    """The set is a consequence, not a preference.

    It used to read `{"SLACK", "TEAMS"}` with nothing saying why, and it was
    wrong: `TelegramSurfaceAdapter` implements `fetch_thread_context`, the router
    has a group route, and `test_telegram_group_injects_reply_as_channel_context`
    asserts a Telegram group reply reaches the agent as `channel_context`. The
    only reader of this field is the standing guidance, so the flag said "you
    have no channel history here" to the one chat platform that was handing it
    some. Nothing failed, because a bare set restated the constant instead of the
    rule -- which is the whole reason the conformance test in
    `test_adapter_contract.py` now checks the claim against the adapter.

    WhatsApp is absent for the same reason it was always absent: no
    `fetch_thread_context`, so there is no history to promise.
    """
    channel = {p for p, c in PLATFORM_CAPABILITIES.items() if c.is_channel_capable}
    assert channel == {"SLACK", "TEAMS", "TELEGRAM"}


def test_channel_history_is_the_platforms_that_can_fetch_a_window():
    """A second fact, and the reason it is second.

    `is_channel_capable` was doing two jobs: "can be @-mentioned in a group"
    and "can read the conversation around the mention". Telegram is the first
    and not the second -- `TelegramSurfaceAdapter.fetch_thread_context` returns
    at most the one message being replied to, and says so: "Telegram bots cannot
    read group history". So the guidance was promising it a
    recent-channel-message tool it does not have.
    """
    history = {p for p, c in PLATFORM_CAPABILITIES.items() if c.reads_channel_history}
    assert history == {"SLACK", "TEAMS"}


def test_no_platform_names_a_reply_tool_any_more():
    """Deleted with the tool. The observer sends the one reply on every email
    surface now, so there is nothing for the prompt to name."""
    for platform in PLATFORM_CAPABILITIES:
        assert not hasattr(get_platform_capabilities(platform), "reply_tool")


def test_only_telegram_renders_a_native_voice_note():
    voice = {p for p, c in PLATFORM_CAPABILITIES.items() if c.supports_native_voice}
    assert voice == {"TELEGRAM"}


def test_the_shared_system_bot_platforms():
    """One shared bot / number per deployment: the platforms a shared webhook may narrow."""
    shared = {p for p, c in PLATFORM_CAPABILITIES.items() if c.has_shared_system_bot}
    assert shared == {"TELEGRAM", "WHATSAPP"}


@pytest.mark.parametrize(
    ("platform", "expected"),
    [
        ("telegram", True),
        ("WHATSAPP", True),
        ("SLACK", False),
        ("RESEND", False),
        ("DISCORD", False),
        (None, False),
    ],
)
def test_has_shared_system_bot_is_answered_from_the_registry(platform, expected):
    assert has_shared_system_bot(platform) is expected


@pytest.mark.parametrize("platform", sorted(PLATFORM_CAPABILITIES))
def test_platform_facts_mirror_the_registry(platform):
    """The agent's view is a snapshot of the registry, field for field."""
    from app.modules.agent_surfaces.contracts.platforms import platform_facts

    capabilities = get_platform_capabilities(platform)
    facts = platform_facts(platform)

    assert facts is not None
    assert facts.platform == capabilities.platform
    assert facts.display_name == capabilities.display_name
    assert facts.is_email is capabilities.is_email
    assert facts.delivery_cardinality is capabilities.delivery_cardinality
    assert facts.reply_window_hours == capabilities.reply_window_hours
    assert facts.progress_style is capabilities.progress_style
    assert facts.shows_live_progress is capabilities.shows_live_progress
    assert facts.soft_char_limit == capabilities.soft_char_limit
    assert facts.formatting_style == capabilities.formatting_style
    assert facts.supports_native_choices is capabilities.supports_native_choices
    assert facts.supports_native_files is capabilities.supports_native_files
    assert facts.supports_native_voice is capabilities.supports_native_voice
    assert facts.is_channel_capable is capabilities.is_channel_capable
    assert facts.reads_channel_history is capabilities.reads_channel_history
    assert facts.inline_mb_cap == capabilities.inline_mb_cap
    assert facts.media_cap_note == capabilities.media_cap_note
    assert facts.has_shared_system_bot is capabilities.has_shared_system_bot


def test_platform_facts_is_case_insensitive_and_none_for_unknown():
    from app.modules.agent_surfaces.contracts.platforms import platform_facts

    assert platform_facts("whatsapp") == platform_facts("WHATSAPP")
    assert platform_facts("WHATSAPP").reply_window_hours == 24
    assert platform_facts("DISCORD") is None
    assert platform_facts(None) is None


def test_platform_facts_are_frozen():
    import dataclasses

    from app.modules.agent_surfaces.contracts.platforms import platform_facts

    with pytest.raises(dataclasses.FrozenInstanceError):
        platform_facts("SLACK").soft_char_limit = 1  # type: ignore[misc]
