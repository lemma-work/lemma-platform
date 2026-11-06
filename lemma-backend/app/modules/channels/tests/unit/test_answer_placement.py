"""Where an answer to a line goes (``domain/placement``): in the main
timeline, pointing at the line, wherever the chat reads as one -- a member's
own channel, a group made in Lemma, a WhatsApp or Telegram group -- and in the
line's thread only where the place keeps threads, or the line was in one. A
line heard on a platform is answered at its place, pointing at it."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from app.modules.channels.domain.entities import ChannelKind, SourceKind
from app.modules.channels.domain.placement import (
    Placement,
    answer_in_lemma,
    answer_to_heard,
    keeps_threads,
)


def _line(*, thread_root_id=None, place_id=None, source_id=None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        thread_root_id=thread_root_id,
        place_id=place_id or source_id,
        source_id=source_id,
    )


def test_only_a_shared_slack_or_teams_place_keeps_threads():
    assert keeps_threads(kind=SourceKind.CHAT, platform="SLACK")
    assert keeps_threads(kind=SourceKind.CHAT, platform="teams")
    assert not keeps_threads(kind=SourceKind.PERSON, platform="SLACK")
    assert not keeps_threads(kind=SourceKind.CHAT, platform="WHATSAPP")
    assert not keeps_threads(kind=SourceKind.CHAT, platform="TELEGRAM")


def test_a_group_made_in_lemma_answers_in_the_main_timeline():
    line = _line()
    assert answer_in_lemma(ChannelKind.GROUP, line) == Placement(None, line.id, None)


def test_a_whatsapp_or_telegram_group_answers_in_the_main_timeline_and_carries_it_there():
    place = uuid4()
    line = _line(place_id=place)
    assert answer_in_lemma(ChannelKind.GROUP, line, place_threads=False) == Placement(
        None, line.id, place
    )


def test_a_slack_or_teams_channel_keeps_its_habit_of_answering_in_a_thread():
    place = uuid4()
    line = _line(place_id=place)
    assert answer_in_lemma(ChannelKind.GROUP, line, place_threads=True) == Placement(
        line.id, line.id, place
    )


def test_a_line_in_a_thread_is_answered_in_that_thread():
    root, place = uuid4(), uuid4()
    line = _line(thread_root_id=root, place_id=place)
    assert answer_in_lemma(ChannelKind.GROUP, line).thread_root_id == root
    assert answer_in_lemma(ChannelKind.DIRECT, line) == Placement(root, line.id, None)


def test_a_members_own_channel_answers_in_the_main_timeline():
    line = _line(place_id=uuid4())
    assert answer_in_lemma(ChannelKind.DIRECT, line) == Placement(None, line.id, None)


def test_a_contacts_channel_keeps_a_members_question_in_its_thread_and_carries_it_nowhere():
    line = _line(place_id=uuid4())
    assert answer_in_lemma(ChannelKind.CONTACT, line) == Placement(
        line.id, line.id, None
    )


def test_a_heard_line_is_answered_at_its_place_pointing_at_it():
    place = uuid4()
    line = _line(source_id=place)
    assert answer_to_heard(line, threads=False, thread_root_id=None) == Placement(
        None, line.id, place
    )


def test_a_heard_line_in_a_threaded_place_is_answered_in_its_thread():
    place, root = uuid4(), uuid4()
    opened = _line(source_id=place)
    # It opened the thread the answer goes into.
    assert answer_to_heard(opened, threads=True, thread_root_id=opened.id) == (
        Placement(opened.id, opened.id, place)
    )
    reply = _line(source_id=place, thread_root_id=root)
    assert answer_to_heard(reply, threads=True, thread_root_id=None).thread_root_id == (
        root
    )


def test_a_heard_line_where_the_place_keeps_no_threads_is_answered_in_the_chat():
    place, root = uuid4(), uuid4()
    line = _line(source_id=place, thread_root_id=root)
    assert answer_to_heard(line, threads=False, thread_root_id=root) == Placement(
        None, line.id, place
    )
