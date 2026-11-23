from __future__ import annotations

import io
import sys
from unittest.mock import MagicMock

import pytest
import typer
from lemma_cli.cli_core.select import (
    _initial_index,
    _render_arrow_selector,
    _select_with_arrows,
    filter_items,
    item_label,
    select_from_items,
)


def test_item_label() -> None:
    assert item_label({"name": "Acme", "slug": "acme"}) == "Acme (acme)"
    assert item_label({"name": "Acme", "slug": "Acme"}) == "Acme"
    assert item_label({"id": "123"}) == "123"
    assert item_label({"title": "Standup", "id": "conv-1"}) == "Standup (conv-1)"
    assert item_label({}, fallback="none") == "none"


def test_filter_items_empty_query() -> None:
    items = [
        {"name": "Alpha", "slug": "alpha"},
        {"name": "Beta", "slug": "beta"},
    ]
    assert filter_items(items, "") == items


def test_filter_items_by_name() -> None:
    items = [
        {"name": "Alpha Corp", "slug": "corp-1"},
        {"name": "Beta Inc", "slug": "inc-2"},
        {"name": "Gamma LLC", "slug": "llc-3"},
    ]
    assert filter_items(items, "alpha") == [items[0]]
    assert filter_items(items, "BETA") == [items[1]]
    assert filter_items(items, "corp") == [items[0]]


def test_filter_items_by_slug() -> None:
    items = [
        {"name": "First", "slug": "apple-pod"},
        {"name": "Second", "slug": "banana-pod"},
    ]
    assert filter_items(items, "apple") == [items[0]]
    assert filter_items(items, "BANANA") == [items[1]]
    assert filter_items(items, "-pod") == items


def test_filter_items_by_title_and_id_for_conversations() -> None:
    # Conversation rows carry 'title' and 'id', not 'name' or 'slug'
    items = [
        {"id": "conv-fund-1", "title": "Q3 Fund Raising"},
        {"id": "conv-eng-2", "title": "Sprint Planning"},
    ]
    assert filter_items(items, "fund") == [items[0]]
    assert filter_items(items, "q3") == [items[0]]
    assert filter_items(items, "sprint") == [items[1]]
    assert filter_items(items, "conv-eng") == [items[1]]


def test_filter_items_no_matches() -> None:
    items = [
        {"name": "Alpha", "slug": "alpha"},
        {"name": "Beta", "slug": "beta"},
    ]
    assert filter_items(items, "xyz") == []


def test_filter_items_handles_missing_fields() -> None:
    items = [
        {"id": "item-1"},
        {"name": None, "slug": None},
        {"name": "Valid", "slug": "valid"},
    ]
    assert filter_items(items, "valid") == [items[2]]
    assert filter_items(items, "item-1") == [items[0]]


def test_render_arrow_selector_basic() -> None:
    buf = io.StringIO()
    items = [
        {"name": "Alpha", "slug": "alpha"},
        {"name": "Beta", "slug": "beta"},
    ]
    _render_arrow_selector(items, label="pod", selected=0, output=buf)
    content = buf.getvalue()
    assert "Select pod (type to filter, up/down, enter)" in content
    assert "> Alpha (alpha)" in content
    assert "  Beta (beta)" in content
    assert "\033[7m" in content


def test_render_arrow_selector_with_filter() -> None:
    buf = io.StringIO()
    items = [{"name": "Alpha", "slug": "alpha"}]
    _render_arrow_selector(items, label="pod", selected=0, filter_text="alp", output=buf)
    content = buf.getvalue()
    assert "Filter: alp" in content
    assert "> Alpha (alpha)" in content


def test_render_arrow_selector_no_matches() -> None:
    buf = io.StringIO()
    _render_arrow_selector([], label="pod", selected=0, filter_text="nonexistent", output=buf)
    content = buf.getvalue()
    assert "Filter: nonexistent" in content
    assert "(no matches)" in content


def test_initial_index() -> None:
    items = [
        {"id": "a", "name": "A"},
        {"id": "b", "name": "B"},
    ]
    assert _initial_index(items, None) == 0
    assert _initial_index(items, "b") == 1
    assert _initial_index(items, "c") == 0


class FakeStdin:
    def __init__(self, sequence: list[str]) -> None:
        self.sequence = list(sequence)
        self.index = 0

    def fileno(self) -> int:
        return 0

    def read(self, n: int) -> str:
        if self.index >= len(self.sequence):
            return "\n"
        val = self.sequence[self.index]
        self.index += 1
        return val


def test_select_with_arrows_direct_enter(monkeypatch: pytest.MonkeyPatch) -> None:
    items = [
        {"name": "Alpha", "slug": "alpha"},
        {"name": "Beta", "slug": "beta"},
    ]
    fake_termios = MagicMock()
    fake_tty = MagicMock()
    monkeypatch.setitem(sys.modules, "termios", fake_termios)
    monkeypatch.setitem(sys.modules, "tty", fake_tty)
    monkeypatch.setattr(sys, "stdin", FakeStdin(["\n"]))

    selected = _select_with_arrows(items, label="pod", current_id=None)
    assert selected == items[0]


