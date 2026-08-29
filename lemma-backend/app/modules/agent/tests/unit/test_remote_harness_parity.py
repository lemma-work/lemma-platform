"""What a remote harness is told about a user's message.

The in-process harness assembles everything the surface knows about a turn --
who sent it, what it quotes, which channel it came from, the files attached.
The Agent Host path read `message.text` and little else, so the same Slack
thread answered by a Codex or Claude Code host arrived as bare text: no sender,
no referent for a quoted reply, and no sign of the three files already sitting
in the datastore. It read as though the agent had ignored the attachment.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent.domain.entities import Message
from app.modules.agent.domain.value_objects import MessageKind, MessageRole
from app.modules.agent.infrastructure.harnesses.agent_host.tool_payload import (
    _MAX_TOOL_STRING_CHARACTERS,
    bounded_tool_value,
)
from app.modules.agent.infrastructure.harnesses.remote_payload import _message_text

pytestmark = pytest.mark.unit


def _user_message(**metadata) -> Message:
    return Message(
        conversation_id=uuid4(),
        sequence=0,
        role=MessageRole.USER.value,
        kind=MessageKind.TEXT,
        text="this one is wrong",
        metadata=metadata,
    )


class TestTheRemotePathSeesTheWholeTurn:
    def test_the_sender_is_named(self) -> None:
        text = _message_text(
            _user_message(surface_platform="SLACK", sender_display_name="Priya")
        )

        assert "Priya" in text

    def test_a_quoted_reply_keeps_its_referent(self) -> None:
        """Without it, "this one is wrong" is a pronoun about nothing."""
        text = _message_text(
            _user_message(
                surface_platform="SLACK",
                quoted_message={"text": "the Q3 figure is 41%", "author": "Priya"},
            )
        )

        assert "41%" in text

    def test_the_channel_context_survives(self) -> None:
        text = _message_text(
            _user_message(
                surface_platform="SLACK",
                channel_context=[{"text": "ship on the 14th", "author": "Deepak"}],
            )
        )

        assert "ship on the 14th" in text

    def test_attachments_are_still_reported(self) -> None:
        text = _message_text(_user_message(attachments=["invoice.pdf"]))

        assert "invoice.pdf" in text

    def test_the_message_body_is_never_lost(self) -> None:
        text = _message_text(_user_message(surface_platform="SLACK"))

        assert "this one is wrong" in text

    def test_a_plain_message_still_renders(self) -> None:
        assert "this one is wrong" in _message_text(_user_message())


class TestAgentHostTruncatesRatherThanDiscards:
    """This payload is the persisted transcript, so a `carries_history=True`
    resume replayed the placeholder where the file the agent read used to be."""

    def test_an_oversized_string_keeps_its_head(self) -> None:
        value = "IMPORTANT-HEADER " + "x" * (_MAX_TOOL_STRING_CHARACTERS * 2)

        result = bounded_tool_value(value)

        assert result["truncated"] is True
        assert result["preview"].startswith("IMPORTANT-HEADER")

    def test_the_original_size_is_reported(self) -> None:
        value = "x" * (_MAX_TOOL_STRING_CHARACTERS + 1)

        assert bounded_tool_value(value)["character_count"] == len(value)

    def test_a_string_just_under_the_limit_is_untouched(self) -> None:
        value = "x" * _MAX_TOOL_STRING_CHARACTERS

        assert bounded_tool_value(value) == value
