"""Editing a Telegram message is not sending a new one."""

from __future__ import annotations

import pytest

from app.modules.agent_surfaces.platforms.telegram.parser import (
    TelegramMessageParser,
)

pytestmark = pytest.mark.unit


def _message() -> dict:
    return {
        "message_id": 5,
        "date": 1700000000,
        "chat": {"id": 999, "type": "private"},
        "from": {"id": 999, "is_bot": False, "first_name": "Ada"},
        "text": "what is on my calendar?",
    }


def test_a_message_starts_a_turn():
    event = TelegramMessageParser().parse({"update_id": 1, "message": _message()})

    assert event is not None
    assert event.external_message_id == "5"


def test_an_edit_of_it_starts_none():
    """It carries the original's id: dropped inside the dedupe window, answered
    a second time after it."""
    edited = {**_message(), "text": "what is on my calendar today?", "edit_date": 1}

    assert (
        TelegramMessageParser().parse({"update_id": 2, "edited_message": edited})
        is None
    )
