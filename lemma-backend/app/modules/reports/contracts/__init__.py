"""What other modules may ask of reports.

The module that holds a message files a report about it (``file_report``),
in its own unit of work, after it has checked the reporter may read the
message and taken the copy from its own records. Each report route defines
its own request and response at its own boundary (DES-13) and maps them to
these.
"""

from __future__ import annotations

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.reports.domain.reports import (
    LINES_BEFORE,
    MAX_NOTE_CHARS,
    AuthorKind,
    ExcerptLine,
    FiledReport,
    NewReport,
    ReportPlace,
    ReportReason,
    clean_note,
)
from app.modules.reports.events.report_email import add_report_email_work
from app.modules.reports.infrastructure.repository import ReportRepository

#: What each reason means, for a route's own request schema to say.
REASONS_SAID = (
    "`OFFENSIVE` offensive or hateful, `HARMFUL` harmful or dangerous, "
    "`SEXUAL` sexual, `SPAM` spam or a scam, `OTHER` something else."
)


async def file_report(uow: SqlAlchemyUnitOfWork, report: NewReport) -> FiledReport:
    """Keep the report, and queue the email that tells the team -- both in
    the caller's unit of work, which commits them together."""
    filed = await ReportRepository(uow.session).file(report)
    await add_report_email_work(uow, pod_id=report.pod_id, report_id=filed.id)
    return filed


__all__ = [
    "LINES_BEFORE",
    "MAX_NOTE_CHARS",
    "REASONS_SAID",
    "AuthorKind",
    "ExcerptLine",
    "FiledReport",
    "NewReport",
    "ReportPlace",
    "ReportReason",
    "clean_note",
    "file_report",
]
