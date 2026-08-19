"""The last-expression value is echoed into `result`; redact its duplicate in
stdout only when stdout is *nothing but* that value.

A blind substring replace used to clobber any ``print(x)`` whose text merely
coincided with (or contained) the result — dropping a line the code genuinely
emitted and reading, to the agent, like an error.
"""

from __future__ import annotations

from app.modules.agent.tools.workspace_cli.helper import replace_result_if_present
from app.modules.workspace.contracts.execution import PythonExecutionResult

_MARKER = "[Result REDACTED as it is given in `result` field]"


def _result(**kwargs) -> PythonExecutionResult:
    return PythonExecutionResult(success=True, **kwargs)


def test_stdout_that_is_only_the_echoed_result_is_collapsed():
    trimmed = replace_result_if_present(_result(stdout="42\n", result="42"))
    assert trimmed.stdout == _MARKER


def test_a_printed_line_that_merely_contains_the_result_is_kept():
    # print("the answer is 42") then a bare 42 — the printed line is real output
    # and must survive; only the standalone echo would be a duplicate.
    trimmed = replace_result_if_present(
        _result(stdout="the answer is 42\n", result="42")
    )
    assert trimmed.stdout == "the answer is 42\n"
    assert _MARKER not in trimmed.stdout


def test_unrelated_stdout_is_left_untouched():
    trimmed = replace_result_if_present(
        _result(stdout="hello world\n", result="42")
    )
    assert trimmed.stdout == "hello world\n"


def test_no_result_leaves_stdout_alone():
    trimmed = replace_result_if_present(_result(stdout="42\n", result=None))
    assert trimmed.stdout == "42\n"
