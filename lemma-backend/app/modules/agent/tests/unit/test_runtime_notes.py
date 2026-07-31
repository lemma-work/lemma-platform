from datetime import datetime, timedelta, timezone

from app.modules.agent.domain.runtime_notes import (
    append_runtime_notes,
    build_runtime_notes,
)


def test_runtime_notes_render_current_time_in_utc():
    fixed = datetime(
        2026,
        7,
        25,
        18,
        45,
        12,
        tzinfo=timezone(timedelta(hours=5, minutes=30)),
    )

    assert build_runtime_notes(now=fixed) == (
        "<notes>\n"
        "Current date and time: 2026-07-25T13:15:12Z (UTC).\n"
        "</notes>"
    )


def test_runtime_notes_are_appended_without_mutating_source_text():
    source = "USER:\nPlease summarize this."
    fixed = datetime(2026, 7, 25, 13, 15, 12, tzinfo=timezone.utc)

    rendered = append_runtime_notes(source, now=fixed)

    assert source == "USER:\nPlease summarize this."
    assert rendered.startswith(source + "\n\n<notes>\n")
    assert rendered.endswith("</notes>")
