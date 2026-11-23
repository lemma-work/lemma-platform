"""Keeping reports, and reading one back for the email."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.reports.domain.reports import (
    AuthorKind,
    ExcerptLine,
    FiledReport,
    NewReport,
    ReportPlace,
    ReportReason,
    ReportStatus,
)
from app.modules.reports.infrastructure.models import ContentReportModel


class StoredReport(NewReport):
    """A report as the table holds it."""

    id: UUID
    created_at: datetime
    updated_at: datetime
    status: ReportStatus


class ReportRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def file(self, report: NewReport) -> FiledReport:
        """Keep the report. The same person reporting the same message again
        replaces their report -- its reason, words and copy -- and opens it
        again if the team had closed it; it keeps its id and first date."""
        at = datetime.now(timezone.utc)
        values = {
            "organization_id": report.organization_id,
            "pod_id": report.pod_id,
            "reporter_user_id": report.reporter_user_id,
            "place": report.place.value,
            "conversation_id": report.conversation_id,
            "channel_id": report.channel_id,
            "message_id": report.message_id,
            "place_title": report.place_title[:255] if report.place_title else None,
            "author_kind": report.author_kind.value,
            "author_id": report.author_id,
            "author_name": report.author_name[:255] if report.author_name else None,
            "agent_run_id": report.agent_run_id,
            "reason": report.reason.value,
            "note": report.note,
            "excerpt": [line.model_dump(mode="json") for line in report.excerpt],
            "status": ReportStatus.OPEN.value,
            "updated_at": at,
        }
        statement = insert(ContentReportModel).values(**values)
        replaced = {
            name: statement.excluded[name]
            for name in (
                "place_title",
                "author_name",
                "reason",
                "note",
                "excerpt",
                "updated_at",
            )
        }
        row = (
            await self._session.execute(
                statement.on_conflict_do_update(
                    constraint="uq_content_reports_reporter_message",
                    set_={
                        **replaced,
                        "status": ReportStatus.OPEN.value,
                        "closed_at": None,
                        "outcome": None,
                    },
                ).returning(ContentReportModel.id, ContentReportModel.created_at)
            )
        ).one()
        return FiledReport(id=row[0], created_at=row[1])

    async def get(self, report_id: UUID) -> StoredReport | None:
        row = await self._session.scalar(
            select(ContentReportModel).where(ContentReportModel.id == report_id)
        )
        if row is None:
            return None
        return StoredReport(
            id=row.id,
            created_at=row.created_at,
            updated_at=row.updated_at,
            status=ReportStatus(row.status),
            organization_id=row.organization_id,
            pod_id=row.pod_id,
            reporter_user_id=row.reporter_user_id,
            place=ReportPlace(row.place),
            conversation_id=row.conversation_id,
            channel_id=row.channel_id,
            message_id=row.message_id,
            place_title=row.place_title,
            author_kind=AuthorKind(row.author_kind),
            author_id=row.author_id,
            author_name=row.author_name,
            agent_run_id=row.agent_run_id,
            reason=ReportReason(row.reason),
            note=row.note,
            excerpt=tuple(ExcerptLine.model_validate(line) for line in row.excerpt),
        )
