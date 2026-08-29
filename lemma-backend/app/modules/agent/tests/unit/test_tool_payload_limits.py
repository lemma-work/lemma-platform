"""One tool result must not be able to crowd a conversation out of its context.

An unbounded result is not a one-turn cost: it is persisted, replayed on every
later turn, and counted against the model's window each time. Six tools returned
whatever they were handed -- connector responses and operation schemas, a
sandboxed function's output, a sub-agent's whole transcript, an entire skill
file.
"""

from __future__ import annotations

import pytest

from app.modules.agent.tools.tool_payload_limits import (
    DEFAULT_TOOL_PAYLOAD_LIMIT,
    bounded_tool_payload,
)

pytestmark = pytest.mark.unit


class TestBoundedToolPayload:
    def test_an_ordinary_result_passes_through_untouched(self) -> None:
        value = {"rows": [{"id": 1}], "ok": True}

        assert bounded_tool_payload(value) is value

    def test_an_oversized_structure_is_replaced_not_clipped(self) -> None:
        """Half a JSON document is not a smaller document, it is an unparseable
        one -- and the model would treat the fragment as the whole answer."""
        value = {"items": [{"id": index, "blob": "x" * 200} for index in range(1000)]}

        result = bounded_tool_payload(value)

        assert result["truncated"] is True
        assert "items" not in result

    def test_an_oversized_string_keeps_its_head(self) -> None:
        """A schema, a document, or a message thread introduces itself first."""
        value = "SCHEMA-START " + ("y" * (DEFAULT_TOOL_PAYLOAD_LIMIT * 2))

        result = bounded_tool_payload(value)

        assert result["truncated"] is True
        assert result["preview"].startswith("SCHEMA-START")
        assert len(result["preview"]) == DEFAULT_TOOL_PAYLOAD_LIMIT

    def test_the_model_is_told_what_to_do_about_it(self) -> None:
        """A `truncated: true` flag with no instruction just tells the model it
        is stuck."""
        value = ["z" * 400] * 500

        note = bounded_tool_payload(value)["note"]

        assert "narrower" in note or "Narrow" in note

    def test_a_value_exactly_at_the_limit_is_kept(self) -> None:
        """The boundary itself: `<=` keeps it, `<` would clip it."""
        value = "a" * DEFAULT_TOOL_PAYLOAD_LIMIT

        assert bounded_tool_payload(value) == value

    def test_one_character_past_the_limit_is_clipped(self) -> None:
        value = "a" * (DEFAULT_TOOL_PAYLOAD_LIMIT + 1)

        assert bounded_tool_payload(value)["truncated"] is True

    def test_what_it_is_gets_named(self) -> None:
        """`what` distinguishes 'the connector said too much' from 'your
        sub-agent's transcript is too long', which need different responses."""
        value = {"k": "v" * DEFAULT_TOOL_PAYLOAD_LIMIT}

        result = bounded_tool_payload(value, what="connector response")

        assert "connector response" in result["note"]

    def test_a_value_json_cannot_render_is_still_measured(self) -> None:
        """Measured through the fallback, not charged at a bytes repr."""

        class Opaque:
            def __repr__(self) -> str:
                return "o" * (DEFAULT_TOOL_PAYLOAD_LIMIT + 10)

        assert bounded_tool_payload({"x": Opaque()})["truncated"] is True

    def test_a_bytes_payload_is_not_measured_at_its_repr(self) -> None:
        """`default=str` here would render bytes as their escaped repr, so a
        value gets clipped for a size it does not have."""
        assert bounded_tool_payload({"image": b"\xff" * 40_000}) == {
            "image": b"\xff" * 40_000
        }
