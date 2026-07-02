from __future__ import annotations

import json

import pytest

from lemma_pod_bundle.jsonc import _strip_trailing_commas, loads_jsonc, strip_jsonc


def test_strip_jsonc_removes_line_comments():
    text = '{\n  "a": 1 // trailing note\n}\n'
    assert json.loads(strip_jsonc(text)) == {"a": 1}


def test_strip_jsonc_removes_block_comments():
    text = '{ /* block\n comment */ "a": 1 }'
    assert json.loads(strip_jsonc(text)) == {"a": 1}


def test_strip_jsonc_leaves_slashes_inside_strings():
    text = '{"url": "https://example.com//path", "note": "a /* not a comment */ b"}'
    assert json.loads(strip_jsonc(text)) == {
        "url": "https://example.com//path",
        "note": "a /* not a comment */ b",
    }


def test_strip_jsonc_handles_escaped_quotes_in_strings():
    text = '{"a": "say \\"hi\\" // still a string"}'
    assert json.loads(strip_jsonc(text)) == {"a": 'say "hi" // still a string'}


def test_strip_jsonc_preserves_byte_offsets():
    text = '{"a": 1} // note'
    stripped = strip_jsonc(text)
    assert len(stripped) == len(text)
    # Newlines inside block comments are preserved so line numbers stay accurate.
    text2 = '{ /* one\ntwo */ "a": 1 }'
    assert strip_jsonc(text2).count("\n") == text2.count("\n")


def test_strip_trailing_commas_object_and_array():
    text = '{"a": [1, 2, ], "b": {"c": 3, }, }'
    assert json.loads(_strip_trailing_commas(text)) == {"a": [1, 2], "b": {"c": 3}}


def test_strip_trailing_commas_keeps_length_and_string_commas():
    text = '{"a": "1,2,]", "b": [1, ],}'
    stripped = _strip_trailing_commas(text)
    assert len(stripped) == len(text)
    assert json.loads(stripped) == {"a": "1,2,]", "b": [1]}


def test_loads_jsonc_combined():
    text = """
    {
      // config for the widget
      "name": "widget", /* inline */
      "values": [1, 2, 3,],
    }
    """
    assert loads_jsonc(text) == {"name": "widget", "values": [1, 2, 3]}


def test_loads_jsonc_plain_json_passthrough():
    assert loads_jsonc('{"a": 1}') == {"a": 1}
    assert loads_jsonc("[]") == []


def test_loads_jsonc_invalid_still_raises():
    with pytest.raises(json.JSONDecodeError):
        loads_jsonc('{"a": }')
