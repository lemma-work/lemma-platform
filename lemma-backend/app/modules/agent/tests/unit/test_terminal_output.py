"""Rendering raw PTY output into something a model can actually read."""

from __future__ import annotations

from app.modules.agent.tools.workspace_cli.helper import (
    normalize_terminal_output,
    tail_truncate,
)


def test_progress_redraws_collapse_to_their_final_state():
    """A progress bar redraws one line; only the last state is real output.

    Passed through verbatim, one `npm install` spends thousands of tokens
    re-drawing the same line.
    """

    raw = "Installing  0%\rInstalling 50%\rInstalling 100%\nDone\n"

    assert normalize_terminal_output(raw) == "Installing 100%\nDone\n"


def test_colour_and_cursor_sequences_are_stripped():
    raw = "\x1b[32m\x1b[1mPASS\x1b[0m \x1b[2Ksrc/app.test.ts\n"

    assert normalize_terminal_output(raw) == "PASS src/app.test.ts\n"


def test_window_title_and_bare_escapes_are_stripped():
    raw = "\x1b]0;my-shell\x07ready\n"

    assert normalize_terminal_output(raw) == "ready\n"


def test_a_line_that_is_only_a_redraw_keeps_its_last_nonblank_state():
    # A trailing carriage return must not blank out the line it just drew.
    assert normalize_terminal_output("working...\r") == "working..."


def test_plain_output_is_left_alone():
    raw = "hello\nworld\n"

    assert normalize_terminal_output(raw) == raw


def test_truncation_keeps_the_end_where_the_live_screen_is():
    """The prompt, the latest error, and current progress are all at the end."""

    text = "banner\n" + "x" * 100 + "\nthe-part-that-matters"

    truncated = tail_truncate(text, 30)

    assert truncated is not None
    assert truncated.endswith("the-part-that-matters")
    assert "banner" not in truncated
    assert "truncated" in truncated


def test_short_output_is_not_truncated():
    assert tail_truncate("short", 100) == "short"
    assert tail_truncate(None, 100) is None
