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

from app.core.log.log import get_logger

logger = get_logger(__name__)

#: Beyond this, a reported count is not a large request -- it is a broken one.
#: The largest context window any model currently offers is a few million
#: tokens, so an order of magnitude above that cannot be real.
_IMPLAUSIBLE_TOKEN_COUNT = 20_000_000


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
    totals = {
        field: _usage_value(usage, field) + carried.get(field, 0)
        for field in _USAGE_FIELDS
    }
    _warn_if_implausible(totals)
    return totals


def _warn_if_implausible(totals: dict[str, int]) -> None:
    """Say so when a reported count cannot be true, in either direction.

    Observed in production: one Fireworks model reported 656 million prompt
    tokens for a single request -- escalating request over request within one
    trace, while its `cache_read` figure stayed sane. These numbers are recorded
    verbatim and feed usage accounting, so a silent one is a billing figure
    nobody can explain later.

    Reported rather than clamped. Clamping would invent a number, and the honest
    answer is that the provider's is unusable.
    """
    for field in ("input_tokens", "output_tokens"):
        value = totals.get(field, 0)
        if value > _IMPLAUSIBLE_TOKEN_COUNT:
            logger.warning(
                "agent.usage.implausible_provider_count.degraded",
                usage_field=field,
                reported_value=value,
            )
    # The same failure wearing the other face. A request that reached a provider
    # carried a prompt, so no input tokens against a non-zero request count
    # cannot be true either -- and it is the more dangerous of the two, because
    # a total of zero is not recorded at all rather than recorded wrong: the run
    # then looks exactly like one that never happened. Streaming makes it
    # reachable. The accumulation this harness selects replaces the running
    # total with each chunk's usage, and a chunk carrying none maps to zeros, so
    # any chunk after the last usage-bearing one would erase the figure.
    if totals.get("requests", 0) > 0 and totals.get("input_tokens", 0) <= 0:
        logger.warning(
            "agent.usage.missing_provider_count.degraded",
            usage_field="input_tokens",
            request_count=totals["requests"],
        )
