"""Deterministic guards for WhatsApp outbound text translation/sanitization.

These cover the bug where Markdown leaks into WhatsApp messages and renders as
literal ``*asterisks*`` instead of bold, and where a stray delimiter makes
WhatsApp drop all native formatting in a message.
"""

from app.modules.agent_surfaces.platforms.whatsapp.text_format import (
    balance_whatsapp_delimiters,
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


def test_heading_becomes_bold():
    # WhatsApp has no headings, and a heading flattened to bare prose loses the
    # structure the author meant. Bold is the closest thing the platform has.
    assert to_whatsapp_text("# Eyebrow") == "*Eyebrow*"
    assert to_whatsapp_text("## Secondary") == "*Secondary*"


def test_inline_code_maps_to_whatsapp_monospace():
    assert to_whatsapp_text("run `lemma --help`") == "run ```lemma --help```"


def test_fenced_code_block_keeps_the_fence_and_drops_the_language_tag():
    # WhatsApp renders a multi-line ``` block as monospace. Dropping the fence
    # would leave a shell transcript indistinguishable from the prose above it;
    # keeping the ``py`` tag would print it as the block's first line.
    assert to_whatsapp_text("```py\nx = 1\n```") == "```\nx = 1\n```"


def test_markdown_inside_a_code_block_is_delivered_verbatim():
    # Inside a fence, ``**`` and ``|`` are the code, not formatting.
    raw = "See:\n```py\nx = a**b\n| a | b |\n```"
    assert to_whatsapp_text(raw) == "See:\n```\nx = a**b\n| a | b |\n```"


def test_existing_whatsapp_monospace_is_not_mangled():
    assert to_whatsapp_text("```if x:```") == "```if x:```"


def test_bare_asterisk_bullet_is_dropped_and_does_not_kill_bold():
    raw = "list:\n*\n*Agents, Apps, People*\nend"
    result = to_whatsapp_text(raw)
    assert "*Agents, Apps, People*" in result
    # The bare bullet line no longer sits between the two *bold* markers.
    assert "\n*\n" not in result


def test_star_bullets_become_native_whatsapp_bullets():
    # Deleting the marker flattens a list into an unmarked run of lines; WhatsApp
    # bullets ``- `` natively, so the bullet is translated, not dropped.
    assert to_whatsapp_text("* first\n* second") == "- first\n- second"
    assert to_whatsapp_text("+ first") == "- first"


def test_unpaired_marker_hugging_text_on_one_side_is_dropped():
    # ``*bold`` never finds a partner, so the marker would land as a literal
    # asterisk mid-sentence.
    assert to_whatsapp_text("this is *bold") == "this is bold"
    assert to_whatsapp_text("**line one\nline two**") == "line one\nline two"


def test_a_character_the_author_typed_is_not_deleted():
    # The old rule deleted any delimiter hugging whitespace, which silently
    # rewrote arithmetic and identifiers.
    assert to_whatsapp_text("2 * 3 = 6") == "2 * 3 = 6"
    assert to_whatsapp_text("a ~ b") == "a ~ b"
    assert to_whatsapp_text("call var_name twice") == "call var_name twice"


def test_image_does_not_leave_a_stranded_bang():
    assert to_whatsapp_text("![alt](https://x.test/a.png)") == "https://x.test/a.png"


def test_table_is_flattened_into_readable_lines():
    assert to_whatsapp_text("| a | b |\n|---|---|\n| 1 | 2 |") == "a — b\n\n1 — 2"


def test_underscore_strong_maps_to_whatsapp_bold():
    assert to_whatsapp_text("__Agents__") == "*Agents*"


def test_translation_is_idempotent():
    # Several call sites format then truncate then re-balance; running the
    # translation twice must not keep eating the message.
    once = to_whatsapp_text("# Title\n\n**bold** and `code`\n* one\n* two")
    assert to_whatsapp_text(once) == once


def test_balancing_repairs_a_pair_cut_in_half_by_truncation():
    # Truncation happens after translation, so a 4096-character slice can cut a
    # ``*bold*`` pair in half — the exact broken marker this module prevents.
    assert balance_whatsapp_delimiters("intro *bol") == "intro bol"


def test_empty_and_whitespace_input_are_safe():
    assert to_whatsapp_text("") == ""
    assert to_whatsapp_text("   ") == ""


def test_plain_text_strips_markers_for_captions():
    assert to_plain_text("**Live** *hero*") == "Live hero"
    assert to_plain_text("2 * 3 = 6") == "2 * 3 = 6"
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
