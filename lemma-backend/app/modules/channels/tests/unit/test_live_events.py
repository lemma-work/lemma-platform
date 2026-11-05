"""The ``channel`` topic's events as each reader reads them: cursors, kinds,
withholding -- the REST mapper, encoded once per kind of reader."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.modules.channels.domain.entities import (
    Channel,
    ChannelKind,
    ChannelMessage,
    Principal,
    Sender,
    Visibility,
)
from app.modules.channels.services.live.events import (
    LinesView,
    cursor_of,
    parse_cursor,
)

pytestmark = pytest.mark.unit

_AT = datetime(2026, 10, 19, 9, 0, tzinfo=timezone.utc)
POD = uuid4()
PRIYA = uuid4()
TOM = uuid4()
LEM = uuid4()
CHANNEL = Channel(
    id=uuid4(),
    pod_id=POD,
    kind=ChannelKind.GROUP,
    visibility=Visibility.PUBLIC,
    created_at=_AT,
    last_line_at=_AT,
)


def _line(sender: Sender, *, seq: int, text: str = "…") -> ChannelMessage:
    return ChannelMessage(
        id=uuid4(),
        channel_id=CHANNEL.id,
        pod_id=POD,
        seq=seq,
        sender=sender,
        text=text,
        created_at=_AT,
    )


def test_a_cursor_is_the_change_and_the_newest_line() -> None:
    assert parse_cursor(cursor_of(12, 4)) == (12, 4)
    for foreign in (None, "", "12", "a:b", "1712-0", "-1:2"):
        assert parse_cursor(foreign) is None


def test_lines_new_to_the_reader_are_created_and_older_ones_edited() -> None:
    old = _line(Sender.member(PRIYA), seq=3, text="fixed")
    new = _line(Sender.member(PRIYA), seq=5, text="new")
    view = LinesView(CHANNEL, [old, new], names={}, agents={}, since_seq=4)
    kinds = [json.loads(event)["type"] for event in view.events_for(TOM)]
    assert kinds == ["line.edited", "line.created"]


def test_an_answer_made_with_one_members_access_is_withheld_from_the_rest() -> None:
    answer = _line(
        Sender.agent(LEM, on_behalf_of=Principal.member(PRIYA)),
        seq=7,
        text="made with Priya's access",
    )
    view = LinesView(CHANNEL, [answer], names={}, agents={}, since_seq=6)
    (hers,) = [json.loads(event)["message"] for event in view.events_for(PRIYA)]
    (his,) = [json.loads(event)["message"] for event in view.events_for(TOM)]
    assert hers["text"] == "made with Priya's access" and not hers["withheld"]
    assert his["withheld"] and his["text"] is None and his["parts"] == []
    # Encoded once per kind of reader, not once per reader.
    assert view.events_for(uuid4()) == view.events_for(TOM)
