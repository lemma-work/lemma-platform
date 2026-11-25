from __future__ import annotations

import os
import sys

import pytest

from lemma_cli.cli_core import select as sel

ITEMS = [
    {"id": "1", "name": "Alpha", "slug": "alpha-team"},
    {"id": "2", "name": "Beta", "slug": "beta"},
    {"id": "3", "name": "Gamma Ray", "slug": "gamma"},
]


def test_filter_items_empty_text_keeps_everything():
    assert sel.filter_items(ITEMS, "") == ITEMS


def test_filter_items_ignores_case():
    assert sel.filter_items(ITEMS, "ALPHA") == [ITEMS[0]]


def test_filter_items_matches_slug_shown_in_label():
    assert sel.filter_items(ITEMS, "-team") == [ITEMS[0]]


def test_filter_items_matches_inside_the_name():
    assert sel.filter_items(ITEMS, "ray") == [ITEMS[2]]


def test_filter_items_no_match_is_empty():
    assert sel.filter_items(ITEMS, "zzz") == []


def test_filter_items_does_not_change_the_input():
    sel.filter_items(ITEMS, "beta")
    assert len(ITEMS) == 3


def test_initial_index_uses_current_id():
    assert sel._initial_index(ITEMS, "2") == 1
    assert sel._initial_index(ITEMS, "missing") == 0
    assert sel._initial_index(ITEMS, None) == 0


def test_typing_adds_to_filter_and_resets_selection():
    state = sel._MenuState(selected=2)
    state, done = sel._apply_key("b", state, 3)
    assert (state.filter_text, state.selected, done) == ("b", 0, None)


def test_q_is_a_letter_not_cancel():
    state, done = sel._apply_key("q", sel._MenuState(), 3)
    assert (state.filter_text, done) == ("q", None)


def test_non_ascii_letter_is_typed():
    state, _ = sel._apply_key("é", sel._MenuState(), 3)
    assert state.filter_text == "é"


@pytest.mark.parametrize("key", ["\x7f", "\x08"])
def test_backspace_removes_last_letter(key):
    state, done = sel._apply_key(key, sel._MenuState("be"), 1)
    assert (state.filter_text, done) == ("b", None)


def test_backspace_on_empty_filter_does_nothing():
    state, done = sel._apply_key("\x7f", sel._MenuState(), 3)
    assert (state.filter_text, done) == ("", None)


@pytest.mark.parametrize(
    ("key", "selected", "expected"),
    [("\x1b[B", 0, 1), ("\x1b[B", 2, 0), ("\x1b[A", 0, 2), ("\x1b[A", 2, 1)],
)
def test_arrows_move_and_wrap(key, selected, expected):
    state, done = sel._apply_key(key, sel._MenuState(selected=selected), 3)
    assert (state.selected, done) == (expected, None)


@pytest.mark.parametrize("key", ["\x1b[A", "\x1b[B"])
def test_arrows_do_nothing_without_matches(key):
    state, done = sel._apply_key(key, sel._MenuState("zzz"), 0)
    assert (state.selected, done) == (0, None)


def test_enter_picks_when_something_is_visible():
    assert sel._apply_key("\r", sel._MenuState(selected=1), 2)[1] == "pick"
    assert sel._apply_key("\n", sel._MenuState(selected=1), 2)[1] == "pick"


def test_enter_does_nothing_without_matches():
    assert sel._apply_key("\r", sel._MenuState("zzz"), 0)[1] is None


@pytest.mark.parametrize("key", ["\x1b", "\x03", "\x04"])
def test_cancel_keys(key):
    assert sel._apply_key(key, sel._MenuState("be"), 1)[1] == "cancel"


def test_unknown_escape_sequence_is_ignored():
    state, done = sel._apply_key("\x1b[C", sel._MenuState("b"), 1)
    assert (state.filter_text, done) == ("b", None)


def _pipe_with(data: bytes) -> int:
    r, w = os.pipe()
    os.write(w, data)
    os.close(w)
    return r


needs_posix = pytest.mark.skipif(
    sys.platform == "win32", reason="select() on a pipe is POSIX-only"
)


@needs_posix
def test_read_key_arrow_sequence():
    r = _pipe_with(b"\x1b[A")
    try:
        assert sel._read_key(r) == "\x1b[A"
    finally:
        os.close(r)


@needs_posix
def test_read_key_lone_escape_does_not_block():
    r, w = os.pipe()  # writer stays open: a blocking read(2) would hang here
    try:
        os.write(w, b"\x1b")
        assert sel._read_key(r) == "\x1b"
    finally:
        os.close(r)
        os.close(w)


@needs_posix
def test_read_key_reads_a_whole_utf8_character():
    r = _pipe_with("é".encode())
    try:
        assert sel._read_key(r) == "é"
    finally:
        os.close(r)


def test_select_by_number_picks_item(monkeypatch):
    monkeypatch.setattr(sel.typer, "prompt", lambda *a, **k: 2)
    assert sel._select_by_number(ITEMS, label="pod") == ITEMS[1]
