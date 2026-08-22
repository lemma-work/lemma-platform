"""Counting the tokens a run burned, including the attempts it threw away.

A mid-stream drop is retried by re-driving the run from a snapshot, and the
provider still bills for the abandoned attempt. So usage carries forward across
attempts rather than being read off the final run -- otherwise every retry would
under-report, and the one case where cost matters most is the one where the run
went wrong.

Read defensively: the shape of `usage` is pydantic-ai's, and a field that is not
there counts as zero rather than raising, because a billing read must never be
what fails a run.
"""

from __future__ import annotations


def _usage_value(usage: object, field: str) -> int:
    value = getattr(usage, field, 0)
    if value is None:
        return 0
    try:
        return int(value)
    except TypeError, ValueError:
        return 0


_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "requests",
    "tool_calls",
    "cache_write_tokens",
    "cache_read_tokens",
    "input_audio_tokens",
    "output_audio_tokens",
)


def accumulate_usage(carried: dict[str, int], usage: object) -> None:
    """Fold an abandoned attempt's usage into the running total."""
    if usage is None:
        return
    for field in _USAGE_FIELDS:
        carried[field] = carried.get(field, 0) + _usage_value(usage, field)


def usage_totals(usage: object, carried: dict[str, int]) -> dict[str, int]:
    """This attempt's usage plus everything earlier attempts already spent."""
    return {
        field: _usage_value(usage, field) + carried.get(field, 0)
        for field in _USAGE_FIELDS
    }
