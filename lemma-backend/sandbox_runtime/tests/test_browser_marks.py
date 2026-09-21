"""The one fact about a login that cannot be read back off the profile.

Why this file exists at all is the measurement in `marks.py`: on a real
profile, `api.lemma.work` held two HttpOnly session cookies belonging to a
signed-in person and `youtube.com` held six HttpOnly cookies belonging to
nobody, and every flag CDP reports said the same thing about both. So the
list is written down when somebody states it, and these tests are about that
file behaving like a file rather than about any guess.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sandbox_runtime.browser_relay import marks


@pytest.fixture
def profile(monkeypatch, tmp_path: Path) -> Path:
    """The marks file somewhere writable, beside a profile directory.

    Beside, not inside: quiesce empties the four lock files *within*
    `profile/`, and a mark has to outlive that the same way a cookie does.
    """
    (tmp_path / "profile").mkdir()
    where = tmp_path / "signed-in.json"
    monkeypatch.setattr(marks, "MARKS_FILE", where)
    return where


def test_nothing_recorded_reads_as_nothing(profile: Path) -> None:
    assert marks.signed_in_sites() == []
    assert not profile.exists(), "reading must not create the file"


def test_a_site_is_remembered_once_however_often_it_is_said(profile: Path) -> None:
    """Answering a sign-in twice is one sign-in. A person who reloads the
    page and presses the button again has not signed in to two sites."""
    marks.mark_signed_in("lemma.work")
    marks.mark_signed_in("LEMMA.work")
    marks.mark_signed_in(".lemma.work")

    assert marks.signed_in_sites() == ["lemma.work"]


def test_signing_out_takes_the_mark_with_it(profile: Path) -> None:
    marks.mark_signed_in("lemma.work")
    marks.mark_signed_in("example.com")

    assert marks.forget_marks(["lemma.work"]) == ["example.com"]
    assert marks.signed_in_sites() == ["example.com"]


def test_forgetting_a_site_that_was_never_marked_is_not_an_error(profile: Path) -> None:
    """The controller drops marks beside cookies without checking first, and
    a site whose cookies were there but whose mark never was is ordinary."""
    marks.mark_signed_in("lemma.work")

    assert marks.forget_marks(["nobody.example"]) == ["lemma.work"]


def test_nothing_but_names_is_ever_written(profile: Path) -> None:
    """The guard on the whole idea. This file sits on a disk the agent's own
    shell can read, so it has to be worth nothing to whoever reads it -- the
    same reason no cookie value crosses the sandbox boundary."""
    marks.mark_signed_in("lemma.work")

    written = json.loads(profile.read_text())
    assert written == {"sites": ["lemma.work"]}


def test_a_corrupt_file_reads_as_empty_rather_than_failing(profile: Path) -> None:
    """A cache of labels must not be able to break the listing. The cost of
    being wrong here is a missing "signed in" badge; the cost of raising is a
    settings page that cannot show somebody their logins at all."""
    profile.write_text("{ this is not json")

    assert marks.signed_in_sites() == []
    # And it repairs itself on the next write rather than staying broken.
    assert marks.mark_signed_in("lemma.work") == ["lemma.work"]


def test_a_file_of_the_wrong_shape_is_also_just_empty(profile: Path) -> None:
    profile.write_text(json.dumps(["lemma.work"]))

    assert marks.signed_in_sites() == []


def test_a_write_leaves_no_temporary_file_behind(profile: Path) -> None:
    """Written to a neighbour and renamed, so a reader never sees half a
    file -- which is only true if the neighbour is in the same directory and
    does not survive."""
    marks.mark_signed_in("lemma.work")

    assert [p.name for p in profile.parent.iterdir() if p.is_file()] == [profile.name]
