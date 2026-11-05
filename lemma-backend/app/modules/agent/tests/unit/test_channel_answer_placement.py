"""Where an agent's answer to a line lands: in the main timeline, pointing at
the line, wherever the chat reads as one -- a member's own channel, a group
made in Lemma, a WhatsApp or Telegram group -- and in the line's thread only
where the platform threads a bot's answers, or the line was in a thread."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from app.modules.agent.services.channel_answer_lines import where_the_answer_goes
from app.modules.channels.contracts import ChannelKind


def _channel(kind: ChannelKind) -> SimpleNamespace:
    return SimpleNamespace(kind=kind)


def _line(*, thread_root_id=None, place_id=None) -> SimpleNamespace:
    return SimpleNamespace(id=uuid4(), thread_root_id=thread_root_id, place_id=place_id)


def test_a_group_made_in_lemma_answers_in_the_main_timeline():
    line = _line()
    assert where_the_answer_goes(_channel(ChannelKind.GROUP), line) == (None, None)


def test_a_whatsapp_or_telegram_group_answers_in_the_main_timeline_and_carries_it_there():
    place = uuid4()
    line = _line(place_id=place)
    assert where_the_answer_goes(
        _channel(ChannelKind.GROUP), line, place_threads=False
    ) == (None, place)


def test_a_slack_or_teams_channel_keeps_its_habit_of_answering_in_a_thread():
    place = uuid4()
    line = _line(place_id=place)
    assert where_the_answer_goes(
        _channel(ChannelKind.GROUP), line, place_threads=True
    ) == (line.id, place)


def test_a_line_in_a_thread_is_answered_in_that_thread():
    root, place = uuid4(), uuid4()
    line = _line(thread_root_id=root, place_id=place)
    assert where_the_answer_goes(_channel(ChannelKind.GROUP), line) == (root, place)
    assert where_the_answer_goes(_channel(ChannelKind.DIRECT), line) == (root, None)


def test_a_members_own_channel_answers_in_the_main_timeline():
    line = _line(place_id=uuid4())
    assert where_the_answer_goes(_channel(ChannelKind.DIRECT), line) == (None, None)


def test_a_contacts_channel_keeps_a_members_question_in_its_thread_and_carries_it_nowhere():
    line = _line(place_id=uuid4())
    assert where_the_answer_goes(_channel(ChannelKind.CONTACT), line) == (line.id, None)
