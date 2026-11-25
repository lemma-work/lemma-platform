"""Every report is queued for the team, and the job says what became of it."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, Mock
from uuid import uuid4

import pytest

from app.modules.agent.tests.unit.work_recording import RecordingWorkSession
from app.modules.reports.config import reports_settings
from app.modules.reports.events.report_email import (
    REPORT_EMAIL,
    ReportEmailPayload,
    add_report_email_work,
    run_report_email,
)

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_with_no_address_a_report_is_still_queued_and_said_in_the_log(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A deployment that never set ``REPORTS_EMAIL`` hears of each report
    from its log, one line per report; nothing is read, nothing is sent."""
    monkeypatch.setattr(reports_settings, "reports_email", None)
    report_id = uuid4()
    database = RecordingWorkSession()

    await add_report_email_work(
        Mock(session=database), pod_id=uuid4(), report_id=report_id
    )

    queued = database.item(REPORT_EMAIL)
    assert queued["dedup_key"] == f"content-report:{report_id}"
    payload = ReportEmailPayload.model_validate(json.loads(queued["payload"]))
    assert payload.report_id == report_id

    ctx = MagicMock()
    with caplog.at_level("INFO"):
        await run_report_email(ctx, payload)

    ctx.checkpoint.assert_not_called()
    said = [
        record.getMessage()
        for record in caplog.records
        if "reports.email_skipped" in record.getMessage()
    ]
    assert len(said) == 1
    assert "no_address" in said[0] and str(report_id) in said[0]