def test_select_with_arrows_filter_and_select(monkeypatch: pytest.MonkeyPatch) -> None:
    items = [
        {"name": "Alpha", "slug": "alpha"},
        {"name": "Beta", "slug": "beta"},
        {"name": "Gamma", "slug": "gamma"},
    ]
    fake_termios = MagicMock()
    fake_tty = MagicMock()
    monkeypatch.setitem(sys.modules, "termios", fake_termios)
    monkeypatch.setitem(sys.modules, "tty", fake_tty)
    # Type 'b', then press enter
    monkeypatch.setattr(sys, "stdin", FakeStdin(["b", "\n"]))

    selected = _select_with_arrows(items, label="pod", current_id=None)
    assert selected == items[1]


def test_select_with_arrows_backspace(monkeypatch: pytest.MonkeyPatch) -> None:
    items = [
        {"name": "Alpha", "slug": "alpha"},
        {"name": "Beta", "slug": "beta"},
    ]
    fake_termios = MagicMock()
    fake_tty = MagicMock()
    monkeypatch.setitem(sys.modules, "termios", fake_termios)
    monkeypatch.setitem(sys.modules, "tty", fake_tty)
    # Type 'z' (no match), backspace '\x7f', type 'b', enter
    monkeypatch.setattr(sys, "stdin", FakeStdin(["z", "\x7f", "b", "\n"]))

    selected = _select_with_arrows(items, label="pod", current_id=None)
    assert selected == items[1]


def test_select_with_arrows_navigation(monkeypatch: pytest.MonkeyPatch) -> None:
    items = [
        {"name": "Alpha", "slug": "alpha"},
        {"name": "Beta", "slug": "beta"},
    ]
    fake_termios = MagicMock()
    fake_tty = MagicMock()
    monkeypatch.setitem(sys.modules, "termios", fake_termios)
    monkeypatch.setitem(sys.modules, "tty", fake_tty)
    # Down arrow: '\x1b', '[', 'B', then Enter
    monkeypatch.setattr(sys, "stdin", FakeStdin(["\x1b", "[", "B", "\n"]))

    selected = _select_with_arrows(items, label="pod", current_id=None)
    assert selected == items[1]


def test_select_with_arrows_drains_delete_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # Delete key sends ESC [ 3 ~
    items = [
        {"name": "Alpha", "slug": "alpha"},
        {"name": "Beta", "slug": "beta"},
    ]
    fake_termios = MagicMock()
    fake_tty = MagicMock()
    monkeypatch.setitem(sys.modules, "termios", fake_termios)
    monkeypatch.setitem(sys.modules, "tty", fake_tty)
    # User presses Delete (\x1b, [, 3, ~), then types 'b', then Enter
    # The trailing '~' MUST NOT be appended to filter_text
    monkeypatch.setattr(sys, "stdin", FakeStdin(["\x1b", "[", "3", "~", "b", "\n"]))

    selected = _select_with_arrows(items, label="pod", current_id=None)
    assert selected == items[1]


def test_select_with_arrows_drains_home_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # Home key sends ESC [ 1 ~ or ESC [ H
    items = [
        {"name": "Alpha", "slug": "alpha"},
        {"name": "Beta", "slug": "beta"},
    ]
    fake_termios = MagicMock()
    fake_tty = MagicMock()
    monkeypatch.setitem(sys.modules, "termios", fake_termios)
    monkeypatch.setitem(sys.modules, "tty", fake_tty)
    # User presses Home (\x1b, [, 1, ~), then Enter
    monkeypatch.setattr(sys, "stdin", FakeStdin(["\x1b", "[", "1", "~", "\n"]))

    selected = _select_with_arrows(items, label="pod", current_id=None)
    assert selected == items[0]


def test_select_with_arrows_preserves_selection_on_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items = [
        {"name": "Alpha", "slug": "alpha"},
        {"name": "Beta One", "slug": "beta-1"},
        {"name": "Beta Two", "slug": "beta-2"},
    ]
    fake_termios = MagicMock()
    fake_tty = MagicMock()
    monkeypatch.setitem(sys.modules, "termios", fake_termios)
    monkeypatch.setitem(sys.modules, "tty", fake_tty)
    # Down arrow to Beta One (\x1b, [, B), type 'b' (Beta One still matched), Enter
    monkeypatch.setattr(sys, "stdin", FakeStdin(["\x1b", "[", "B", "b", "\n"]))

    selected = _select_with_arrows(items, label="pod", current_id=None)
    assert selected == items[1]


def test_select_with_arrows_exit_ctrl_c(monkeypatch: pytest.MonkeyPatch) -> None:
    items = [{"name": "Alpha"}]
    fake_termios = MagicMock()
    fake_tty = MagicMock()
    monkeypatch.setitem(sys.modules, "termios", fake_termios)
    monkeypatch.setitem(sys.modules, "tty", fake_tty)
    monkeypatch.setattr(sys, "stdin", FakeStdin(["\x03"]))

    with pytest.raises(typer.Exit):
        _select_with_arrows(items, label="pod", current_id=None)


def test_select_from_items_single_and_empty() -> None:
    with pytest.raises(typer.Exit):
        select_from_items([], label="pod")

    single = {"name": "Only"}
    assert select_from_items([single], label="pod") == single
