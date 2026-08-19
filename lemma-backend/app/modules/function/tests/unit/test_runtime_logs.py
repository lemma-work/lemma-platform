"""``terminal_logs`` decides what a finished sandbox run's stdout/stderr becomes
in storage. Redaction + truncation are already covered elsewhere (see
``test_function_dispatcher.py::test_terminal_logs_redacts_a_secret_that_straddles_the_size_limit``);
these lock down the section-assembly branches: stderr with no stdout, the
truncation marker, and the "nothing to keep" case.
"""

from __future__ import annotations

from app.modules.function.application.runtime_logs import terminal_logs
from app.modules.function.contracts.runtime import RuntimeTerminalRequest


def _request(
    *, stdout: str = "", stderr: str = "", output_truncated: bool = False
) -> RuntimeTerminalRequest:
    return RuntimeTerminalRequest(
        status="completed",
        output_data={},
        stdout=stdout,
        stderr=stderr,
        output_truncated=output_truncated,
    )


def test_terminal_logs_keeps_stderr_when_stdout_is_empty() -> None:
    """A function that never printed to stdout but failed loudly on stderr
    must not lose that output because the stdout section is empty."""
    logs = terminal_logs(_request(stdout="", stderr="Traceback: boom"))

    assert logs is not None
    assert "Traceback: boom" in logs


def test_terminal_logs_appends_truncation_marker_when_output_was_truncated() -> None:
    logs = terminal_logs(
        _request(stdout="partial output", output_truncated=True)
    )

    assert logs is not None
    assert "partial output" in logs
    assert "[function output truncated]" in logs


def test_terminal_logs_omits_truncation_marker_when_not_truncated() -> None:
    logs = terminal_logs(_request(stdout="complete output", output_truncated=False))

    assert logs is not None
    assert "[function output truncated]" not in logs


def test_terminal_logs_returns_none_when_stdout_and_stderr_are_both_empty() -> None:
    """No stdout, no stderr, and no truncation flag: there is nothing to keep,
    so the run's ``logs`` column should stay ``None`` rather than store an
    empty string."""
    assert terminal_logs(_request()) is None
