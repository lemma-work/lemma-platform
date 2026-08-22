from typing import List
import re

from app.modules.agent.tools.workspace_entities import (
    PythonExecutionResult,
)

CHARACTER_LIMIT_STDOUT = 30000
CHARACTER_LIMIT_STDERR = 10000
CHARACTER_LIMIT_OUTPUT = 10000
CHARACTER_LIMIT_DATASTORE_RESULT = 50000  # Approximately 10k tokens (1 token ≈ 4 chars)

# CSI (colours, cursor moves), OSC (window title), and single-character escapes.
# A PTY emits these constantly; to a model they are noise that costs tokens and
# obscures the text it actually needs to read.
_CSI_SEQUENCE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_OSC_SEQUENCE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_OTHER_ESCAPE = re.compile(r"\x1b[@-Z\\-_]")
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def normalize_terminal_output(text: str) -> str:
    """Render raw PTY output as the plain text a reader would see.

    Interactive programs redraw: progress bars overwrite themselves with
    carriage returns, and escape sequences move the cursor and set colours.
    Passed through verbatim, a single `npm install` can spend thousands of
    tokens re-drawing one line. Collapsing each line to its final state keeps
    what the user would actually have on screen.
    """

    if not text:
        return text
    cleaned = _OSC_SEQUENCE.sub("", text)
    cleaned = _CSI_SEQUENCE.sub("", cleaned)
    cleaned = _OTHER_ESCAPE.sub("", cleaned)
    lines: List[str] = []
    for line in cleaned.split("\n"):
        # Within a line, a carriage return means "redraw from the start", so
        # only the last segment survives - that is the final rendered state.
        segments = line.split("\r")
        rendered = next(
            (segment for segment in reversed(segments) if segment.strip()),
            segments[-1] if segments else "",
        )
        lines.append(_CONTROL_CHARACTERS.sub("", rendered))
    return "\n".join(lines)


def tail_truncate(text: str | None, limit: int) -> str | None:
    """Keep the end of the text rather than the beginning.

    For an interactive terminal the live screen is at the end: a prompt, the
    latest error, the current progress. Head-truncating it hands the agent the
    banner and hides the part it needs to act on.
    """

    if text is None or len(text) <= limit:
        return text
    return "…[earlier output truncated]…\n" + text[-limit:]


def _redact_if_only_the_result(stream: str | None, result_value: str) -> str | None:
    """Redact a stream *only* when it is nothing but the echoed result.

    The `result` field already carries the last expression's value, so when a
    stream contains that value and nothing else it is a pure duplicate worth
    collapsing. A blind substring replace, by contrast, clobbered any `print(x)`
    whose text merely coincided with (or contained) the result — silently
    dropping a line the code genuinely emitted — so match the whole stream, not
    a substring.
    """
    replace_string = "[Result REDACTED as it is given in `result` field]"
    if stream and result_value and stream.strip() == result_value.strip():
        return replace_string
    return stream


def replace_result_if_present(result: PythonExecutionResult) -> PythonExecutionResult:
    """Collapse stdout/stderr to a marker when it only echoes the result."""
    stdout = _redact_if_only_the_result(result.stdout, result.result)
    stderr = _redact_if_only_the_result(result.stderr, result.result)
    return PythonExecutionResult(
        success=result.success,
        stdout=stdout,
        stderr=stderr,
        result=result.result,
        error_in_exec=result.error_in_exec,
        execution_count=result.execution_count,
        data=result.data,
    )


def trim_python_result(result: PythonExecutionResult) -> PythonExecutionResult:
    """Trim the Python execution result to remove unnecessary details and limit size"""
    # Create a new result with only the necessary fields
    result = replace_result_if_present(result)
    return PythonExecutionResult(
        success=result.success,
        stdout=result.stdout[:CHARACTER_LIMIT_STDOUT] if result.stdout else None,
        stderr=result.stderr[:CHARACTER_LIMIT_STDERR] if result.stderr else None,
        result=result.result[:CHARACTER_LIMIT_OUTPUT] if result.result else None,
        error_in_exec=result.error_in_exec,  # Keep error_in_exec as is
        execution_count=result.execution_count,
        data=result.data,  # Keep data as is (rich outputs)
    )
