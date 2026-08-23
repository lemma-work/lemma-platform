"""Deterministic guards for WhatsApp outbound text translation/sanitization.

These cover the bug where Markdown leaks into WhatsApp messages and renders as
literal ``*asterisks*`` instead of bold, and where a stray delimiter makes
WhatsApp drop all native formatting in a message.
"""

from app.modules.agent_surfaces.platforms.whatsapp.text_format import (
    to_plain_text,
    to_whatsapp_text,
)


def test_native_whatsapp_syntax_is_preserved_untouched():
    # Text already in WhatsApp's own subset must pass through byte-for-byte.
    assert to_whatsapp_text("*Live hero (lemma.world)*") == "*Live hero (lemma.world)*"
    assert to_whatsapp_text("_italic_") == "_italic_"
    assert to_whatsapp_text("~strike~") == "~strike~"
    assert to_whatsapp_text("*Agents, Apps, People*") == "*Agents, Apps, People*"


def test_markdown_strong_maps_to_whatsapp_bold():
    assert to_whatsapp_text("**Agents, Apps, People**") == "*Agents, Apps, People*"
    assert to_whatsapp_text("see **bold** here") == "see *bold* here"


def test_markdown_link_reduces_to_bare_url():
    assert to_whatsapp_text("read [the homepage](https://lemma.world)") == (
        "read https://lemma.world"
    )


def test_heading_prefix_is_stripped():
    assert to_whatsapp_text("# Eyebrow") == "Eyebrow"
    assert to_whatsapp_text("## Secondary") == "Secondary"


def test_inline_code_maps_to_whatsapp_monospace():
    assert to_whatsapp_text("run `lemma --help`") == "run ```lemma --help```"


def test_fenced_code_block_drops_fences_keeps_body():
    assert to_whatsapp_text("```py\nx = 1\n```") == "x = 1"


def test_existing_whatsapp_monospace_is_not_mangled():
    assert to_whatsapp_text("```if x:```") == "```if x:```"


def test_bare_asterisk_bullet_is_dropped_and_does_not_kill_bold():
    raw = "list:\n*\n*Agents, Apps, People*\nend"
    result = to_whatsapp_text(raw)
    assert "*Agents, Apps, People*" in result
    # The bare bullet line no longer sits between the two *bold* markers.
    assert "\n*\n" not in result


def test_stray_delimiter_hugging_space_is_dropped():
    assert to_whatsapp_text("* bold") == "bold"
    assert to_whatsapp_text("a * ").strip() == "a"


def test_empty_and_whitespace_input_are_safe():
    assert to_whatsapp_text("") == ""
    assert to_whatsapp_text("   ") == ""


def test_plain_text_strips_markers_for_captions():
    assert to_plain_text("**Live** *hero*") == "Live hero"
    assert to_plain_text("[x](https://lemma.world)") == "https://lemma.world"
    assert to_plain_text("# Heading text") == "Heading text"


def test_plain_text_keeps_underscores_inside_identifiers():
    # word-internal underscores (var names) are not italic markers.
    assert to_plain_text("a_b and _italic_") == "a_b and italic"


def test_message_uses_to_whatsapp_backport_preserves_bullets():
    raw = "• *Agents, Apps, People*\n• *triad as H1*"
    result = to_whatsapp_text(raw)
    assert "• *Agents, Apps, People*" in result
    assert "• *triad as H1*" in result
