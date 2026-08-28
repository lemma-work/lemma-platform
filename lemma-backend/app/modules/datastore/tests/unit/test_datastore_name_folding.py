"""A file is found by matching its path, so the path has to be retypable.

Nobody types a datastore path from scratch: they read it out of a listing, or a
model reads it out of a prompt block, and then repeats it. A character that
renders as a space but is not one therefore makes the file permanently
unreachable -- the row says one thing, every request says another, and nothing
in the 404 hints that they differ, because they render identically.

The case these tests are built from is real. A macOS screenshot attached to a
conversation on dev was stored as
``Screenshot 2026-08-28 at 6.37.26<U+202F>PM.png`` -- a NARROW NO-BREAK SPACE
before the meridiem, which is how macOS has named screenshots since Ventura. The
agent asked for it with an ordinary space, got a 404 whose message echoed back
what looked like the same path, and spent twelve tool calls trying to escape a
space that was never the problem. It never did read the screenshot.

Every character under test is written as an escape rather than as itself. A test
about characters nobody can see is the last place to put one where nobody can
see it -- and a reviewer has to be able to tell which one each case is about.
"""

from __future__ import annotations

import pytest

from app.modules.datastore.domain.errors import DatastoreValidationError
from app.modules.datastore.services.files.paths import (
    normalize_datastore_name,
    normalize_datastore_path,
)

#: Exactly what macOS wrote, byte for byte.
A_MACOS_SCREENSHOT = "Screenshot 2026-08-28 at 6.37.26\u202fPM.png"

#: What a person -- or a model -- types when they read that name back.
AS_ANYBODY_WOULD_TYPE_IT = "Screenshot 2026-08-28 at 6.37.26 PM.png"


def test_a_macos_screenshot_is_stored_under_the_name_people_can_ask_for():
    assert normalize_datastore_name(A_MACOS_SCREENSHOT) == AS_ANYBODY_WOULD_TYPE_IT


def test_the_two_spellings_of_that_name_are_one_name():
    """The property that matters: write it one way, read it the other."""
    assert normalize_datastore_name(A_MACOS_SCREENSHOT) == normalize_datastore_name(
        AS_ANYBODY_WOULD_TYPE_IT
    )


def test_a_path_folds_every_segment_not_just_the_last():
    folded = normalize_datastore_path(f"/me/holiday\u00a0photos/{A_MACOS_SCREENSHOT}")

    assert folded == f"/me/holiday photos/{AS_ANYBODY_WOULD_TYPE_IT}"


@pytest.mark.parametrize(
    ("raw", "what"),
    [
        ("a\u00a0b.png", "NO-BREAK SPACE"),
        ("a\u202fb.png", "NARROW NO-BREAK SPACE"),
        ("a\u2007b.png", "FIGURE SPACE"),
        ("a\u2009b.png", "THIN SPACE"),
        ("a\u205fb.png", "MEDIUM MATHEMATICAL SPACE"),
        ("a\u3000b.png", "IDEOGRAPHIC SPACE"),
    ],
)
def test_every_space_that_is_not_a_space_becomes_one(raw, what):
    assert normalize_datastore_name(raw) == "a b.png", what


@pytest.mark.parametrize(
    ("raw", "what"),
    [
        ("re\u200bport.csv", "ZERO WIDTH SPACE"),
        ("re\u200cport.csv", "ZERO WIDTH NON-JOINER"),
        ("re\u200dport.csv", "ZERO WIDTH JOINER"),
        ("re\ufeffport.csv", "BYTE ORDER MARK"),
        ("re\u2060port.csv", "WORD JOINER"),
        ("re\u202eport.csv", "RIGHT-TO-LEFT OVERRIDE"),
    ],
)
def test_an_invisible_character_is_dropped_rather_than_kept(raw, what):
    """Invisible means indistinguishable on screen, so it cannot be retyped."""
    assert normalize_datastore_name(raw) == "report.csv", what


def test_a_decomposed_name_and_a_composed_one_are_the_same_file():
    """macOS hands over NFD; almost everything else composes. Same name."""
    decomposed = "cafe\u0301.png"
    composed = "café.png"

    assert normalize_datastore_name(decomposed) == normalize_datastore_name(composed)


@pytest.mark.parametrize(
    "name",
    [
        "My Report (v2) — final.pdf",
        "報告書.pdf",
        "Ünterlagen_2026.xlsx",
        "quarter.csv",
        "a b.png",
        "IMG_0042.HEIC",
    ],
)
def test_a_name_somebody_can_already_type_is_left_exactly_alone(name):
    """Not a slug. Spaces, case, punctuation and non-Latin scripts all survive:
    the name belongs to the person, and the only goal is that what they see is
    what they can ask for."""
    assert normalize_datastore_name(name) == name


def test_a_name_that_is_only_invisible_characters_is_still_refused():
    """Folding must not turn "unusable" into "empty and quietly accepted"."""
    with pytest.raises(DatastoreValidationError):
        normalize_datastore_name("\u200b\u200c\ufeff")


def test_folding_cannot_smuggle_a_separator_into_a_name():
    with pytest.raises(DatastoreValidationError):
        normalize_datastore_name("a\u200b/\u200bb.png")
