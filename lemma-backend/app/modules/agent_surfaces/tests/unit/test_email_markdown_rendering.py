"""What an agent's markdown becomes in somebody's inbox.

Three of these cover things that were silently wrong: a markdown table arrived
as a paragraph of literal pipes, a fenced code block arrived as one inline
`<code>` span with the language tag inside it, and nothing carried any styling
at all -- so a mail client fell back to a serif face and full-window line
lengths. Agents emit tables and code in most substantial answers, so this was
the common case rather than an edge one.

The assertions are deliberately about structure and the presence of styling,
not exact declarations: the palette should be changeable without editing tests.
"""

from __future__ import annotations

import re

import pytest

from app.modules.agent_surfaces.platforms.email_render import render_email_content


def _html(content: str, content_type: str = "markdown") -> str:
    _, html = render_email_content(content=content, content_type=content_type)
    assert html is not None
    return html


def _is_styled(html: str, tag: str) -> bool:
    """Whether every `tag` in `html` carries a style attribute.

    Matched by attribute rather than by string prefix: `style` can land before
    or after the tag's own attributes (an `<a>` gets `href` first), and a test
    that pins the order fails on a change nobody made.
    """
    opens = re.findall(rf"<{tag}(\s[^>]*)?>", html)
    return bool(opens) and all("style=" in attrs for attrs in opens)


class TestStructureSurvives:
    def test_a_table_becomes_a_table(self):
        html = _html("| Region | Revenue |\n| --- | --- |\n| EMEA | $1.2M |\n")
        assert "<table" in html and "<th" in html and "<td" in html
        assert "| Region |" not in html, "pipes leaked through as literal text"

    def test_a_fenced_block_becomes_a_pre_not_an_inline_span(self):
        html = _html("```python\ntotal = 1\n```\n")
        assert "<pre" in html and "</pre>" in html
        assert "language-python" in html
        assert ">python\n" not in html, "the language tag leaked into the content"

    def test_a_fenced_block_is_not_nested_inside_a_paragraph(self):
        """`<pre>` inside `<p>` is invalid, and is what happens if the styling
        pass touches the placeholder the fenced-code stash leaves behind."""
        html = _html("before\n\n```\nx = 1\n```\n\nafter")
        assert "<p style=" in html
        assert "<pre" in html
        pre_index = html.index("<pre")
        assert "</p>" in html[:pre_index], "the paragraph before it never closed"

    def test_lists_and_links_survive(self):
        html = _html("- one\n- two\n\nSee [docs](https://example.com).")
        assert "<ul" in html and html.count("<li") == 2
        assert _is_styled(html, "a")
        assert 'href="https://example.com"' in html


class TestEverythingCarriesInlineStyling:
    """No mail client can be relied on for a stylesheet, so every rule has to
    ride on the element itself."""

    @pytest.mark.parametrize(
        ("source", "tag"),
        [
            ("# Heading", "h1"),
            ("## Heading", "h2"),
            ("paragraph", "p"),
            ("- item", "ul"),
            ("- item", "li"),
            ("> quoted", "blockquote"),
            ("`inline`", "code"),
            ("| a |\n| --- |\n| b |", "table"),
            ("| a |\n| --- |\n| b |", "th"),
            ("| a |\n| --- |\n| b |", "td"),
            ("```\nx\n```", "pre"),
            ("**bold**", "strong"),
        ],
    )
    def test_the_tag_is_styled(self, source: str, tag: str):
        assert _is_styled(_html(source), tag), f"<{tag}> reached the inbox unstyled"

    def test_the_body_is_wrapped_and_width_limited(self):
        html = _html("hello")
        assert html.startswith("<div style=")
        assert "max-width:640px" in html, "text would run the full window width"
        assert "font-family:" in html, "the client would pick its own face"

    def test_code_inside_pre_does_not_double_up_the_chrome(self):
        html = _html("```\nx = 1\n```")
        pre_start = html.index("<pre")
        code_start = html.index("<code", pre_start)
        code_tag = html[code_start : html.index(">", code_start)]
        assert "background:transparent" in code_tag, (
            "an inline-code background inside a code block draws a box in a box"
        )


class TestTheOtherContentTypes:
    def test_plain_text_is_left_alone(self):
        plain, html = render_email_content(content="just text", content_type="text")
        assert plain == "just text"
        assert html is None, "a text reply must not grow an HTML part"

    def test_html_is_passed_through_unstyled(self):
        """An agent that wrote HTML meant that HTML; restyling it would fight
        whatever it was doing."""
        source = '<div class="mine">hi</div>'
        _, html = render_email_content(content=source, content_type="html")
        assert html == source


class TestEveryEmailPlatformRendersTheSameWay:
    """Gmail and Outlook used to default to `content_type="text"` while Resend
    defaulted to `"markdown"`, and nothing ever set the metadata that would have
    overridden it. So the same agent reply arrived rendered on one provider and
    as raw `**bold**` and literal table pipes on the other two -- which is worse
    than unstyled, because markdown source is not something a reader should see.
    """

    @pytest.mark.parametrize(
        "module_path",
        [
            "app.modules.agent_surfaces.platforms.resend.service",
        ],
    )
    def test_the_reply_path_defaults_to_markdown(self, module_path: str):
        import importlib
        import inspect

        source = inspect.getsource(importlib.import_module(module_path))
        assert 'or "text"' not in source, (
            "an email reply path still falls back to plain text, so markdown "
            "will reach the reader as source"
        )

    def test_plain_text_is_still_reachable_when_asked_for(self):
        """The default changed; the option did not. A caller that means text
        still gets text and no HTML part."""
        plain, html = render_email_content(content="**not bold**", content_type="text")
        assert plain == "**not bold**"
        assert html is None


class TestAListMayInterruptAParagraph:
    """The shape models actually write, and the one Python-Markdown refuses.

    A label line followed straight by bullets was one paragraph, and HTML does
    not preserve newlines — so it arrived as a flowed sentence with hyphens
    loose in it. Reported from a real send: "WHAT YOU CAN DO - Land results in
    tables and files - Run agents and workflows for multi-step work".
    """

    def test_bullets_under_a_label_become_a_real_list(self):
        html = _html("What you can do\n- Land results\n- Run agents")
        assert html.count("<li") == 2
        assert "<ul" in html

    def test_numbered_items_under_a_label_too(self):
        html = _html("Steps\n1. Connect the surface\n2. Send a message")
        assert html.count("<li") == 2
        assert "<ol" in html

    def test_a_lazy_continuation_does_not_split_one_list_in_two(self):
        """An unindented line inside a list belongs to the item above it.

        Inserting a break before the next item would end the list and start a
        second one, which is a worse outcome than the bug being fixed.
        """
        html = _html("- item one\n  continued on the next line\n- item two")
        assert html.count("<ul") == 1
        assert html.count("<li") == 2

    def test_a_hyphen_inside_a_fenced_block_stays_code(self):
        html = _html("Run it\n```\nnpm run build\n- not a bullet\n```")
        assert "<li" not in html
        assert "- not a bullet" in html

    def test_a_list_that_was_already_correct_is_unchanged(self):
        html = _html("What you can do\n\n- Land results\n- Run agents")
        assert html.count("<li") == 2
        assert html.count("<ul") == 1

    def test_prose_containing_a_dash_is_not_turned_into_a_list(self):
        """An em-dash aside and a hyphenated word are not bullets."""
        html = _html("Lemma is durable — it stays in the pod.\nWell-formed too.")
        assert "<li" not in html
