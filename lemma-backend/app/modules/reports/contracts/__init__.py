"""What other modules may ask of reports.

The module that holds a message files a report about it (``file_report``),
in its own unit of work, after it has checked the reporter may read the
message and taken the copy from its own records. The request and response a
report route takes are here too, so every place a person can report from
asks the same thing.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

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


class ReportRequest(BaseModel):
    """Why the reader reports this message, and anything they want to add."""

    reason: ReportReason = Field(
        description=(
            "`OFFENSIVE` offensive or hateful, `HARMFUL` harmful or dangerous, "
            "`SEXUAL` sexual, `SPAM` spam or a scam, `OTHER` something else."
        )
    )
    note: str | None = Field(
        default=None,
        max_length=MAX_NOTE_CHARS,
        description="The reader's own words, for the team. Optional.",
    )


class ReportResponse(BaseModel):
    """The report as kept. Reporting the same message again replaces it: the
    same id comes back."""

    id: UUID
    created_at: datetime


async def file_report(uow: SqlAlchemyUnitOfWork, report: NewReport) -> FiledReport:
    """Keep the report, and queue the email that tells the team -- both in
    the caller's unit of work, which commits them together."""
    filed = await ReportRepository(uow.session).file(report)
    await add_report_email_work(uow, pod_id=report.pod_id, report_id=filed.id)
    return filed


__all__ = [
    "LINES_BEFORE",
    "AuthorKind",
    "ExcerptLine",
    "FiledReport",
    "NewReport",
    "ReportPlace",
    "ReportReason",
    "ReportRequest",
    "ReportResponse",
    "clean_note",
    "file_report",
]
