"""Unit tests for HTML fragment wrapping."""

from __future__ import annotations

from app.core.html_document import wrap_html_fragment


def test_wraps_fragment_into_full_document():
    doc = wrap_html_fragment("<div>hi</div>", title="My Widget", embed=True)
    assert doc.startswith("<!doctype html>")
    assert "<div>hi</div>" in doc
    assert "<title>My Widget</title>" in doc
    # embed mode includes the height bridge
    assert "lemma-widget-height" in doc
    assert "lemma-widget-theme" in doc
    assert "--lemma-widget-text" in doc
    assert "lemma-widget-submit" not in doc
    assert "data-lemma-submit-bridge" not in doc


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
