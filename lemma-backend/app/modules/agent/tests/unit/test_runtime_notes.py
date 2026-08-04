from datetime import datetime, timedelta, timezone

from app.modules.agent.domain.runtime_notes import (
    build_runtime_notes,
    prepend_runtime_notes,
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


def test_runtime_notes_lead_the_prompt_without_mutating_source_text():
    """The notes go ahead of the user's text, so the user's turn stays last."""
    source = "USER:\nPlease summarize this."
    fixed = datetime(2026, 7, 25, 13, 15, 12, tzinfo=timezone.utc)

    rendered = prepend_runtime_notes(source, now=fixed)

    assert source == "USER:\nPlease summarize this."
    assert rendered.startswith("<notes>\n")
    assert rendered.endswith("</notes>\n\n" + source)
