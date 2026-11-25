"""What a report holds, and what the team's email says about it."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.reports.domain.reports import (
    MAX_EXCERPT_CHARS,
    AuthorKind,
    ExcerptLine,
    NewReport,
    ReportPlace,
    ReportReason,
    ReportStatus,
    clean_note,
)
from app.modules.reports.events.report_email import ReportNames, render_report_email
from app.modules.reports.infrastructure.repository import StoredReport

pytestmark = pytest.mark.unit

_AT = datetime(2026, 11, 23, 10, 42, tzinfo=timezone.utc)


def _report(**changes: object) -> StoredReport:
    fields: dict[str, object] = {
        "id": uuid4(),
        "created_at": _AT,
        "updated_at": _AT,
        "status": ReportStatus.OPEN,
        "organization_id": uuid4(),
        "pod_id": uuid4(),
        "reporter_user_id": uuid4(),
        "place": ReportPlace.CONVERSATION,
        "conversation_id": uuid4(),
        "message_id": uuid4(),
        "place_title": "Reply to Harbor",
        "author_kind": AuthorKind.AGENT,
        "author_id": uuid4(),
        "author_name": "Support",
        "agent_run_id": uuid4(),
        "reason": ReportReason.OFFENSIVE,
        "note": "It insults the customer.",
        "excerpt": (
            ExcerptLine.of(who="Priya", text="Draft a reply to Harbor", at=_AT),
            ExcerptLine.of(
                who="Support", text="<b>Here is a draft</b>", at=_AT, reported=True
            ),
        ),
    }
    fields.update(changes)
    return StoredReport.model_validate(fields)


_NAMES = ReportNames(
    reporter_name="Priya Shah",
    reporter_email="priya@acme.example",
    lemma="Support",
    organization="Acme",
)


def test_a_report_names_exactly_the_place_it_was_read_in() -> None:
    common = {
        "organization_id": None,
        "pod_id": uuid4(),
        "reporter_user_id": uuid4(),
        "message_id": uuid4(),
        "author_kind": AuthorKind.CONTACT,
        "reason": ReportReason.SPAM,
    }
    NewReport(place=ReportPlace.CHANNEL, channel_id=uuid4(), **common)
    with pytest.raises(ValidationError):
        NewReport(place=ReportPlace.CHANNEL, conversation_id=uuid4(), **common)
    with pytest.raises(ValidationError):
        NewReport(
            place=ReportPlace.CONVERSATION,
            conversation_id=uuid4(),
            channel_id=uuid4(),
            **common,
        )


def test_the_copy_keeps_enough_of_a_long_line_and_says_it_was_cut() -> None:
    line = ExcerptLine.of(who="Sam", text="x" * (MAX_EXCERPT_CHARS * 3), at=_AT)
    assert len(line.text) == MAX_EXCERPT_CHARS
    assert line.text.endswith("…")


def test_words_that_say_nothing_are_no_words() -> None:
    assert clean_note(None) is None
    assert clean_note("  \x00 ") is None
    assert clean_note("  It was rude. ") == "It was rude."


def test_the_email_says_who_reported_what_and_why_with_the_copy() -> None:
    report = _report()
    subject, email = render_report_email(report, _NAMES)
    assert subject == "Report: offensive or hateful · a reply from Support · Acme"
    for said in (
        "Priya Shah reported a reply from Support.",
        "priya@acme.example",
        "It insults the customer.",
        f"run {report.agent_run_id}",
        f"report {report.id}",
        "the reported message",
    ):
        assert said in email.text
    # What a person or a model wrote is text in the email, never markup.
    assert "<b>Here is a draft</b>" not in email.html
    assert "&lt;b&gt;Here is a draft&lt;/b&gt;" in email.html


def test_an_outsider_nobody_vouched_for_is_said_to_be_one() -> None:
    report = _report(
        place=ReportPlace.CHANNEL,
        conversation_id=None,
        channel_id=uuid4(),
        place_title="Acme × Harbor",
        author_kind=AuthorKind.CONTACT,
        author_id=None,
        author_name="Sam",
        agent_run_id=None,
        reason=ReportReason.SPAM,
        note=None,
    )
    subject, email = render_report_email(report, _NAMES)
    assert subject == "Report: spam or a scam · a message from Sam · Acme"
    assert "Sam, a contact, not vouched for by its platform" in email.text
    assert "The shared chat Acme × Harbor" in email.text
