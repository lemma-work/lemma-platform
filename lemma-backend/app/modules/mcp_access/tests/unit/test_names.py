"""What the consent screen and the connected list say about a client."""

from __future__ import annotations

import pytest

from app.modules.mcp_access.domain.names import MAX_DISPLAY_NAME, display_name
from app.modules.mcp_access.services.consent import _redirect_target

pytestmark = pytest.mark.unit


def test_a_name_cannot_reverse_the_sentence_it_sits_in():
    spoofed = "Claude\u202e reads backwards from here\u202c"
    assert "\u202e" not in display_name(spoofed, "fallback")
    assert "\u202c" not in display_name(spoofed, "fallback")


def test_a_name_is_one_short_line():
    assert display_name("Claude\n\n  Desktop\t", "x") == "Claude Desktop"
    long = display_name("A" * 1_000, "x")
    assert len(long) == MAX_DISPLAY_NAME
    assert long.endswith("…")


def test_a_name_of_nothing_visible_falls_back():
    assert display_name("\u200b\u202e\u2066", "claude.ai") == "claude.ai"
    assert display_name(None, "claude.ai") == "claude.ai"


@pytest.mark.parametrize(
    ("uri", "shown"),
    [
        ("https://claude.ai/api/mcp/auth_callback", "claude.ai"),
        ("http://127.0.0.1:53682/callback", "127.0.0.1:53682"),
        # The authority of an app's own URI is whatever the app wrote.
        ("evilapp://claude.ai/cb", "evilapp:"),
        ("com.example.app:/oauth", "com.example.app:"),
    ],
)
def test_the_redirect_is_named_by_what_can_be_checked(uri, shown):
    assert _redirect_target(uri) == shown
