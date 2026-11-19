"""What a failed app build tells the person who imported it.

A sandbox command answers with what it printed on stdout and stderr and how it
ended. A build that fails is reported as one line saying why, on the step's
error, and the end of the output, on the step itself -- the import status
returns the plan with every step, so that is where someone reading why an
import failed looks. The step event carries only the error and stays small.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Iterator, Mapping

from app.modules.pod_bundle.domain.errors import AppBuildFailedError
from app.modules.pod_bundle.domain.state import PlanStep

#: Where a failed build's output is kept on its plan step.
BUILD_LOG_DETAIL = "build_log"
_LOG_LIMIT = 4000
_FAILURE_LINE_LIMIT = 300
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_ERROR_HINT = re.compile(r"\berror\b|\bERR_|\bfailed\b|\bcannot\b", re.IGNORECASE)


def _stream(result: Mapping[str, object], name: str) -> str:
    value = result.get(name)
    return _ANSI_ESCAPE.sub("", value).strip() if isinstance(value, str) else ""


def output_tail(result: Mapping[str, object], limit: int = _LOG_LIMIT) -> str:
    """The end of each stream the command wrote, labelled, ``limit`` in all.

    Both streams, because neither alone reliably holds the cause: pnpm writes
    its own errors to stdout and Vite writes its to stderr, so a tail of
    whichever stream was non-empty showed warnings and lost the error. A short
    stream leaves its share of ``limit`` to the longer one.
    """
    streams = [
        (name, text)
        for name in ("stdout", "stderr", "error")
        if (text := _stream(result, name))
    ]
    budget = limit - sum(len(f"[{name}]\n") + 1 for name, _ in streams)
    kept: dict[str, str] = {}
    for position, (name, text) in enumerate(
        sorted(streams, key=lambda stream: len(stream[1]))
    ):
        share = max(budget // (len(streams) - position), 0)
        kept[name] = text[-share:] if share else ""
        budget -= len(kept[name])
    return "\n".join(f"[{name}]\n{kept[name]}" for name, _ in streams)


def failure_line(result: Mapping[str, object]) -> str | None:
    """The one line most likely to say why the command failed.

    The last line naming an error, stderr before stdout; failing that, the last
    line at all. Skipped: stack frames, which name ``error`` functions and say
    nothing, and pnpm's "Command failed with exit code 1", which it prints
    after every failed script.
    """
    candidates: list[str] = []
    for name in ("stderr", "stdout"):
        lines = [
            line.strip()
            for line in _stream(result, name).splitlines()
            if line.strip()
            and not line.strip().startswith("at ")
            and "ELIFECYCLE" not in line
        ]
        hinted = [line for line in lines if _ERROR_HINT.search(line)]
        if hinted:
            return hinted[-1][:_FAILURE_LINE_LIMIT]
        candidates.extend(lines[-1:])
    if candidates:
        return candidates[0][:_FAILURE_LINE_LIMIT]
    return _stream(result, "error")[:_FAILURE_LINE_LIMIT] or None


def build_failed(what: str, result: Mapping[str, object]) -> AppBuildFailedError:
    """``what``, with the line that says why when the output has one. The tail
    rides along as ``details["log"]`` for the step to keep."""
    line = failure_line(result)
    return AppBuildFailedError(
        f"{what}: {line}" if line else f"{what}.",
        details={"log": output_tail(result)},
    )


def build_log_of(exc: AppBuildFailedError) -> str | None:
    """The build output a failure carries, if any."""
    details = exc.details
    if isinstance(details, Mapping):
        log = details.get("log")
        if isinstance(log, str) and log:
            return log
    return None


@contextlib.contextmanager
def build_log_kept_on(step: PlanStep) -> Iterator[None]:
    """Keep the output of a build that fails inside the block on ``step``.

    Cleared first because a re-apply resets a failed step to PENDING but leaves
    its detail, and the last attempt's log would outlive this one's success.
    """
    step.detail = {
        key: value for key, value in step.detail.items() if key != BUILD_LOG_DETAIL
    }
    try:
        yield
    except AppBuildFailedError as exc:
        if (log := build_log_of(exc)) is not None:
            step.detail = {**step.detail, BUILD_LOG_DETAIL: log}
        raise
