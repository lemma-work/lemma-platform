"""In-process extraction produces the shape the shared normalizer expects.

xberg is Kreuzberg renamed, so only the transport is replaced and everything
downstream is inherited. The one thing that is genuinely new is page boundaries:
xberg emits no ``<!-- PAGE n -->`` markers and leaves ``metadata.pages`` unset,
so they are recovered by locating each page's text inside the assembled
markdown. That reconstruction is what these cover.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.modules.datastore.config import DatastoreSettings
from app.modules.datastore.infrastructure.xberg_local_client import _page_boundaries


def _page(number: int, content: str):
    return SimpleNamespace(page_number=number, content=content)


def test_boundaries_are_found_in_order_and_cover_the_document():
    content = "alpha one\n\nbeta two\n\ngamma three"
    pages = [_page(1, "alpha one"), _page(2, "beta two"), _page(3, "gamma three")]

    bounds = _page_boundaries(content, pages)

    assert [b["page_number"] for b in bounds] == [1, 2, 3]
    assert [b["byte_start"] for b in bounds] == sorted(b["byte_start"] for b in bounds)
    # Contiguous, and the last one runs to the end.
    assert bounds[-1]["byte_end"] == len(content.encode("utf-8"))
    for earlier, later in zip(bounds, bounds[1:]):
        assert earlier["byte_end"] == later["byte_start"] - 1


def test_a_page_that_cannot_be_located_is_skipped_not_guessed():
    """Its text folds into the preceding page, which is what a processor
    emitting no boundaries at all already does -- rather than inventing an
    offset and mis-attributing a slice of the document."""
    content = "alpha one\n\ngamma three"
    pages = [_page(1, "alpha one"), _page(2, "never rendered"), _page(3, "gamma three")]

    bounds = _page_boundaries(content, pages)

    assert [b["page_number"] for b in bounds] == [1, 3]


def test_repeated_text_does_not_send_boundaries_backwards():
    """Each search starts where the last match ended, so a header repeated on
    every page cannot anchor page 5 to page 1's copy of it."""
    content = "Header\n\nfirst\n\nHeader\n\nsecond\n\nHeader\n\nthird"
    pages = [_page(1, "Header"), _page(2, "Header"), _page(3, "Header")]

    bounds = _page_boundaries(content, pages)

    starts = [b["byte_start"] for b in bounds]
    assert starts == sorted(starts)
    assert len(set(starts)) == len(starts)


def test_no_pages_means_no_boundaries_rather_than_an_empty_span():
    assert _page_boundaries("some text", []) == []
    assert _page_boundaries("", [_page(1, "x")]) == []


def test_multibyte_text_is_measured_in_bytes_not_characters():
    """The normalizer indexes into UTF-8 bytes, so a boundary counted in
    characters would land mid-document on anything non-ASCII."""
    content = "héllo wörld\n\nsecond page"
    pages = [_page(1, "héllo wörld"), _page(2, "second page")]

    bounds = _page_boundaries(content, pages)

    assert bounds[1]["byte_start"] == content.encode("utf-8").find(b"second page")
    assert bounds[1]["byte_start"] != content.find("second page")


@pytest.mark.parametrize(
    ("configured", "kreuzberg_url", "expected"),
    [
        ("auto", "", "xberg"),
        ("auto", "http://localhost:8002", "kreuzberg"),
        ("xberg", "http://localhost:8002", "xberg"),
        # Accepted rather than rejected: the value is written into every desktop
        # host pack and local .env that predates the rename, and an unknown
        # value fails config validation -- an upgrade that will not boot.
        ("markitdown", "", "xberg"),
        ("markitdown", "http://localhost:8002", "xberg"),
    ],
)
def test_processor_selection(configured, kreuzberg_url, expected):
    settings = DatastoreSettings(
        document_processor=configured, kreuzberg_url=kreuzberg_url
    )
    assert settings.effective_document_processor() == expected
