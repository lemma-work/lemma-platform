"""Telling the team about a report, as a delivery work item.

Written in the transaction that keeps the report, keyed by the report, so the
email exists exactly when the report does, and a report replaced while its
email is still queued sends one, saying what it says by then. A ``delivery``
JOB because it sends mail: the report and the names it needs are read in a
short transaction, and the send happens after it closes. A send that fails is
raised, so the item is tried again.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel

from app.core.email.email_sender import EmailNotConfiguredError, EmailSender
from app.core.email.transactional import (
    EmailDetail,
    RenderedEmail,
    render_transactional_email,
)
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import (
    JobContext,
    WorkClass,
    WorkMode,
    WorkRegistry,
    WorkWriter,
    pod_namespace,
    registry as default_registry,
)
from app.core.log.log import get_logger
from app.modules.identity.contracts.organizations import organization_name
from app.modules.identity.contracts.profiles import user_profile
from app.modules.pod.contracts.members import pod_name
from app.modules.reports.config import reports_settings
from app.modules.reports.domain.reports import AuthorKind, ReportPlace
from app.modules.reports.infrastructure.repository import (
    ReportRepository,
    StoredReport,
)

logger = get_logger(__name__)

REPORT_EMAIL = "reports.email"


class ReportEmailPayload(BaseModel):
    report_id: UUID


@dataclass(frozen=True, slots=True)
class ReportNames:
    """What the email calls the people and places a report names."""

    reporter_name: str
    reporter_email: str | None
    lemma: str
    organization: str | None


def _what(report: StoredReport) -> str:
    """The reported message, said as the email says it."""
    author = report.author_name or "someone"
    if report.author_kind is AuthorKind.AGENT:
        return f"a reply from {author}"
    return f"a message from {author}"


def _written_by(report: StoredReport) -> str:
    author = report.author_name or "Somebody with no name"
    if report.author_kind is AuthorKind.AGENT:
        run = f", in run {report.agent_run_id}" if report.agent_run_id else ""
        return f"{author}, a Lemma's agent{run}"
    if report.author_kind is AuthorKind.CONTACT:
        known = "" if report.author_id else ", not vouched for by its platform"
        return f"{author}, a contact{known}"
    return f"{author}, a member"


def _where(report: StoredReport, names: ReportNames) -> str:
    title = report.place_title or "untitled"
    if report.place is ReportPlace.CONVERSATION:
        return f"{names.reporter_name}'s chat with {names.lemma}: {title}"
    return f"The shared chat {title}"


def render_report_email(
    report: StoredReport, names: ReportNames
) -> tuple[str, RenderedEmail]:
    """The subject and the email for one report: plain words, its copy as it
    was taken, and every id the team needs to look further."""
    what = _what(report)
    reason = report.reason.said
    where_org = f" in {names.organization}" if names.organization else ""
    lines = [
        f"{line.who} · {line.at:%d %b %H:%M} UTC"
        + (" · the reported message" if line.reported else "")
        + f": {line.text}"
        for line in report.excerpt
    ]
    details = [
        EmailDetail("Reported", f"{report.updated_at:%d %b %Y, %H:%M} UTC"),
        EmailDetail(
            "Reported by",
            f"{names.reporter_name} <{names.reporter_email}>"
            if names.reporter_email
            else names.reporter_name,
        ),
        *(
            [EmailDetail("Organization", names.organization)]
            if names.organization
            else []
        ),
        EmailDetail("Lemma", names.lemma),
        EmailDetail("Where", _where(report, names)),
        EmailDetail("Written by", _written_by(report)),
        EmailDetail("Reason", reason),
        *([EmailDetail("Their words", report.note)] if report.note else []),
        EmailDetail(
            "Ids",
            f"report {report.id} · pod {report.pod_id} · "
            + (
                f"conversation {report.conversation_id}"
                if report.conversation_id
                else f"channel {report.channel_id}"
            )
            + f" · message {report.message_id}",
        ),
    ]
    rendered = render_transactional_email(
        preheader=f"{names.reporter_name} reported {what} as {reason}.",
        eyebrow="Report",
        heading=f"{names.reporter_name} reported {what}.",
        body=(
            (
                f"{names.reporter_name} reported {what} in "
                f"{names.lemma}{where_org}, as {reason}."
            ),
            (
                "Below is a copy taken when they reported it. The chat may "
                "have changed since."
            ),
            "Reply to this email to write to them.",
        ),
        details=details,
        highlights=lines,
        footer=(
            (
                "Sent because this address receives the reports filed on "
                "this deployment (REPORTS_EMAIL)."
            ),
        ),
    )
    subject = f"Report: {reason} · {what} · {names.organization or names.lemma}"
    return subject, rendered


async def _names(uow: SqlAlchemyUnitOfWork, report: StoredReport) -> ReportNames:
    profile = await user_profile(uow.session, report.reporter_user_id)
    full = " ".join(
        part
        for part in ((profile.first_name, profile.last_name) if profile else ())
        if part
    )
    email = profile.email if profile else None
    return ReportNames(
        reporter_name=full or email or "Somebody",
        reporter_email=email,
        lemma=await pod_name(uow.session, report.pod_id) or "a Lemma",
        organization=(
            await organization_name(uow, report.organization_id)
            if report.organization_id
            else None
        ),
    )


async def run_report_email(ctx: JobContext, payload: ReportEmailPayload) -> None:
    to = reports_settings.reports_email
    if to is None:
        logger.info("reports.email_skipped", reason="no_address")
        return
    async with ctx.checkpoint() as uow:
        report = await ReportRepository(uow.session).get(payload.report_id)
        names = await _names(uow, report) if report is not None else None
    if report is None or names is None:
        logger.info("reports.email_skipped", reason="report_gone")
        return
    try:
        sender = EmailSender.from_settings()
    except EmailNotConfiguredError:
        logger.warning("reports.email.not_configured.degraded")
        return
    subject, rendered = render_report_email(report, names)
    await sender.send_email(
        str(to),
        subject,
        rendered.html,
        rendered.text,
        raise_on_failure=True,
        reply_to=names.reporter_email,
    )
    logger.info("reports.email_sent", report_id=str(report.id))


def register_report_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=REPORT_EMAIL,
        cls=WorkClass.DELIVERY,
        mode=WorkMode.JOB,
        payload=ReportEmailPayload,
    )(run_report_email)


register_report_work(default_registry)


async def add_report_email_work(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, report_id: UUID
) -> None:
    """Queue the email, when this deployment sends reports anywhere."""
    if reports_settings.reports_email is None:
        return
    await WorkWriter.of(uow).add(
        REPORT_EMAIL,
        pod_namespace(pod_id),
        ReportEmailPayload(report_id=report_id),
        dedup_key=f"content-report:{report_id}",
    )
