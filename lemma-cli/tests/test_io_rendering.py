from __future__ import annotations

import json
from io import StringIO
from types import SimpleNamespace

from rich.console import Console

import lemma_cli.cli_core.io as io_mod
from lemma_cli.cli_core.io import emit, list_items, to_plain


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _capture_emit(state, data):
    """Call emit() with a temporary Rich console that writes to a StringIO buffer."""
    buffer = StringIO()
    old_console = io_mod.console
    io_mod.console = Console(file=buffer, highlight=False)
    try:
        emit(state, data)
    finally:
        io_mod.console = old_console
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# emit() — JSON output
# ---------------------------------------------------------------------------

def test_emit_json_output_is_valid_json():
    state = SimpleNamespace(output="json", full=False)
    output = _capture_emit(state, {"id": "foo", "name": "bar"})
    parsed = json.loads(output.strip())
    assert parsed["id"] == "foo"


def test_emit_json_output_with_items_list():
    state = SimpleNamespace(output="json", full=False)
    data = {"items": [{"id": "1"}, {"id": "2"}]}
    output = _capture_emit(state, data)
    parsed = json.loads(output.strip())
    assert "items" in parsed
    assert len(parsed["items"]) == 2
    assert parsed["items"][0]["id"] == "1"


# ---------------------------------------------------------------------------
# emit() — pretty output
# ---------------------------------------------------------------------------

def test_emit_pretty_output_dict_shows_keys():
    state = SimpleNamespace(output="pretty", full=False)
    output = _capture_emit(state, {"id": "x", "name": "hello"})
    assert "hello" in output


def test_emit_pretty_output_list_shows_table():
    state = SimpleNamespace(output="pretty", full=False)
    data = {"items": [{"id": "1", "name": "foo"}]}
    output = _capture_emit(state, data)
    assert "foo" in output


# ---------------------------------------------------------------------------
# to_plain()
# ---------------------------------------------------------------------------

def test_to_plain_unwraps_to_dict():
    class Obj:
        def to_dict(self):
            return {"key": "value"}

    result = to_plain(Obj())
    assert result == {"key": "value"}


def test_to_plain_passes_through_primitives():
    assert to_plain("hello") == "hello"
    assert to_plain(42) == 42
    assert to_plain(None) is None


def test_to_plain_handles_nested_list():
    class Obj:
        def __init__(self, val):
            self.val = val

        def to_dict(self):
            return {"val": self.val}

    result = to_plain([Obj("a"), Obj("b")])
    assert result == [{"val": "a"}, {"val": "b"}]


# ---------------------------------------------------------------------------
# list_items()
# ---------------------------------------------------------------------------

def test_list_items_from_dict_with_items_key():
    result = list_items({"items": [{"a": 1}]})
    assert result == [{"a": 1}]


def test_list_items_from_list():
    data = [{"a": 1}, {"b": 2}]
    result = list_items(data)
    assert result == data


def test_list_items_empty_for_non_dict():
    result = list_items("foo")
    assert result == []


# ---------------------------------------------------------------------------
# format_columns() — ENUM options
# ---------------------------------------------------------------------------

_ENUM_TABLE = {
    "name": "tickets",
    "primary_key_column": "id",
    "columns": [
        {"name": "id", "type": "UUID"},
        {
            "name": "status",
            "type": "ENUM",
            "options": ["new", "triaged", "waiting", "closed"],
        },
    ],
}


def test_detail_view_shows_enum_options():
    """An ENUM's whole meaning is its options. They used to be readable only via
    `--output json`, while the docs told QA to read them off this output."""
    state = SimpleNamespace(output="pretty", full=False)
    output = _capture_emit(state, _ENUM_TABLE)
    assert "status:enum(new|triaged|waiting|+1)" in output


def test_full_uncaps_enum_options():
    state = SimpleNamespace(output="pretty", full=True)
    output = _capture_emit(state, _ENUM_TABLE)
    assert "status:enum(new|triaged|waiting|closed)" in output


def test_list_view_keeps_columns_compact():
    """Options belong to the detail view — rendering them in list rows would
    widen the Columns cell of every table with an enum."""
    state = SimpleNamespace(output="pretty", full=False)
    output = _capture_emit(state, {"items": [_ENUM_TABLE]})
    assert "status:enum" in output
    assert "triaged" not in output


def test_column_without_options_is_unchanged():
    state = SimpleNamespace(output="pretty", full=False)
    output = _capture_emit(state, {"name": "t", "columns": [{"name": "n", "type": "TEXT"}]})
    assert "n:text" in output
    assert "n:text(" not in output
