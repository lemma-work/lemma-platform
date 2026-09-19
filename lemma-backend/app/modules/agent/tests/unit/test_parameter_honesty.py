"""A parameter the model can set is a parameter the server honours, or says it did not.

Each of these was a silent discard: the schema offered a knob, the model turned
it, and nothing used the value or said so. That is worse than not offering the
knob, because the model has no way to learn — it sees a result consistent with
its request and draws the wrong conclusion. The case that bit was a poll asking
to wait minutes, silently given its own much shorter deadline, and coming back
empty: a run of such polls reads as "still quiet" rather than "you cannot wait
that long here".
"""

from __future__ import annotations

import pytest

from app.modules.agent.tools.workspace_cli.helper import (
    CHARACTER_LIMIT_STDERR,
    CHARACTER_LIMIT_STDOUT,
    render_terminal_result,
)
from app.modules.agent.tools.workspace_cli.models import (
    ExecCommandResult,
    ExecutePythonRequest,
)
from app.modules.workspace.sandbox_session import _clamped_yield_ms

pytestmark = pytest.mark.unit


def test_a_wait_longer_than_one_poll_can_give_says_so():
    """The silent clamp that made an empty poll unreadable."""
    effective_ms, notice = _clamped_yield_ms(240_000)

    assert effective_ms < 240_000
    assert notice is not None
    # It must say what an empty result means, or the clamp is still unreadable.
    assert "no new output" in notice
    assert "wait_for" in notice


def test_a_wait_within_the_budget_is_left_alone_and_says_nothing():
    assert _clamped_yield_ms(5_000) == (5_000, None)


def test_a_caller_asking_for_less_output_gets_less():
    """`max_output_tokens` reached `write_stdin` and was deleted on arrival."""
    result = {"stdout": "x" * 5_000, "stderr": "y" * 5_000}

    stdout, stderr = render_terminal_result(result, tty=False, max_output_tokens=100)

    assert stdout is not None and len(stdout) < 1_000
    assert stderr is not None and len(stderr) < 1_000


def test_the_standing_limits_are_a_ceiling_a_caller_cannot_raise():
    """One `npm ci` landing whole in a conversation is replayed on every later
    turn of it, so a caller may ask for less and never for more."""
    result = {"stdout": "x" * 200_000, "stderr": "y" * 200_000}

    stdout, stderr = render_terminal_result(
        result, tty=False, max_output_tokens=10_000_000
    )

    assert stdout is not None and len(stdout) <= CHARACTER_LIMIT_STDOUT + 100
    assert stderr is not None and len(stderr) <= CHARACTER_LIMIT_STDERR + 100


def test_no_opinion_means_the_standing_limits():
    result = {"stdout": "x" * 200_000}

    stdout, _ = render_terminal_result(result, tty=False)

    assert stdout is not None and len(stdout) <= CHARACTER_LIMIT_STDOUT + 100


def test_python_execution_cannot_ask_for_an_unbounded_wait():
    """It was the one unbounded number on this model, and unlike `exec_command`
    there is no handle to come back to — a timeout ends the work."""
    with pytest.raises(ValueError):
        ExecutePythonRequest(code="1", timeout_seconds=100_000)

    assert ExecutePythonRequest(code="1", timeout_seconds=300).timeout_seconds == 300


def test_a_result_can_carry_a_notice_about_the_call_itself():
    """Separate from `error`: nothing failed, and calling it an error would
    have the model try to repair something that worked."""
    result = ExecCommandResult(success=True, notice="waited less than asked")

    assert result.success is True
    assert result.error is None
    assert result.notice == "waited less than asked"


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        # An interactive session used to have its stated patience overwritten.
        ({"cmd": "x", "tty": True, "timeout_seconds": 300}, (300, None)),
        # A stated patience used to throw away a stated yield.
        (
            {"cmd": "x", "timeout_seconds": 120, "yield_time_ms": 5_000},
            (120, 5_000),
        ),
        # A stated patience alone still means "wait it out".
        ({"cmd": "x", "timeout_seconds": 120}, (120, None)),
        # Neither stated: the defaults, unchanged.
        ({"cmd": "x"}, (60, 30_000)),
    ],
)
def test_the_two_clocks_are_independent(kwargs, expected):
    """Patience and early-return are different questions, and were conflated."""
    from app.modules.agent.tools.workspace_cli.models import ExecCommandRequest
    from app.modules.agent.tools.workspace_cli.workspace_cli import exec_clocks

    assert exec_clocks(ExecCommandRequest(**kwargs)) == expected


def test_every_render_call_passes_the_cap_the_caller_asked_for():
    """The renderer honouring `max_output_tokens` is only half of it: a call
    site that forgets to pass it leaves the parameter documented and dead.

    That is what happened. `write_stdin` forwarded it and `exec_command` did
    not, in the same file, so the tool an agent reaches for most was the one
    whose cap did nothing. Checked structurally rather than by driving a
    sandbox, because the defect is the *absence* of an argument and the
    cheapest thing that notices an absence is reading the call.
    """
    import ast
    from pathlib import Path

    source = Path("app/modules/agent/tools/workspace_cli/workspace_cli.py")
    tree = ast.parse(source.read_text())
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "render_terminal_result"
    ]

    assert calls, "the renderer is no longer called here; retarget this test"
    for call in calls:
        passed = {keyword.arg for keyword in call.keywords}
        assert "max_output_tokens" in passed, (
            f"render_terminal_result at line {call.lineno} drops the caller's "
            "cap, so the parameter is accepted and ignored"
        )
