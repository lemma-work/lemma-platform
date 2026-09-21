"""Unit tests for HTML fragment wrapping."""

from __future__ import annotations

import re

from app.core.html_document import wrap_html_fragment


def test_wraps_fragment_into_full_document():
    doc = wrap_html_fragment("<div>hi</div>", title="My Widget", embed=True)
    assert doc.startswith("<!doctype html>")
    assert "<div>hi</div>" in doc
    assert "<title>My Widget</title>" in doc
    # embed mode includes the height bridge
    assert "lemma-widget-height" in doc
    assert "lemma-widget-theme" in doc
    assert "lemma-widget-submit" not in doc
    assert "data-lemma-submit-bridge" not in doc


def test_theme_is_taken_by_prefix_not_from_a_list_of_names():
    """A curated list guessed at what a widget would draw and guessed low: the
    hosts send state colours, the ink for an accent fill and the corner scale,
    and every one was dropped here. What stays is the syntax guard."""
    doc = wrap_html_fragment("<div>hi</div>", embed=True)
    assert "^--lemma-widget-[a-z0-9-]+$" in doc
    assert '"--lemma-widget-bg", "--lemma-widget-surface"' not in doc
    assert "UNSAFE" in doc and "@import" in doc


def test_height_is_measured_on_the_body_not_the_scrolling_area():
    """`documentElement.scrollHeight` is clamped to the viewport by spec, so it
    hands back whatever height the host just set: the frame could only grow, a
    filtered list kept its dead space, and a short widget stayed floored at the
    reservation it waited in."""
    doc = wrap_html_fragment("<div>hi</div>", embed=True)
    # Comments stripped: the bridge names the trap it avoids, and a check that
    # reads prose as code fails on the explanation rather than on the code.
    code = re.sub(r"//[^\n]*", "", doc)
    assert "documentElement.scrollHeight" not in code
    assert "body.getBoundingClientRect().height" in code
    assert "observe(document.body)" in code
    # Observing the root is the other half of the ratchet: it resizes because
    # the host resized the frame.
    assert "observe(document.documentElement)" not in code


def test_standalone_omits_height_bridge():
    doc = wrap_html_fragment("<div>hi</div>", embed=False)
    assert "lemma-widget-height" not in doc
    assert "lemma-widget-theme" not in doc
    assert "lemma-widget-submit" not in doc
    assert "<div>hi</div>" in doc


def test_full_document_passes_through_unchanged():
    full = "<!doctype html><html><body>x</body></html>"
    assert wrap_html_fragment(full) == full
    assert (
        wrap_html_fragment("  <html><body>y</body></html>  ")
        == "<html><body>y</body></html>"
    )


def test_title_is_escaped():
    doc = wrap_html_fragment("<div>x</div>", title="</title><script>evil</script>")
    assert "<script>evil</script>" not in doc
    assert "&lt;script&gt;" in doc
